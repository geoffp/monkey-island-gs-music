# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the MI1 MIDI Synth bank by sampling the MT-32 (Munt) per config/mt32bank.toml.

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
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import resample

from . import doc_tuning as dt
from .audio import one_cycle, onset, pitch_offset, refine_period, rising_zero, rms_envelope
from .docram import SILENCE, Wave, allocate, ram_image, to_doc
from .midisynth import INSTRUMENT_SIZE, INSTRUMENTS, WAVELISTS, Bank, Instrument, WaveBank, envelope, wavelist
from .munt import SR, render

LOOP_BYTES = 256
FIRST_PAGE = 2                  # pages $00-$01: synthLAB metronome woodblock
MIN_DRUM_RATE = 6000
OSC_SWAP, OSC_ONESHOT, OSC_FREE = 3, 2, 0     # wavelist oscillator configs
# Voices free only at envelope 0, and MIDI Synth steals the least noticeable (quietest) voice.
# Slow tails from loud notes therefore outlive quieter *sustained* notes, which get stolen.
# Keep tails short:
MIN_RELEASE_RATE = 20           # release <= ~0.18 s full scale
MIN_KIT_FALL_RATE = 18          # drum kit voice ends within ~0.28 s
SUSTAIN_FLOOR = 12              # fitted sustain below this -> 0, so the voice frees itself
AUDIBLE = 0.1                   # a decaying sound is "over" below 10% of its peak (-20 dB)
MIN_VOLUME = 16                 # quietest wavelist volume (loudness is relative to the loudest)
# Generator 2 plays silence; an all-zero envelope ends at once so it never holds a voice
# (MIDI Synth frees a voice only when BOTH envelopes reach zero).
GEN2_OFF = envelope(attack=(0, 31), decay1=(0, 31), decay2=(0, 31), sustain=0, decay3_rate=31,
                    release1=(0, 31), release2=(0, 31), release3_rate=31, velocity_gain=0)

Renderer = Callable[..., np.ndarray]           # munt.render with the paths bound


def fit_envelope(env: np.ndarray, start: int, off: int, release_slow: bool = False) -> bytes:
    """MIDI Synth envelope approximating a loudness contour (levels linear 0-127): attack to
    the peak, two decays (150 ms and 600 ms after it), sustain at the level 100 ms before
    note-off, and a release fitted to the fall after note-off."""
    top = float(np.max(env[start:off])) or 1.0
    half = int(0.05 * SR)                    # average +-50 ms: tremolo/beating would mislead points
    lv = lambda i: int(round(127 * float(np.mean(env[max(i - half, 0):max(min(i + half, len(env)), 1)])) / top))
    pk = start + int(np.argmax(env[start:off]))
    t = lambda a, b: (b - a) / SR
    d1, d2 = min(pk + int(0.15 * SR), off), min(pk + int(0.6 * SR), off)
    sus_at = max(off - int(0.1 * SR), d2)
    l1, l2, ls = lv(d1), lv(d2), lv(sus_at)
    level_off = max(lv(off), 1)
    below = np.nonzero(env[off:] < 0.1 * env[off] + 1e-9)[0]
    rel = (below[0] if len(below) else int(0.3 * SR)) / SR
    if ls < SUSTAIN_FLOOR:
        # decaying sound: reach zero where it drops below audibility, freeing the voice
        quiet = np.nonzero(env[pk:off] < AUDIBLE * top)[0]
        gone = pk + (int(quiet[0]) if len(quiet) else off - pk)
        if gone <= d1:
            d1, l1, d2, l2 = gone, 0, gone, 0
        elif gone <= d2:
            d2, l2 = gone, 0
        ls, sus_at = 0, max(gone, d2)
    return envelope(
        attack=(127, dt.segment_rate(0, 127, t(start, pk))),
        decay1=(l1, dt.segment_rate(127, l1, t(pk, d1))),
        decay2=(l2, dt.segment_rate(l1, l2, t(d1, d2))),
        sustain=ls,
        decay3_rate=dt.segment_rate(l2, ls, t(d2, sus_at)),
        # cap the release at ~1 s so stopping playback (All Notes Off -> Release 1) is prompt
        release1=(0, max(MIN_RELEASE_RATE, dt.segment_rate(level_off, 0, rel * (2 if release_slow else 1)))),
        release2=(0, 31), release3_rate=31, velocity_gain=4)


# ---------------------------------------------------------------------------
# Sampling

@dataclass
class Melodic:
    spec: dict                  # its [[melodic]] entry
    attack: Wave
    loop: Wave
    rate: float                 # the attack's sample rate
    loop_cycles: float          # nominal cycles the loop holds (see sample_melodic)
    env: bytes                  # fitted EnvelopeRec
    loudness: float             # peak RMS of the render
    period: float               # MT-32 period at the loop (samples at SR)


@dataclass
class Drum:
    spec: dict                  # its [[drum]] entry
    wave: Wave
    rate: float
    offsets: dict[int, int]     # rhythm key it covers -> semitones from the sampled key
    loudness: float
    seconds: float              # length to -46 dB


def sample_melodic(m: dict, render_note: Renderer, hold: float) -> Melodic:
    x = render_note(program=m["program"], key=m["key"], hold=hold)
    f0 = 440 * 2 ** ((m["key"] - 69) / 12)
    a = onset(x)
    off = a + int(hold * SR)                # smf2wav drops leading silence: the note starts at a
    env = rms_envelope(x)
    nbytes = m.get("attack_bytes", 2048)
    rate = min(dt.DOC_RATE, max(nbytes / 0.12, 20 * f0))
    end = rising_zero(x, a + int(nbytes / rate * SR), int(SR / f0) + 2)
    loop_at = rising_zero(x, max(end, a + int(0.3 * SR)), int(SR / f0) + 2)
    period = refine_period(x, loop_at, f0)
    flat = x[a:end] / np.maximum(env[a:end], 0.1 * np.max(env[a:off]))
    attack = resample(flat, nbytes)
    # the loop is level-matched to the attack's last quarter, so the handover is level
    cycle = one_cycle(x, loop_at, period, out=LOOP_BYTES)
    cycle_rms = float(np.sqrt(np.mean(cycle ** 2))) or 1.0
    attack_rms = float(np.sqrt(np.mean(attack[-nbytes // 4:] ** 2))) or 1.0
    peak = max(np.max(np.abs(attack)), np.max(np.abs(cycle)) * attack_rms / cycle_rms)
    return Melodic(
        m, Wave(f"{m['name']} A", to_doc(attack, peak)),
        Wave(f"~{m['name']} L", to_doc(cycle * attack_rms / cycle_rms, peak)),
        rate=nbytes / ((end - a) / SR),
        # the loop holds one MT-32 period = f0*period/SR nominal cycles, so it plays at the
        # MT-32's own pitch, matching the attack sample
        loop_cycles=f0 * period / SR,
        env=fit_envelope(env, a, off), loudness=float(np.max(env[a:off])), period=period)


def sample_drum(d: dict, render_note: Renderer) -> Drum:
    """A one-shot of d["bytes"], at the rate that spreads its -46 dB length over them (at
    least MIN_DRUM_RATE), faded out over its last 10%."""
    x = render_note(program=None, key=d["key"], hold=0.5)
    a = onset(x)
    env = rms_envelope(x, 0.005)
    tail = np.nonzero(env[a:] > 0.005 * np.max(env))[0]
    dur = (tail[-1] + 1) / SR if len(tail) else 0.1
    n = d["bytes"]
    rate = min(dt.DOC_RATE, max(MIN_DRUM_RATE, n / dur))
    seg = x[a:a + int(min(dur, n / rate) * SR)]
    fade = np.ones(len(seg))
    k = max(1, len(seg) // 10)
    fade[-k:] = np.linspace(1, 0, k)
    data = to_doc(resample(seg * fade, int(len(seg) / SR * rate)))
    offsets = {c: 0 if c == d["key"] else pitch_offset(x, render_note(program=None, key=c, hold=0.5))
               for c in d["covers"]}
    return Drum(d, Wave(d["name"], data.ljust(n, bytes([SILENCE]))[:n]), rate, offsets, float(np.max(env)), dur)


# ---------------------------------------------------------------------------
# Instruments

def _volume(loudness: float, loudest: float) -> int:
    return max(MIN_VOLUME, min(127, round(127 * loudness / loudest)))


def melodic_instrument(s: Melodic, pages: dict[str, int], volume: int, silence: tuple) -> Instrument:
    m = s.spec
    ta = dt.encode(dt.tuning_for(s.rate, m["key"], s.attack.size_code))
    tl = dt.encode(dt.single_cycle_tuning(s.loop_cycles))
    wl = wavelist(127, OSC_SWAP, (pages[s.attack.name], s.attack.size_code, volume, *ta),
                  (pages[s.loop.name], 0, volume, *tl))
    return Instrument.build(m["name"], (s.env, [wl]), (GEN2_OFF, [wavelist(127, OSC_FREE, silence, silence)]))


def _key_max(z: Drum) -> int:
    """The highest home key at which a drum's wave plays in tune: its tuning must fit in
    octaves 0-6, so slow and small (single-cycle mode) waves need low keys."""
    size = z.wave.size_code
    return math.floor(69 + 12 * (math.log2(z.rate / dt.B0) - (size - dt.resolution(size)) + 4) - 0.5)


def _audible_seconds(z: Drum) -> float:
    """Where the one-shot falls below AUDIBLE of its peak."""
    y = np.abs(np.frombuffer(z.wave.data, np.uint8).astype(float) - 128)
    e = np.convolve(y, np.ones(32) / 32, mode="same")
    above = np.nonzero(e >= AUDIBLE * e.max())[0]
    return (above[-1] + 1) / z.rate if len(above) else 0.05


def kit_instrument(slot: int, zones: list[Drum], pages: dict[str, int], volume: Callable[[Drum], int],
                   silence: tuple, drums: dict[int, list[int]]) -> tuple[Instrument, float, int]:
    """A drum kit: its zones laid out up the keyboard in order of how high each can play
    (`_key_max`), each wide enough for the rhythm keys it covers. Fills `drums` (rhythm key ->
    [slot, key]). Returns the instrument, its longest one-shot (s) and its fall rate."""
    if len(zones) > WAVELISTS:
        raise ValueError(f"kit slot {slot}: {len(zones)} zones > {WAVELISTS}")
    wls = []
    cursor = 0
    ordered = sorted(zones, key=_key_max)
    for j, z in enumerate(ordered):
        home = cursor + max(0, -min(z.offsets.values()))
        if home > _key_max(z):
            raise ValueError(f"kit slot {slot}: no key range left for {z.spec['name']} "
                             f"(home {home} > max {_key_max(z)})")
        top = 127 if j == len(ordered) - 1 else home + max(0, max(z.offsets.values()))
        cursor = top + 1
        t = dt.encode(dt.tuning_for(z.rate, home, z.wave.size_code))
        wls.append(wavelist(top, OSC_ONESHOT, (pages[z.wave.name], z.wave.size_code, volume(z), *t), silence))
        for c, off in z.offsets.items():
            drums[c] = [slot, home + off]
    # One-shot drums: decay to zero over the kit's longest sample, so each hit frees its
    # voice by itself (note-off, if early, releases at the same rate).
    longest = max(_audible_seconds(z) for z in zones)
    fall = max(MIN_KIT_FALL_RATE, dt.segment_rate(127, 0, longest))
    env = envelope(attack=(127, 31), decay1=(127, 31), decay2=(127, 31), sustain=0,
                   decay3_rate=fall, release1=(0, fall), velocity_gain=4)
    inst = Instrument.build(f"Kit {'AB'[slot != 0]}", (env, wls),
                            (GEN2_OFF, [wavelist(127, OSC_FREE, silence, silence)] * len(wls)))
    return inst, longest, fall


# ---------------------------------------------------------------------------
# Building

def build(spec: dict, smf2wav: Path, roms: Path, cache: Path, log=print):
    """(Bank, WaveBank, MT-32 program -> slot, rhythm key -> [slot, key])."""
    render_note = lambda **kw: render(smf2wav, roms, cache, **kw)
    hold = spec.get("hold_seconds", 2.0)
    melodic: dict[int, Melodic] = {}
    for m in spec["melodic"]:
        s = melodic[m["slot"]] = sample_melodic(m, render_note, hold)
        nbytes, f0 = len(s.attack.data), 440 * 2 ** ((m["key"] - 69) / 12)
        log(f"  {m['name']:<12} prog {m['program']:3d} key {m['key']}: attack {nbytes} B @ {s.rate:7.0f} Hz "
            f"({nbytes / s.rate * 1000:.0f} ms), loop period {s.period:.2f} smp "
            f"(MT-32 {SR / s.period:.2f} Hz vs {f0:.2f})")
    drum_list: list[Drum] = []
    for d in spec["drum"]:
        z = sample_drum(d, render_note)
        drum_list.append(z)
        log(f"  {d['name']:<12} rhythm key {d['key']}: {d['bytes']} B @ {z.rate:6.0f} Hz "
            f"({z.seconds * 1000:.0f} ms), covers {z.offsets}")
    kits: dict[int, list[Drum]] = {}
    for z in drum_list:
        kits.setdefault(z.spec["slot"], []).append(z)

    silence_wave = Wave("Silence", bytes([SILENCE]) * 256)
    # spec order: the allocator places equal-size waves in list order
    waves = [silence_wave, *(w for s in melodic.values() for w in (s.attack, s.loop)),
             *(z.wave for z in drum_list)]
    reserve = spec.get("reserve_pages", 0)
    pages = allocate(waves, FIRST_PAGE, 255 - reserve)
    used = sum(w.pages for w in waves)
    log(f"  DOC RAM: {used} pages used of {256 - FIRST_PAGE - reserve} "
        f"(+{reserve} reserved for effects, {FIRST_PAGE} metronome)")
    loudest = max([s.loudness for s in melodic.values()] + [z.loudness for z in drum_list])
    silence = (pages[silence_wave.name], 0, 0, 4, 0, 0)

    insts = [Instrument("", bytes(INSTRUMENT_SIZE)) for _ in range(INSTRUMENTS)]
    programs: dict[int, int] = {}
    drums: dict[int, list[int]] = {}
    for slot, s in melodic.items():
        insts[slot] = melodic_instrument(s, pages, _volume(s.loudness, loudest), silence)
        programs.update(dict.fromkeys(s.spec["covers"], slot))
    for slot, zones in kits.items():
        insts[slot], longest, fall = kit_instrument(slot, zones, pages, lambda z: _volume(z.loudness, loudest),
                                                    silence, drums)
        log(f"  kit slot {slot}: {len(zones)} zones, voice frees after ~{longest:.2f}s (rate {fall})")
    bank = Bank(f"{spec['bank']}.WAV", INSTRUMENTS, insts)
    wb = WaveBank({pages[w.name]: (w.name[:15], w.size_code) for w in waves}, ram_image(waves, pages))
    return bank, wb, programs, drums


def map_toml(spec_path: Path, programs: dict[int, int], drums: dict[int, list[int]]) -> str:
    """The generated mapping mid2seq reads (BankMap `map_file`)."""
    lines = [f"# Generated by migs.mt32bank from {spec_path} -- do not edit",
             "[programs]   # MT-32 program -> slot",
             *(f'"{p}" = {s}' for p, s in sorted(programs.items())),
             "", "[drums]      # MT-32 rhythm key -> [slot, key]",
             *(f'"{k}" = {v}' for k, v in sorted(drums.items()))]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", type=Path, default=Path("config/mt32bank.toml"))
    ap.add_argument("--smf2wav", type=Path, default=Path("vendor/munt/bin/mt32emu-smf2wav"))
    ap.add_argument("--roms", type=Path, default=Path("original files/mt-32 roms"))
    ap.add_argument("--cache", type=Path, default=Path("build/cache/mt32samples"))
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("build/seq/mt32"))
    args = ap.parse_args(argv)
    spec = tomllib.loads(args.spec.read_text())
    print(f"sampling MT-32 per {args.spec}")
    bank, wb, programs, drums = build(spec, args.smf2wav, args.roms, args.cache)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    name = spec["bank"]
    wave_bytes = wb.to_bytes()
    (args.out_dir / f"{name}.BNK").write_bytes(bank.to_bytes())
    (args.out_dir / f"{name}.WAV").write_bytes(wave_bytes)
    (args.out_dir / f"{name}.map.toml").write_text(map_toml(args.spec, programs, drums))
    print(f"wrote {args.out_dir}/{name}.BNK, {name}.WAV (sha {hashlib.sha256(wave_bytes).hexdigest()[:12]}), "
          f"{name}.map.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
