# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the MI1 MIDI Synth bank by sampling the MT-32 (Munt) per gs/mt32bank.toml.

Melodic slot: OscConfig 3 = osc A plays a short attack sample once, then osc B loops one
sustain cycle (256 B, single-cycle mode). The attack is "flattened" (divided by its own
loudness contour) and MIDI Synth's envelope is fitted to the MT-32's contour instead, so the
attack -> loop handover is level. Drum zone: one-shot sample. Tuning uses the measured model
in doc_tuning.py. Writes <out>/MI1.BNK, MI1.WAV and MI1.map.toml (program/drum -> slot/key).
"""

from __future__ import annotations

import argparse
import hashlib
import math
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import mido
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample

from . import doc_tuning as dt
from .midisynth import Bank, Instrument, WaveBank, envelope, wavelist

SR = 32000                      # Munt output rate
MELODIC_CH, RHYTHM_CH = 1, 9    # MT-32 part 1 = MIDI channel 2; rhythm = 10
LOOP_BYTES = 256
FIRST_PAGE = 2                  # pages $00-$01: synthLAB metronome woodblock
MIN_DRUM_RATE = 6000


# ---------------------------------------------------------------------------
# Rendering through Munt

def _reverb_off() -> mido.Message:
    # MT-32 system area 10 00 03 = reverb level 0; Roland checksum
    addr_data = [0x10, 0x00, 0x03, 0x00]
    cs = (128 - sum(addr_data) % 128) % 128
    return mido.Message("sysex", data=[0x41, 0x10, 0x16, 0x12, *addr_data, cs])


def render(smf2wav: Path, roms: Path, cache: Path, *, program: int | None, key: int,
           hold: float, velocity: int = 100) -> np.ndarray:
    """Render one note (melodic `program` on part 1, or rhythm `key` if program is None)."""
    tag = f"{'p%03d' % program if program is not None else 'rhythm'}-k{key}-h{hold}-v{velocity}"
    wav = cache / f"{tag}.wav"
    if not wav.exists():
        cache.mkdir(parents=True, exist_ok=True)
        tpq, s = 480, 960                               # 120 BPM: 960 ticks per second
        ch = RHYTHM_CH if program is None else MELODIC_CH
        trk = mido.MidiTrack([_reverb_off(),
                              mido.Message("control_change", channel=ch, control=7, value=100, time=0),
                              mido.Message("control_change", channel=ch, control=10, value=64, time=0)])
        if program is not None:
            trk.append(mido.Message("program_change", channel=ch, program=program, time=0))
        trk.append(mido.Message("note_on", channel=ch, note=key, velocity=velocity, time=int(0.5 * s)))
        trk.append(mido.Message("note_off", channel=ch, note=key, velocity=64, time=int(hold * s)))
        trk.append(mido.MetaMessage("end_of_track", time=int(2.0 * s)))
        mf = mido.MidiFile(ticks_per_beat=tpq)
        mf.tracks.append(trk)
        mid = cache / f"{tag}.mid"
        mf.save(mid)
        subprocess.run([str(smf2wav), "--quiet", "-m", str(roms), "-f", "-o", str(wav), str(mid)],
                       check=True, stdout=subprocess.DEVNULL)
    rate, data = wavfile.read(wav)
    if rate != SR:
        raise ValueError(f"{wav}: expected {SR} Hz, got {rate}")
    x = data.astype(np.float64)
    return (x.mean(axis=1) if x.ndim == 2 else x) / 32768.0


# ---------------------------------------------------------------------------
# Analysis

def rms_envelope(x: np.ndarray, win: float = 0.01) -> np.ndarray:
    n = max(1, int(win * SR))
    return np.sqrt(np.convolve(x * x, np.ones(n) / n, mode="same"))


def onset(x: np.ndarray, frac: float = 0.02) -> int:
    peak = np.max(np.abs(x))
    return int(np.argmax(np.abs(x) > frac * peak))


def refine_period(x: np.ndarray, at: int, f0: float) -> float:
    """Period (samples) near SR/f0 by autocorrelation around sample `at`, with parabolic peak."""
    p0 = SR / f0
    n = int(p0 * 8)
    seg = x[at : at + n + int(p0 * 1.1) + 2]
    lo, hi = int(p0 * 0.97), int(p0 * 1.03) + 1
    corr = [float(np.dot(seg[:n], seg[lag : lag + n])) for lag in range(lo, hi + 1)]
    i = int(np.argmax(corr))
    if i in (0, len(corr) - 1):
        return p0                       # no clear period near the nominal pitch (inharmonic)
    if 0 < i < len(corr) - 1:
        a, b, c = corr[i - 1], corr[i], corr[i + 1]
        den = a - 2 * b + c
        return lo + i + (0.5 * (a - c) / den if den else 0.0)
    return float(lo + i)


def rising_zero(x: np.ndarray, near: int, span: int) -> int:
    for i in range(near, min(len(x) - 1, near + span)):
        if x[i] <= 0 < x[i + 1]:
            return i + 1
    return near


def one_cycle(x: np.ndarray, start: int, period: float, cycles: int = 4, out: int = LOOP_BYTES) -> np.ndarray:
    """Average `cycles` consecutive periods, band-limited to `out` points (periodic)."""
    fine = 4096
    acc = np.zeros(fine)
    for c in range(cycles):
        pos = start + period * (c + np.arange(fine) / fine)
        acc += np.interp(pos, np.arange(len(x)), x)
    return resample(acc / cycles, out)


def to_doc(y: np.ndarray, peak: float | None = None) -> bytes:
    """Signed float -> DOC unsigned 8-bit; 0 is never emitted (it halts the oscillator)."""
    peak = peak or (float(np.max(np.abs(y))) or 1.0)
    return bytes(np.clip(np.round(128 + 126 * y / peak), 1, 255).astype(np.uint8))


def fit_envelope(env: np.ndarray, start: int, off: int, release_slow: bool = False) -> bytes:
    """MIDI Synth envelope approximating a loudness contour (levels linear 0-127)."""
    top = float(np.max(env[start:off])) or 1.0
    lv = lambda i: int(round(127 * float(env[min(max(i, 0), len(env) - 1)]) / top))
    pk = start + int(np.argmax(env[start:off]))
    t = lambda a, b: (b - a) / SR
    d1, d2 = min(pk + int(0.15 * SR), off), min(pk + int(0.6 * SR), off)
    sus_at = max(off - int(0.05 * SR), d2)
    l1, l2, ls = lv(d1), lv(d2), lv(sus_at)
    level_off = max(lv(off), 1)
    below = np.nonzero(env[off:] < 0.1 * env[off] + 1e-9)[0]
    rel = (below[0] if len(below) else int(0.3 * SR)) / SR
    return envelope(
        attack=(127, dt.segment_rate(0, 127, t(start, pk))),
        decay1=(l1, dt.segment_rate(127, l1, t(pk, d1))),
        decay2=(l2, dt.segment_rate(l1, l2, t(d1, d2))),
        sustain=ls, decay3_rate=dt.segment_rate(l2, ls, t(d2, sus_at)),
        release1=(0, dt.segment_rate(level_off, 0, rel * (2 if release_slow else 1))),
        release2=(0, 31), release3_rate=31, velocity_gain=4)


def pitch_offset(ref: np.ndarray, other: np.ndarray) -> int:
    """Semitone shift of `other` relative to `ref` (log-frequency spectrum cross-correlation)."""
    def logspec(x):
        a = onset(x)
        seg = x[a : a + int(0.06 * SR)] * np.hanning(int(0.06 * SR))[: len(x[a : a + int(0.06 * SR)])]
        mag = np.abs(np.fft.rfft(seg, 8192))
        freqs = np.fft.rfftfreq(8192, 1 / SR)
        grid = 60 * 2 ** (np.arange(0, 7 * 12 * 4) / 48)            # 60 Hz .. ~7.7 kHz, 1/4 semis
        return np.log1p(np.interp(grid, freqs, mag))
    a, b = logspec(ref), logspec(other)
    a, b = a - a.mean(), b - b.mean()
    best = max(range(-48, 49), key=lambda s: float(np.dot(a[max(0, s):len(a) + min(0, s)],
                                                          b[max(0, -s):len(b) - max(0, s)])))
    return -round(best / 4)            # a[i+s] ~ b[i]: s > 0 means `other` is lower


# ---------------------------------------------------------------------------
# Building

@dataclass
class Wave:
    name: str
    data: bytes

    @property
    def size_code(self) -> int:
        return int(math.log2(len(self.data) // 256))


def allocate(waves: list[Wave], first: int, last: int) -> dict[str, int]:
    """Largest-first, size-aligned placement in DOC pages [first, last]. Returns name -> page."""
    used = [False] * 256
    for p in range(256):
        used[p] = not first <= p <= last
    out = {}
    for w in sorted(waves, key=lambda w: -len(w.data)):
        n = len(w.data) // 256
        for p in range(0, 256, n):
            if not any(used[p : p + n]):
                for q in range(p, p + n):
                    used[q] = True
                out[w.name] = p
                break
        else:
            free = sum(not u for u in used)
            raise ValueError(f"DOC RAM full placing {w.name} ({len(w.data)} B); {free} pages free")
    return out


def build(spec: dict, smf2wav: Path, roms: Path, cache: Path, log=print):
    hold = spec.get("hold_seconds", 2.0)
    waves: list[Wave] = [Wave("Silence", bytes([0x80]) * 256)]
    slots: dict[int, dict] = {}
    loud: dict[str, float] = {}

    for m in spec["melodic"]:
        x = render(smf2wav, roms, cache, program=m["program"], key=m["key"], hold=hold)
        f0 = 440 * 2 ** ((m["key"] - 69) / 12)
        a = onset(x)
        off = int((0.5 + hold) * SR)
        env = rms_envelope(x)
        loud[m["name"]] = float(np.max(env[a:off]))
        nbytes = m.get("attack_bytes", 2048)
        rate = min(dt.DOC_RATE, max(nbytes / 0.12, 20 * f0))
        end = rising_zero(x, a + int(nbytes / rate * SR), int(SR / f0) + 2)
        loop_at = rising_zero(x, max(end, a + int(0.3 * SR)), int(SR / f0) + 2)
        period = refine_period(x, loop_at, f0)
        flat = x[a:end] / np.maximum(env[a:end], 0.1 * np.max(env[a:off]))
        attack = resample(flat, nbytes)
        cycle = one_cycle(x, loop_at, period)
        cycle_rms = float(np.sqrt(np.mean(cycle ** 2))) or 1.0
        attack_rms = float(np.sqrt(np.mean(attack[-nbytes // 4:] ** 2))) or 1.0
        peak = max(np.max(np.abs(attack)), np.max(np.abs(cycle)) * attack_rms / cycle_rms)
        waves.append(Wave(f"{m['name']} A", to_doc(attack, peak)))
        waves.append(Wave(f"~{m['name']} L", to_doc(cycle * attack_rms / cycle_rms, peak)))
        true_rate = nbytes / ((end - a) / SR)
        slots[m["slot"]] = {"kind": "melodic", "spec": m, "rate": true_rate,
                            # the loop holds one MT-32 period = f0*period/SR nominal cycles, so
                            # it plays at the MT-32's own pitch, matching the attack sample
                            "loop_cycles": f0 * period / SR,
                            "env": fit_envelope(env, a, off)}
        log(f"  {m['name']:<12} prog {m['program']:3d} key {m['key']}: attack {nbytes} B @ {true_rate:7.0f} Hz "
            f"({(end - a) / SR * 1000:.0f} ms), loop period {period:.2f} smp (MT-32 {SR / period:.2f} Hz vs {f0:.2f})")

    kits: dict[int, list[dict]] = {}
    for d in spec["drum"]:
        x = render(smf2wav, roms, cache, program=None, key=d["key"], hold=0.5)
        a = onset(x)
        env = rms_envelope(x, 0.005)
        loud[d["name"]] = float(np.max(env))
        tail = np.nonzero(env[a:] > 0.005 * np.max(env))[0]
        dur = (tail[-1] + 1) / SR if len(tail) else 0.1
        n = d["bytes"]
        rate = min(dt.DOC_RATE, max(MIN_DRUM_RATE, n / dur))
        seg = x[a : a + int(min(dur, n / rate) * SR)]
        fade = np.ones(len(seg))
        k = max(1, len(seg) // 10)
        fade[-k:] = np.linspace(1, 0, k)
        samp = resample(seg * fade, int(len(seg) / SR * rate))
        data = to_doc(samp)
        data = data + bytes([0x80]) * (n - len(data))
        waves.append(Wave(d["name"], data[:n]))
        offsets = {}
        for c in d["covers"]:
            offsets[c] = 0 if c == d["key"] else pitch_offset(
                x, render(smf2wav, roms, cache, program=None, key=c, hold=0.5))
        kits.setdefault(d["slot"], []).append({"spec": d, "rate": rate, "offsets": offsets})
        log(f"  {d['name']:<12} rhythm key {d['key']}: {n} B @ {rate:6.0f} Hz ({dur * 1000:.0f} ms), "
            f"covers {offsets}")

    reserve = spec.get("reserve_pages", 0)
    pages = allocate(waves, FIRST_PAGE, 255 - reserve)
    used = sum(len(w.data) for w in waves) // 256
    log(f"  DOC RAM: {used} pages used of {256 - FIRST_PAGE - reserve} "
        f"(+{reserve} reserved for effects, {FIRST_PAGE} metronome)")
    by_name = {w.name: w for w in waves}
    vol = lambda name: max(16, min(127, round(127 * loud[name] / max(loud.values()))))
    silence = (pages["Silence"], 0, 0, 4, 0, 0)

    insts = [Instrument("", bytes(288)) for _ in range(16)]
    programs, drums = {}, {}
    for slot, s in slots.items():
        m = s["spec"]
        aw, lw = by_name[f"{m['name']} A"], by_name[f"~{m['name']} L"]
        ta = dt.encode(dt.tuning_for(s["rate"], m["key"], aw.size_code))
        tl = dt.encode(dt.single_cycle_tuning(s["loop_cycles"]))
        v = vol(m["name"])
        wl = wavelist(127, 3, (pages[aw.name], aw.size_code, v, *ta), (pages[lw.name], 0, v, *tl))
        silent = wavelist(127, 0, silence, silence)
        insts[slot] = Instrument.build(m["name"], (s["env"], [wl]), (envelope(), [silent]))
        for p in m["covers"]:
            programs[p] = slot
    for slot, zones in kits.items():
        if len(zones) > 8:
            raise ValueError(f"kit slot {slot}: {len(zones)} zones > 8")
        wls = []
        # A wave plays in tune only for home keys where its tuning fits octave 0-6; slow and
        # small (single-cycle mode) waves need low keys. Lay zones out by that limit.
        def key_max(z):
            w = by_name[z["spec"]["name"]]
            return math.floor(69 + 12 * (math.log2(z["rate"] / dt.B0)
                                         - (w.size_code - dt.resolution(w.size_code)) + 4) - 0.5)
        cursor = 0
        ordered = sorted(zones, key=key_max)
        for j, z in enumerate(ordered):
            d = z["spec"]
            neg = max(0, -min(z["offsets"].values()))
            pos = max(0, max(z["offsets"].values()))
            home = cursor + neg
            if home > key_max(z):
                raise ValueError(f"kit slot {slot}: no key range left for {d['name']} "
                                 f"(home {home} > max {key_max(z)})")
            top = 127 if j == len(ordered) - 1 else home + pos
            cursor = top + 1
            w = by_name[d["name"]]
            t = dt.encode(dt.tuning_for(z["rate"], home, w.size_code))
            wls.append(wavelist(top, 2, (pages[d["name"]], w.size_code, vol(d["name"]), *t), silence))
            for c, off in z["offsets"].items():
                drums[c] = [slot, home + off]
        env = envelope(attack=(127, 31), sustain=127, release1=(0, 12), velocity_gain=4)
        insts[slot] = Instrument.build(f"Kit {'AB'[slot != 0]}", (env, wls),
                                       (envelope(), [wavelist(127, 0, silence, silence)] * len(wls)))
    ram = bytearray([0x80]) * 0x10000
    for w in waves:
        ram[pages[w.name] * 256 : pages[w.name] * 256 + len(w.data)] = w.data
    bank = Bank(f"{spec['bank']}.WAV", 16, insts)
    wb = WaveBank({pages[w.name]: (w.name[:15], w.size_code) for w in waves}, bytes(ram))
    return bank, wb, programs, drums


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, default=Path("gs/mt32bank.toml"))
    ap.add_argument("--smf2wav", type=Path, default=Path("vendor/munt/bin/mt32emu-smf2wav"))
    ap.add_argument("--roms", type=Path, default=Path("original files/mt-32 roms"))
    ap.add_argument("--cache", type=Path, default=Path("gs/build/mt32samples"))
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("gs/seq/mt32"))
    args = ap.parse_args(argv)
    spec = tomllib.loads(args.spec.read_text())
    print(f"sampling MT-32 per {args.spec}")
    bank, wb, programs, drums = build(spec, args.smf2wav, args.roms, args.cache)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    name = spec["bank"]
    (args.out_dir / f"{name}.BNK").write_bytes(bank.to_bytes())
    (args.out_dir / f"{name}.WAV").write_bytes(wb.to_bytes())
    lines = [f"# Generated by migs.mt32bank from {args.spec} -- do not edit",
             "[programs]   # MT-32 program -> slot"]
    lines += [f'"{p}" = {s}' for p, s in sorted(programs.items())]
    lines += ["", "[drums]      # MT-32 rhythm key -> [slot, key]"]
    lines += [f'"{k}" = {v}' for k, v in sorted(drums.items())]
    (args.out_dir / f"{name}.map.toml").write_text("\n".join(lines) + "\n")
    h = hashlib.sha256((args.out_dir / f"{name}.WAV").read_bytes()).hexdigest()[:12]
    print(f"wrote {args.out_dir}/{name}.BNK, {name}.WAV (sha {h}), {name}.map.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
