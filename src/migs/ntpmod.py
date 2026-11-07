# SPDX-License-Identifier: GPL-3.0-or-later
"""MI1 MT-32 MIDI -> 15-channel ProTracker MOD, built to NinjaTrackerPlus' constraints.

The MOD is the primary artefact: it renders on the desktop (ffmpeg/libopenmpt) and converts
to .NTP for the IIGS. Samples come from Munt renders of the sounds in gs/mt32bank.toml and
are packed as NTP will pack them into the 64 KB DOC RAM. See docs/ninjatrackerplus.md.

Melodic sample = raw MT-32 attack (`attack_bytes`, loop header) + a 512-byte loop holding
k identical sustain cycles (one averaged cycle, tiled: the loop is exactly periodic), at a rate
that makes 512 bytes = k periods; note base + finetune make it play in tune. The attack's end
is crossfaded into the loop's periodic continuation, so neither seam clicks or buzzes.
Loudness after the attack follows the MT-32 envelope via per-row Cxx.
Levels are measured in Munt, not guessed: each sample's peak, its velocity response, and the
MT-32's channel-volume (CC7) curve; each song is normalised so its loudest note is volume 64.
Velocity: Cxx in the note's cell; if the note needs an EDx micro-delay, Cxx goes in the
preceding cell when that's silent-safe (one-shot track), else the note uses the instrument's
default volume and the next row corrects it.
"""

from __future__ import annotations

import argparse
import functools
import math
import re
import struct
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import mido
import numpy as np
from scipy.signal import resample

from . import mt32bank as mb

PAL = 7093789.2 / 2                       # Amiga PAL clock / 2: rate = PAL / period (NTP)
ROWS = 64
STOPPER = 8
NOTES = 72                                # NTP/ProTracker 6-octave table
MI1_TICK = 0.5 / 120                      # seconds per MI1 tick (120 PPQN, 120 BPM)
RHYTHM = 9


# ---------------------------------------------------------------------------
# Period tables (ProTracker, extended to 6 octaves, all finetunes) from NTP's make_tables.php

def period_tables(make_tables: Path) -> dict[int, list[int]]:
    text = make_tables.read_text().replace("\r", "")
    body = text[text.index("$pt_durations_finetune"):text.index("];\n\n/* find the highest")]
    tables = {}
    for m in re.finditer(r"//\s*tuning\s*(-?\d+)(.*?)\]", body, re.S):
        nums = [int(x) for x in re.findall(r"\b\d+\b", re.sub(r"/\*.*?\*/", "", m.group(2)))]
        tables[int(m.group(1))] = nums[:NOTES]
    if 0 not in tables or any(len(v) < NOTES for v in tables.values()):
        raise ValueError(f"{make_tables}: could not read the 16 finetune period tables")
    return tables


# ---------------------------------------------------------------------------
# Samples

@dataclass
class ModSample:
    name: str
    data: np.ndarray            # float -1..1
    loop_start: int = 0         # bytes
    loop_len: int = 0           # 0 = one-shot
    finetune: int = 0
    base_note: int = 0          # NTP note index (0-71) that plays MT-32 key `key0`
    key0: int = 60
    kind: str = "melodic"
    level: float = 1.0          # peak amplitude of the source (Munt, velocity 100, CC7 100) / data peak
    vel_v: np.ndarray = field(default_factory=lambda: np.array([0.0, 127.0]))  # velocity response:
    vel_g: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.27]))   # gain vs velocity 100
    key_v: np.ndarray = field(default_factory=lambda: np.array([0.0]))         # key response:
    key_g: np.ndarray = field(default_factory=lambda: np.array([1.0]))         # gain vs key0
    env_t: np.ndarray = field(default_factory=lambda: np.zeros(1))   # seconds after note-on
    env_g: np.ndarray = field(default_factory=lambda: np.ones(1))    # loudness vs note-on (may exceed 1: swells)
    rel_g: np.ndarray = field(default_factory=lambda: np.zeros(1))   # release, per 10 ms
    seconds: float = 0.0        # one-shot length (drums)
    default_volume: int = 48
    tune_cents: float = 0.0
    steady: bool = False        # sustain holds its level (a raw multi-cycle loop won't pump)
    rate: float = 0.0           # sample rate at key0 (Hz)
    max_loop: int = 512         # longest loop the recording allows (bytes, power of two)

    def mod_bytes(self) -> bytes:
        return bytes(np.clip(np.round(self.data * 127), -128, 127).astype(np.int8).tobytes())

    def ntp_blocks(self) -> list[int]:
        """DOC bytes this sample takes once NTP prepares it (power-of-two blocks)."""
        def block(n):
            return 1 << max(8, math.ceil(math.log2(n)))
        def with_stoppers(n):
            return n if n in (256, 512, 1024, 2048, 4096, 8192, 16384, 32768) else n + STOPPER
        if self.loop_len:
            return [block(with_stoppers(self.loop_start)), block(with_stoppers(self.loop_len))]
        return [block(with_stoppers(len(self.data)))]


def _best_tuning(freq: float, tables, min_rate, max_rate, loop_bytes, key0, key_lo, key_hi):
    """(err_cents, k, rate, base_note, finetune): `loop_bytes` = k periods at `rate`, played
    by note base_note with finetune so that period gives exactly that rate."""
    ks = np.arange(max(1, math.ceil(loop_bytes * freq / max_rate)), math.floor(loop_bytes * freq / min_rate) + 1)
    n = np.arange(NOTES)
    valid = (n + key_lo - key0 >= 0) & (n + key_hi - key0 < NOTES)
    if not len(ks) or not valid.any():
        raise ValueError(f"no tuning for {freq:.1f} Hz over keys {key_lo}-{key_hi}")
    fts = list(tables)
    per = np.array([tables[ft] for ft in fts], dtype=np.float64)          # (finetune, note)
    rates = loop_bytes * freq / ks                                        # (k,)
    err = 1200 * np.log2((PAL / per)[None, :, :] / rates[:, None, None])  # (k, finetune, note)
    a = np.where(valid[None, None, :], np.abs(err), np.inf)
    # first minimum in (k, finetune, note) scan order, as the original nested loops chose
    ki, fi, ni = np.unravel_index(int(np.argmin(a)), a.shape)
    return float(err[ki, fi, ni]), int(ks[ki]), float(rates[ki]), int(ni), fts[fi]


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    """Centered moving average over n samples (edges averaged over what exists): the window of
    np.convolve(x, ones(n), "same") / the same of ones, i.e. x[i - n//2 .. i + (n-1)//2] clipped,
    computed in O(len) from a cumulative sum instead of O(len * n) (250 ms windows at 32 kHz
    made the direct convolution 2/3 of the whole build)."""
    if n <= 1:
        return x
    c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    i = np.arange(len(x))
    lo = np.maximum(i - n // 2, 0)
    hi = np.minimum(i + (n - 1) // 2, len(x) - 1) + 1
    return (c[hi] - c[lo]) / (hi - lo)


def env_at(s: "ModSample", dt: float, width: float, t_loop: float | None = None) -> float:
    """Mean envelope gain over [dt - width/2, dt + width/2]: one Cxx per row stands for the
    whole row, so averaging (not point sampling) keeps tremolo from aliasing into jumps.
    Before `t_loop` (when this note's attack hands over to the loop) the gain is 1: the
    attack is raw source audio and already carries its own contour; env_g is relative to
    the loop, which is taken later in the sustain."""
    ts = np.linspace(dt - width / 2, dt + width / 2, 9)
    g = np.interp(ts, s.env_t, s.env_g, left=s.env_g[0], right=s.env_g[-1])
    if t_loop is not None:
        g = np.where(ts < t_loop, 1.0, g)
    return float(np.mean(g))


def periodic_loop(x: np.ndarray, start: float, period: float, k: int, out: int, cycles: int = 4) -> np.ndarray:
    """`out` points holding k identical cycles: the average of `cycles` source periods from
    `start`, tiled k times and band-limited. Exactly periodic, so the loop seam is continuous;
    the source's own cycle-to-cycle drift (chorus, vibrato) would otherwise jump at the seam
    and buzz at the loop rate."""
    fine = 1024
    acc = np.zeros(fine)
    for c in range(cycles):
        acc += np.interp(start + period * (c + np.arange(fine) / fine), np.arange(len(x)), x)
    cyc = acc / cycles
    loop = resample(np.tile(cyc, k), out)
    # averaging detuned partials loses level: match the source's RMS over the same span
    src = np.interp(start + np.arange(fine * cycles) * (period / fine), np.arange(len(x)), x)
    e_src, e_loop = float(np.sqrt(np.mean(src ** 2))), float(np.sqrt(np.mean(loop ** 2)))
    return loop * (e_src / e_loop) if e_loop > 0 else loop


def raw_loop(x: np.ndarray, start: float, period: float, k: int, out: int, xfade: float = 0.25) -> np.ndarray:
    """`out` points holding k source periods from `start`, as recorded (chorus, beating and
    vibrato kept), with a crossfade seam: the last `xfade` of the loop fades into the audio
    just before `start`, so the end flows into the start. Used for long loops of steady
    sounds; short loops use periodic_loop (one averaged cycle, tiled)."""
    M = out * 4
    step = k * period / M
    xi = np.arange(len(x))
    body = np.interp(start + np.arange(M) * step, xi, x)
    nx = int(M * xfade)
    pre = np.interp(start - (nx - np.arange(nx)) * step, xi, x)
    w = np.linspace(0, 1, nx + 2)[1:-1]
    body[-nx:] = body[-nx:] * (1 - w) + pre * w
    return resample(body, out)


def zero_start(loop: np.ndarray, c: int) -> np.ndarray:
    """Rotate a periodic loop so the rising zero crossing between samples c-1 and c lands
    exactly on index 0. The fractional part of the shift is an FFT phase rotation (exact for a
    periodic signal), so nothing is overwritten: forcing loop[0] = 0 at a steep crossing
    left a step of up to 11x a normal sample step, i.e. a click every loop pass (a buzz)."""
    n = len(loop)
    y0, y1 = loop[(c - 1) % n], loop[c]
    p = (c - 1) + (-y0 / (y1 - y0) if y1 != y0 else 1.0)    # crossing position, in samples
    k = np.fft.rfftfreq(n, 1 / n)
    out = np.fft.irfft(np.fft.rfft(loop) * np.exp(2j * np.pi * k * p / n), n)
    out[0] = 0.0                                          # residual is ~1e-16
    return out


def attack_and_loop(x: np.ndarray, a: int, period: float, rate: float, k: int, attack_bytes: int,
                    loop_bytes: int, sustain_s: float = 0.4, xfade_cycles: float = 4.0,
                    raw: bool = False) -> tuple[np.ndarray, np.ndarray, float, float]:
    """(attack, loop, source index where the attack ends, source index the loop was taken at).

    - The loop's cycles come from the steady sustain (`sustain_s` after the onset, earlier if
      the sound has decayed below 20% of its peak by then), not from where the attack ends:
      at 2040 B the attack is only ~0.12 s, before e.g. an organ's fundamental has built up
      (Pipe Org 2 at key 60: 61% of its energy in the fundamental at 0.4 s, 17% at 0.12 s).
    - The attack is resampled with a margin (no FFT edge ringing at the seam), and its last
      `xfade_cycles` periods crossfade into the loop's periodic continuation.
    - The loop starts at a rising zero crossing with loop[0] = 0 exactly: Ninjaforce's
      converter rotates every loop to its first sample nearest zero and moves the skipped
      bytes into the loop header, which pushed 2040 B headers into 4 KB blocks and overflowed
      DOC RAM. Of the rising crossings, the one whose phase best matches the attack's tail is
      used, so the crossfade doesn't cancel itself."""
    SR = mb.SR
    att_end = a + attack_bytes / rate * SR
    env = mb.rms_envelope(x)
    top = float(np.max(env[a:])) or 1.0
    at = a + sustain_s * SR
    while at > att_end and env[int(at)] < 0.2 * top:
        at -= 0.01 * SR
    at = max(at, att_end)
    loop = (raw_loop if raw else periodic_loop)(x, at, period, k, loop_bytes)
    margin = int(4 * period)
    hi = min(len(x), int(round(att_end)) + margin)
    seg = x[a:hi]
    attack = resample(seg, int(round(len(seg) * rate / SR)))[:attack_bytes]
    if len(attack) < attack_bytes:
        attack = np.pad(attack, (0, attack_bytes - len(attack)))
    n = min(attack_bytes // 4, max(4, int(round(xfade_cycles * loop_bytes / k))))
    tail = attack[-n:]
    # Level-match the loop to the attack's tail: the loop is taken later in the sustain, where
    # the level differs; unmatched, every note stepped by up to ~4 dB at the handover (choppy).
    # The envelope then starts from gain 1 here (build_samples eases it to the contour).
    e_tail, e_lp = float(np.sqrt(np.mean(tail ** 2))), float(np.sqrt(np.mean(loop ** 2)))
    if e_tail > 0 and e_lp > 0:
        loop = loop * (e_tail / e_lp)
    idx = np.nonzero((np.roll(loop, 1) < 0) & (loop >= 0))[0]
    if not len(idx):
        idx = np.array([int(np.argmin(np.abs(loop)))])
    def score(c):
        ext = np.roll(loop, -c)[np.arange(-n, 0) % loop_bytes]
        return float(np.dot(tail, ext)) / ((np.linalg.norm(tail) * np.linalg.norm(ext)) or 1.0)
    loop = zero_start(loop, int(max(idx, key=score)))
    ext = loop[np.arange(-n, 0) % loop_bytes]           # what the loop would have been before its start
    w = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n + 1)[1:])
    attack[-n:] = tail * (1 - w) + ext * w
    return attack, loop, att_end, at


def drum_rate(seg: np.ndarray, nbytes: int, lo: float, hi: float) -> float:
    """Sample rate for a one-shot of `nbytes`: fast enough for the sound's bandwidth (2.2 x the
    frequency below which 95% of its energy lies), then lowered, by at most half, if the bytes
    can't hold 95% of its energy at that rate. Filling the bytes with the whole -46 dB tail
    instead put hats and shakers (energy ~14 kHz) at 6 kHz, which made them all but silent."""
    SR = mb.SR
    P = np.abs(np.fft.rfft(seg)) ** 2
    c = np.cumsum(P) / (P.sum() or 1.0)
    f95 = float(np.fft.rfftfreq(len(seg), 1 / SR)[min(np.searchsorted(c, 0.95), len(c) - 1)])
    bw = min(hi, max(lo, 2.2 * f95))
    e = np.cumsum(seg ** 2) / (float(np.sum(seg ** 2)) or 1.0)
    d95 = max(int(np.searchsorted(e, 0.95)), 1) / SR
    fits = nbytes / d95                                  # rate at which the bytes hold 95%
    return float(min(hi, max(lo, bw if fits >= bw else max(fits, bw / 2))))


VEL_POINTS = (20, 40, 64, 80, 100, 127)


_PEAKS: dict[int, float] = {}


@functools.lru_cache(maxsize=None)
def _render(smf2wav: Path, roms: Path, cache: Path, program: int | None, key: int, hold: float,
            velocity: int = 100, volume: int = 100) -> np.ndarray:
    """mb.render, cached in-process (read-only arrays): each sound's velocity and key responses
    were re-measured from the same WAVs for every song."""
    x = mb.render(smf2wav, roms, cache, program=program, key=key, hold=hold, velocity=velocity, volume=volume)
    x.flags.writeable = False
    return x


def _peak(x: np.ndarray) -> float:
    if not x.flags.writeable and id(x) in _PEAKS:       # a cached render (kept alive by _render)
        return _PEAKS[id(x)]
    v = float(np.max(mb.rms_envelope(x, 0.005)))
    if not x.flags.writeable:
        _PEAKS[id(x)] = v
    return v


def velocity_response(render_at) -> tuple[np.ndarray, np.ndarray]:
    """Peak level vs velocity, relative to velocity 100, measured from renders (the MT-32's
    velocity sensitivity differs per timbre: closed hat x2.5 at 127, bass x1.4)."""
    ref = _peak(render_at(100)) or 1.0
    return (np.array([0.0, *VEL_POINTS], float),
            np.array([0.0, *(_peak(render_at(v)) / ref for v in VEL_POINTS)]))


def key_response(render_at, key0: int, lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """Peak level vs key, relative to the sampled key `key0`: MT-32 timbres change level with
    key (TVA key follow, partial balance), so one sample's level doesn't fit its whole range."""
    keys = sorted({lo, hi, key0, *range(lo, hi, 12)})
    ref = _peak(render_at(key0)) or 1.0
    return np.array(keys, float), np.array([_peak(render_at(k)) / ref for k in keys])


CC7_POINTS = (0, 10, 20, 32, 40, 52, 64, 77, 90, 100, 110, 120, 127)


def cc7_curve(smf2wav, roms, cache, program: int = 50, key: int = 67) -> tuple[np.ndarray, np.ndarray]:
    """The MT-32 part-volume (CC7) curve, gain relative to CC7 100, measured in Munt. It is
    steep (about 0.35 at 64, 0.11 at 40) and the same on melodic and rhythm parts."""
    lv = [_peak(_render(smf2wav, roms, cache, program=program, key=key, hold=0.5, volume=v)) for v in CC7_POINTS]
    ref = lv[CC7_POINTS.index(100)] or 1.0
    return np.array(CC7_POINTS, float), np.array(lv) / ref


def is_steady(env: np.ndarray, a: int, off: int, sustain_s: float, max_drop_db: float = 6.0) -> bool:
    """True if the sound holds its level from the sustain start to note-off (organs, strings,
    winds). Decaying sounds (marimba, bass, vibes) keep the short averaged loop: a raw
    multi-cycle loop would bake the decay in and pump on every pass."""
    SR = mb.SR
    lv = np.exp(smooth(np.log(np.maximum(env, 1e-6)), int(0.25 * SR)))
    i0, i1 = a + int(sustain_s * SR), off - int(0.15 * SR)
    if i1 <= i0:
        return False
    seg = lv[i0:i1]
    return 20 * math.log10(float(seg.max()) / max(float(seg.min()), 1e-9)) <= max_drop_db


def plan_loops(samples: list[ModSample], usage: dict[int, float], last_page: int,
               max_loop: int) -> dict[int, int]:
    """Loop size per melodic sample index, spending the song's free DOC RAM (pages up to
    `last_page`) on its steady sounds: repeatedly double the loop with the most sustained
    playing time per byte, as long as everything still packs the way NTP packs it."""
    sizes = {i: s.loop_len for i, s in enumerate(samples) if s.kind == "melodic"}
    grow = {i for i, s in enumerate(samples) if s.kind == "melodic" and s.steady and usage.get(i, 0) > 0}

    def fits(sz: dict[int, int]) -> bool:
        blocks = []
        for i, s in enumerate(samples):
            b = s.ntp_blocks()
            if i in sz:
                b = [b[0], sz[i]]
            blocks += b
        try:
            mb.allocate([mb.Wave(f"b{j}", bytes(b)) for j, b in enumerate(blocks)], 0, last_page)
            return True
        except ValueError:
            return False

    while grow:
        i = max(grow, key=lambda j: usage[j] / sizes[j])
        if sizes[i] * 2 > min(max_loop, samples[i].max_loop):
            grow.discard(i)
            continue
        trial = {**sizes, i: sizes[i] * 2}
        if fits(trial):
            sizes = trial
        else:
            grow.discard(i)
    return sizes


def build_samples(spec: dict, cfg: dict, key_ranges: dict[int, tuple[int, int]], smf2wav, roms, cache,
                  tables, log=print) -> tuple[list[ModSample], dict, dict]:
    """Samples per gs/mt32bank.toml; returns (samples, program->index, rhythm key->(index, note, level gain))."""
    SR = mb.SR
    hold = spec.get("hold_seconds", 2.0)
    samples, programs, drums = [], {}, {}
    for m in spec["melodic"]:
        x = _render(smf2wav, roms, cache, program=m["program"], key=m["key"], hold=hold)
        a = mb.onset(x)
        off = a + int(hold * SR)
        env = mb.rms_envelope(x)
        f0 = 440 * 2 ** ((m["key"] - 69) / 12)
        probe = a + int(0.25 * SR)
        period = mb.refine_period(x, probe, f0)
        fm = SR / period
        lo, hi = key_ranges.get(m["slot"], (m["key"], m["key"]))
        sustain_s = cfg.get("sustain_s", 0.4)
        steady = is_steady(env, a, off, sustain_s)
        loop_bytes = m.get("ntp_loop_bytes", cfg["loop_bytes"]) if steady else cfg["loop_bytes"]
        while True:
            err, k, rate, n0, ft = _best_tuning(fm, tables, cfg["min_rate"], cfg["max_rate"],
                                                loop_bytes, m["key"], min(lo, m["key"]), max(hi, m["key"]))
            # a long loop must fit in the recording between the sustain start and note-off
            if loop_bytes <= cfg["loop_bytes"] or a + (sustain_s + loop_bytes / rate + 0.05) * SR <= off:
                break
            loop_bytes //= 2
        # attack = source [a, a + attack_bytes/rate); loop = k periods from the sustain
        att_bytes = m.get("ntp_attack_bytes", cfg["attack_bytes"])
        attack, loop, att_end, loop_at = attack_and_loop(x, a, period, rate, k, att_bytes, loop_bytes,
                                                         sustain_s, cfg.get("attack_xfade_cycles", 4.0),
                                                         raw=loop_bytes > cfg["loop_bytes"])
        e_loop = float(np.sqrt(np.mean(loop ** 2))) or 1e-9
        # envelope after the attack, relative to the loop's own level
        t = np.arange(int(att_end), off, int(0.01 * SR))
        # Smooth in the log domain over 250 ms: MT-32 organs/strings have tremolo and partial
        # beating (Pipe Org 2: gain 0.7-2.3, ~4 Hz) that the static loop can't follow and that
        # per-row Cxx would alias into random jumps; exponential decays stay unbiased in log.
        # Steady sounds: only the slow contour. Their beating/chorus (Chorale: -1..-8 dB at
        # 2.5 Hz) as per-row Cxx is a stepped tremolo on a static loop (choppy), and a raw loop
        # already contains it (applying it again doubles it, out of phase). Smooth over 0.8 s,
        # and at least the raw loop's own length. Decaying sounds keep 250 ms for their decays.
        win = 0.25
        if steady:
            win = max(cfg.get("env_smooth_steady_s", 0.8), loop_bytes / rate if loop_bytes > cfg["loop_bytes"] else 0.0)
        g = np.exp(smooth(np.log(np.maximum(env, 1e-6)), int(min(win, 1.5) * SR)))[t] / e_loop
        # the loop continues the attack's tail at gain 1; ease to the contour over 150 ms
        # (geometric: linear in dB) instead of jumping to it at the first loop row
        w = np.clip((t - att_end) / (cfg.get("handover_s", 0.15) * SR), 0.0, 1.0)
        g = g ** w
        g_max = float(g.max()) if len(g) else 1.0
        rel = env[off: off + int(cfg["release_max"] * SR): int(0.01 * SR)]
        rel_g = rel / max(env[off], 1e-9)
        data = np.concatenate([attack, loop])
        peak = float(np.max(np.abs(data))) or 1.0
        s = ModSample(m["name"], data / peak, loop_start=att_bytes, loop_len=loop_bytes,
                      finetune=ft, base_note=n0, key0=m["key"], kind="melodic",
                      env_t=(t - a) / SR, env_g=g, rel_g=np.clip(rel_g, 0, 1),
                      tune_cents=err, level=peak)
        s.steady, s.rate = steady, rate
        s.max_loop = 1 << int(math.log2(max(1.0, ((off - a) / SR - sustain_s - 0.05) * rate)))
        s.max_loop = s.max_loop if steady else cfg["loop_bytes"]
        s.vel_v, s.vel_g = velocity_response(lambda v: _render(smf2wav, roms, cache, program=m["program"],
                                                                 key=m["key"], hold=0.5, velocity=v))
        s.key_v, s.key_g = key_response(lambda kk: _render(smf2wav, roms, cache, program=m["program"],
                                                             key=kk, hold=0.5), m["key"], min(lo, m["key"]),
                                        max(hi, m["key"]))
        idx = len(samples)
        samples.append(s)
        for p in m["covers"]:
            if "keys" in m:                          # key-split: (lo, hi, sample) per range
                programs.setdefault(p, []).append((*m["keys"], idx))
            else:
                programs[p] = idx
        log(f"  {m['name']:<12} loop {loop_bytes:5d} B{' raw' if loop_bytes > cfg['loop_bytes'] else '    '} "
            f"k={k:3d} rate {rate:7.0f} Hz note {n0:2d} ft {ft:+d} "
            f"err {err:+5.1f} cents, swell x{g_max:.2f}, keys {lo}-{hi}")

    for d in spec["drum"]:
        x = _render(smf2wav, roms, cache, program=None, key=d["key"], hold=0.5)
        a = mb.onset(x)
        env = mb.rms_envelope(x, 0.005)
        tail = np.nonzero(env[a:] > 0.005 * np.max(env))[0]
        dur = (tail[-1] + 1) / SR if len(tail) else 0.1
        want = drum_rate(x[a: a + int(dur * SR)], d["bytes"] - STOPPER,
                         cfg.get("drum_min_rate", 6000), cfg.get("drum_max_rate", 26000))
        # pick the note whose ft-0 period gives a rate close to `want`, leaving room for offsets
        offs = {c: 0 if c == d["key"] else mb.pitch_offset(x, mb.render(smf2wav, roms, cache,
                                                                         program=None, key=c, hold=0.5))
                for c in d["covers"]}
        lo_off, hi_off = min(offs.values()), max(offs.values())
        n0 = min((n for n in range(-lo_off, NOTES - hi_off)), key=lambda n: abs(PAL / tables[0][n] - want))
        rate = PAL / tables[0][n0]
        seg = x[a: a + int(min(dur, (d["bytes"] - STOPPER) / rate) * SR)]
        fade = np.ones(len(seg))
        kk = max(1, len(seg) // 10)
        fade[-kk:] = np.linspace(1, 0, kk)
        data = resample(seg * fade, max(2, int(len(seg) / SR * rate) // 2 * 2))
        peak = float(np.max(np.abs(data))) or 1.0
        s = ModSample(d["name"], data / peak, kind="drum", base_note=n0, key0=d["key"],
                      seconds=len(data) / rate, level=peak)
        s.vel_v, s.vel_g = velocity_response(lambda v: _render(smf2wav, roms, cache, program=None,
                                                                 key=d["key"], hold=0.5, velocity=v))
        idx = len(samples)
        samples.append(s)
        ref = _peak(x) or 1.0                            # each rhythm key has its own MT-32 level
        for c, o in offs.items():
            gain = 1.0 if c == d["key"] else _peak(_render(smf2wav, roms, cache, program=None, key=c, hold=0.5)) / ref
            drums[c] = (idx, n0 + o, gain)
        log(f"  {d['name']:<12} {len(data)} B @ {rate:6.0f} Hz ({len(data) / rate * 1000:4.0f} ms) note {n0:2d}, "
            f"covers {offs}")

    if len(samples) > 31:
        raise ValueError(f"{len(samples)} samples > 31 (MOD limit)")
    return samples, programs, drums


def ntp_doc_pages(samples: list[ModSample], last_page: int = 255) -> int:
    """DOC pages NTP needs for these samples (aligned blocks, packed largest first)."""
    blocks = sorted((b for s in samples for b in s.ntp_blocks()), reverse=True)
    waves = [mb.Wave(f"b{i}", bytes(b)) for i, b in enumerate(blocks)]
    pages = mb.allocate(waves, 0, last_page)             # raises if it can't fit
    return sum(b // 256 for b in blocks)


# ---------------------------------------------------------------------------
# Song conversion

@dataclass
class Note:
    t: float            # seconds
    off: float
    key: int
    vel: int
    channel: int
    volume: int = 100   # channel CC7 at note-on (MT-32 power-on part volume is 100)


def read_notes(path: Path) -> tuple[list[Note], dict[int, int], float]:
    """(notes, first program per channel, end s). Cached per file (path + mtime): a build reads
    each song several times (sample planning, level passes, conversion). Callers must not
    modify the returned notes."""
    st = Path(path).stat()
    return _read_notes(str(Path(path).resolve()), st.st_mtime_ns, st.st_size)


@functools.lru_cache(maxsize=None)
def _read_notes(path: str, _mtime: int, _size: int) -> tuple[list[Note], dict[int, int], float]:
    mf = mido.MidiFile(path)
    tempo = 500_000
    events = []
    for trk in mf.tracks:
        t = 0
        for msg in trk:
            t += msg.time
            if msg.type == "set_tempo":
                tempo = msg.tempo
            events.append((t, msg))
    events.sort(key=lambda e: e[0])
    sec = lambda tick: tick * tempo / 1e6 / mf.ticks_per_beat
    programs, notes, open_, volume = {}, [], {}, {}
    end = 0.0
    for tick, m in events:
        if m.type == "program_change":
            programs.setdefault(m.channel, m.program)
        elif m.type == "control_change" and m.control == 7:
            volume[m.channel] = m.value
        elif m.type == "note_on" and m.velocity > 0:
            open_.setdefault((m.channel, m.note), []).append(
                Note(sec(tick), sec(tick), m.note, m.velocity, m.channel, volume.get(m.channel, 100)))
        elif m.type in ("note_on", "note_off") and open_.get((m.channel, m.note)):
            n = open_[(m.channel, m.note)].pop(0)
            n.off = sec(tick)
            notes.append(n)
        elif m.type == "end_of_track":
            end = max(end, sec(tick))
    for lst in open_.values():
        for n in lst:
            n.off = end
            notes.append(n)
    notes.sort(key=lambda n: (n.t, n.channel, n.key))
    return notes, programs, max(end, max((n.off for n in notes), default=0))


def detect_grid(onsets: list[float], unit_range=(14, 80)) -> tuple[float, float]:
    """(unit, phase) in MI1 ticks maximising circular concentration of the onsets."""
    return _detect_grid(tuple(onsets), tuple(unit_range))


@functools.lru_cache(maxsize=None)
def _detect_grid(onsets: tuple[float, ...], unit_range: tuple[int, int]) -> tuple[float, float]:
    on = np.array(sorted(set(onsets))) / MI1_TICK
    best = (0.0, 30.0, 0.0)
    for P in np.arange(unit_range[0], unit_range[1], 0.05):
        z = np.mean(np.exp(2j * np.pi * on / P))
        if abs(z) > best[0]:
            best = (abs(z), P, (np.angle(z) / (2 * np.pi) * P) % P)
    return best[1], best[2]


@dataclass
class Report:
    notes: int = 0
    dropped: int = 0
    delayed: int = 0
    vel_in_cell: int = 0
    vel_preceding: int = 0
    vel_default: int = 0
    level_ref: float = 1.0      # note loudness that maps to MOD volume 64
    tempo_changes: int = 0
    track_kinds: list = field(default_factory=list)   # "melodic" (2 oscillators) / "drum" (1), in MOD order
    sample_volumes: dict = field(default_factory=dict)  # sample index -> note-on volumes in this song
    nudged: int = 0
    tracks_used: int = 0
    oscillators: int = 0
    stolen: int = 0
    rows: int = 0
    patterns: int = 0
    tempo: int = 0
    speed: int = 0
    row_ms: float = 0.0
    timing_err_ms: float = 0.0


def tempo_commands(ideal: float, speed: int, rows: int, can_set, tol: float) -> list[tuple[int, int]]:
    """[(row, tempo)] keeping the playback clock within ~`tol` s of a grid of rows lasting
    2.5 * speed / `ideal` s each, using the two integer tempos around `ideal`. A change is
    only written where `can_set(row)` (a free effect cell); row 0 always gets the start tempo.
    Hysteresis (`tol`) keeps the changes sparse: every ~tol / (per-row error) rows."""
    lo, hi = math.floor(ideal), math.ceil(ideal)
    row_ideal = 2.5 * speed / ideal
    if lo == hi:
        return [(0, lo)]
    cur = lo if ideal - lo >= 0.5 else hi
    out = [(0, cur)]
    err = 0.0                                            # actual - ideal time at the start of row r
    for r in range(1, rows):
        err += 2.5 * speed / cur - row_ideal
        want = cur
        if err > tol:
            want = hi                                    # running late: shorter rows
        elif err < -tol:
            want = lo
        if want != cur and can_set(r):
            cur = want
            out.append((r, cur))
    return out


def ntp_oscillators(track_osc: list[int]) -> list[int] | None:
    """First oscillator per track as Ninjaforce's converter assigns them (determine_osc_per_track):
    first-fit in track order over oscillators 0-30, pairs starting on even oscillators. None if
    some track gets no oscillator (the converter then silently writes 0)."""
    used = [False] * 31
    out = []
    for need in track_osc:
        for i in range(0, 31, need):
            if all(i + j < 31 and not used[i + j] for j in range(need)):
                for j in range(need):
                    used[i + j] = True
                out.append(i)
                break
        else:
            return None
    return out


class Grid:
    """cells[track][row] = [note(0-72), instrument(0-31), effect, param]; effect -1 = empty."""

    def __init__(self, tracks: int):
        self.cells: list[dict[int, list[int]]] = [dict() for _ in range(tracks)]

    def get(self, tr, row):
        return self.cells[tr].setdefault(row, [0, 0, -1, 0])

    def free(self, tr, row) -> bool:
        c = self.cells[tr].get(row)
        return c is None or (c[0] == 0 and c[2] < 0)

    def effect_free(self, tr, row) -> bool:
        c = self.cells[tr].get(row)
        return c is None or c[2] < 0


def sample_for(programs: dict, prog: int, key: int) -> int | None:
    """Sample index for a program and key: programs[prog] is an index, or a list of
    (lo, hi, index) key ranges (NTP-only key splits, e.g. a low-register organ sample)."""
    v = programs.get(prog)
    if isinstance(v, list):                      # in range, else the nearest range
        return min(v, key=lambda r: max(r[0] - key, key - r[1], 0))[2]
    return v


def melodic_entry(spec: dict, prog: int, key: int) -> dict | None:
    """The spec's melodic entry that plays (prog, key), honouring `keys` splits."""
    hits = [m for m in spec["melodic"] if prog in m["covers"]]
    split = [m for m in hits if "keys" in m]
    inside = [m for m in split if m["keys"][0] <= key <= m["keys"][1]]
    if inside:
        return inside[0]
    whole = [m for m in hits if "keys" not in m]
    if whole:
        return whole[0]
    return min(split, key=lambda m: max(m["keys"][0] - key, key - m["keys"][1], 0)) if split else None


def convert_song(path: Path, samples, programs, drums, cfg: dict, song_cfg: dict,
                 cc7: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[Grid, int, Report]:
    """`cc7`: (CC7 values, gains vs 100) from cc7_curve(); None ignores channel volume."""
    notes, chan_prog, end = read_notes(path)
    rep = Report()
    unit, phase = song_cfg.get("unit_ticks"), song_cfg.get("phase_ticks")
    if unit is None:
        unit, phase = detect_grid([n.t for n in notes])
    phase = phase or 0.0
    rpu, speed = song_cfg.get("rows_per_unit", cfg["rows_per_unit"]), song_cfg.get("speed", cfg["speed"])
    q = song_cfg.get("quantize", cfg["quantize"])
    row_s = unit * MI1_TICK / rpu
    tempo = round(2.5 * speed / row_s)
    while tempo > 255:
        speed -= 1
        tempo = round(2.5 * speed / row_s)
    while tempo < 32:
        speed += 1
        tempo = round(2.5 * speed / row_s)
    # Notes are placed on the ideal grid (row_s = unit / rpu exactly). MOD tempos are integers,
    # so one fixed tempo makes rows drift against the music (up to 0.65%, ~4 rows over MI160);
    # tempo_commands() alternates the neighbouring integer tempos to stay on the ideal grid.
    tempo_ideal = 2.5 * speed / row_s
    tick_s = row_s / speed
    shift = (row_s - (phase * MI1_TICK) % row_s) % row_s + row_s      # grid aligned, >= 1 row lead-in
    rep.tempo, rep.speed, rep.row_ms = tempo, speed, row_s * 1000

    def place(t: float) -> tuple[int, int]:
        n = (t + shift) / tick_s
        row_start = round(n / speed) * speed
        n = round(n + q * (row_start - n))
        r, d = divmod(n, speed)
        return int(r), int(d)

    # Tracks are created on demand within NTP's oscillator budget: one-shot (drum) tracks
    # need 1 oscillator, looped melodic tracks 2 (loop header + loop); 31 are usable.
    budget = song_cfg.get("oscillators", cfg.get("oscillators", 31))
    grid = Grid(0)
    trk_inst: list[int] = []        # current sample (1-based) per track
    trk_busy: list[int] = []        # last row with cells for the current note
    trk_off: list[int] = []         # note-off row of the current melodic note (-1: drum)
    trk_oneshot_end: list[float] = []
    trk_kind: list[str] = []
    osc_used = 0
    err_sum = 0.0

    def new_track(kind: str) -> int:
        nonlocal osc_used
        grid.cells.append({})
        for lst, val in ((trk_inst, 0), (trk_busy, -1), (trk_off, -1), (trk_oneshot_end, -1.0), (trk_kind, kind)):
            lst.append(val)
        osc_used += 1 if kind == "drum" else 2
        return len(grid.cells) - 1

    # Loudness of a note as the MT-32 plays it: sample peak x velocity response x CC7 curve.
    # The song is normalised so its loudest moment (99.5th percentile over notes, including
    # sounds that swell after their attack) is MOD volume 64.
    def sample_of(n: Note) -> int | None:
        if n.channel == RHYTHM:
            return drums[n.key][0] if n.key in drums else None
        return sample_for(programs, chan_prog.get(n.channel, -1), n.key)

    def amp(s: ModSample, n: Note) -> float:
        g7 = float(np.interp(n.volume, *cc7)) if cc7 is not None else 1.0
        gk = drums[n.key][2] if n.channel == RHYTHM else float(np.interp(n.key, s.key_v, s.key_g))
        return s.level * float(np.interp(n.vel, s.vel_v, s.vel_g)) * g7 * gk

    swell = lambda s: max(1.0, float(np.max(s.env_g))) if s.kind == "melodic" else 1.0
    amps = [amp(samples[si], n) * swell(samples[si]) for n in notes if (si := sample_of(n)) is not None]
    ref = song_cfg.get("level_ref") or (float(np.percentile(amps, 99.5)) if amps else 1.0)
    rep.level_ref = ref

    def vol_for(s: ModSample, n: Note) -> int:
        return max(1, min(64, round(64 * amp(s, n) / ref)))

    # default volume (used for one row by delayed notes): the sample's median in this song
    by_sample: dict[int, list[int]] = {}
    for n in notes:
        if (si := sample_of(n)) is not None:
            by_sample.setdefault(si, []).append(vol_for(samples[si], n))
    rep.sample_volumes = by_sample
    fixed = song_cfg.get("default_volumes")          # shared mode: the same for every song
    for si, s in enumerate(samples):
        if fixed is not None:
            s.default_volume = fixed.get(si, 32)
        else:
            s.default_volume = int(np.median(by_sample[si])) if si in by_sample else 32

    hyst = song_cfg.get("volume_hysteresis", cfg.get("volume_hysteresis", 0.75))
    for n in notes:
        rep.notes += 1
        if n.channel == RHYTHM:
            hit = drums.get(n.key)
            if hit is None:
                rep.dropped += 1
                continue
            si, note_idx = hit[:2]
        else:
            si = sample_for(programs, chan_prog.get(n.channel, -1), n.key)
            if si is None:
                rep.dropped += 1
                continue
            s0 = samples[si]
            note_idx = s0.base_note + (n.key - s0.key0)
            if not 0 <= note_idx < NOTES:
                note_idx += 12 * ((0 - note_idx) // 12 + 1) if note_idx < 0 else -12 * ((note_idx - NOTES) // 12 + 1)
        s = samples[si]
        r, d = place(n.t)
        err_sum += abs((r * speed + d) * tick_s - (n.t + shift))
        kind = "drum" if s.kind == "drum" else "melodic"
        # choose a track: free & same sample, else free of the same kind, else a new track,
        # else steal a same-kind track that is only releasing (cut its release)
        tracks = range(len(grid.cells))
        cands = [tr for tr in tracks if trk_kind[tr] == kind and trk_busy[tr] < r and grid.free(tr, r)]
        if cands:
            tr = min(cands, key=lambda t: (trk_inst[t] != si + 1, t))
        elif osc_used + (1 if kind == "drum" else 2) <= budget:
            tr = new_track(kind)
        else:
            releasing = [t for t in tracks if trk_kind[t] == kind and 0 <= trk_off[t] <= r]
            if not releasing:
                rep.dropped += 1
                continue
            tr = min(releasing, key=lambda t: trk_off[t])
            for row in [x for x in grid.cells[tr] if x >= r]:
                del grid.cells[tr][row]
            rep.stolen += 1
        trk_kind[tr] = kind
        same = trk_inst[tr] == si + 1
        vf = 64 * amp(s, n) / ref                    # unrounded, for the envelope rows
        v = vol_for(s, n)
        silent_before = False
        cell = grid.get(tr, r)
        cell[0] = note_idx + 1
        if d == 0:
            cell[1], cell[2], cell[3] = si + 1, 0xC, v
            rep.vel_in_cell += 1
        else:
            rep.delayed += 1
            silent_before = (kind == "drum" and same and trk_oneshot_end[tr] <= (r - 1) * row_s
                             and r - 1 > trk_busy[tr] and grid.effect_free(tr, r - 1))
            if silent_before:
                pc = grid.get(tr, r - 1)
                pc[2], pc[3] = 0xC, v
                cell[1] = 0                          # keep the track's volume
                rep.vel_preceding += 1
            else:
                cell[1] = si + 1                     # default volume; corrected next row
                rep.vel_default += 1
            cell[2], cell[3] = 0xE, 0xD0 | d
        trk_inst[tr] = si + 1
        last = r
        trk_off[tr] = -1
        if kind == "drum":
            trk_oneshot_end[tr] = (r * speed + d) * tick_s + s.seconds
            if d and not silent_before and grid.effect_free(tr, r + 1) and v != s.default_volume:
                c = grid.get(tr, r + 1)
                c[2], c[3] = 0xC, v
                last = r + 1
        else:
            # envelope rows until note-off, then release
            r_off, _ = place(n.off)
            r_off = max(r_off, r + 1)
            trk_off[tr] = r_off
            prev = None
            # the attack plays slower on lower notes: it hands over to the loop later
            t_loop = s.env_t[0] * 2 ** (-(n.key - s.key0) / 12)
            for rr in range(r + 1, r_off):
                dt = (rr - r) * row_s
                g = env_at(s, dt, row_s, t_loop)
                target = vf * g
                # hysteresis: a slowly moving target near x.5 made the volume flicker between
                # neighbours (4-5-4: ~2 dB steps at low volumes), heard as choppy sustains
                if prev is not None and abs(target - prev) < hyst:
                    last = rr
                    continue
                vv = max(0, min(64, round(target)))
                if vv != prev and grid.effect_free(tr, rr):
                    c = grid.get(tr, rr)
                    c[2], c[3] = 0xC, vv
                    prev = vv
                last = rr
            g_off = env_at(s, (r_off - r) * row_s, row_s, t_loop)
            v_off = vf * g_off
            steps = max(1, int(round(len(s.rel_g) * 0.01 / row_s)))
            for i in range(steps + 1):
                rr = r_off + i
                gg = float(np.interp(i * row_s, np.arange(len(s.rel_g)) * 0.01, s.rel_g, right=0.0)) if i < steps else 0.0
                vv = max(0, min(64, round(v_off * gg)))
                if grid.effect_free(tr, rr) and grid.cells[tr].get(rr, [0])[0] == 0:
                    c = grid.get(tr, rr)
                    c[2], c[3] = 0xC, vv
                last = rr
                if vv == 0:
                    break
        trk_busy[tr] = last

    rep.timing_err_ms = 1000 * err_sum / max(1, rep.notes - rep.dropped)
    rows_needed = max([max(c, default=0) for c in grid.cells]) + 1
    rows_needed = max(rows_needed, int((end + shift) / row_s) + 1)
    patterns = math.ceil(rows_needed / ROWS)
    if patterns > 128:
        raise ValueError(f"{patterns} patterns > 128 (MOD order list); raise row length")
    rep.rows, rep.patterns = rows_needed, patterns
    rep.tracks_used, rep.oscillators = len(grid.cells), osc_used
    if len(grid.cells) < 2:
        new_track("drum")
    # Two-oscillator (looped) tracks first: Ninjaforce's converter assigns oscillators first-fit
    # in track order with pairs on even oscillators, so interleaved 1- and 2-oscillator tracks
    # strand odd oscillators; tracks that then find no room silently get oscillator 0 (PHP
    # "Undefined array key" at ntpconverter_lib.php:1613), clashing with track 1.
    order = sorted(range(len(grid.cells)), key=lambda t: (trk_kind[t] != "melodic", t))
    grid.cells = [grid.cells[t] for t in order]
    trk_kind[:] = [trk_kind[t] for t in order]
    rep.track_kinds = list(trk_kind)
    # speed + tempo on row 0 (the lead-in row is always free), loop jump at the very end
    changes = tempo_commands(tempo_ideal, speed, patterns * ROWS,
                             lambda r: any(grid.effect_free(t, r) for t in range(len(grid.cells))),
                             song_cfg.get("tempo_tolerance_ms", cfg.get("tempo_tolerance_ms", 2.0)) / 1000)
    tempo = changes[0][1]
    rep.tempo, rep.tempo_changes = tempo, len(changes) - 1
    for r, t in changes[1:]:
        tr = next(t_ for t_ in range(len(grid.cells)) if grid.effect_free(t_, r))
        c = grid.get(tr, r)
        c[2], c[3] = 0xF, t
    for tr, (eff, par) in enumerate(((0xF, speed), (0xF, tempo))):
        c = grid.get(tr, 0)
        if c[0] or c[2] >= 0:
            raise AssertionError("row 0 must be free (lead-in)")
        c[2], c[3] = eff, par
    last_row = patterns * ROWS - 1
    tr = next((t for t in range(len(grid.cells)) if grid.effect_free(t, last_row)), None)
    if tr is not None:
        c = grid.get(tr, last_row)
        c[2], c[3] = 0xB, 0                     # loop to the start (like the game's FF)
    return grid, patterns, rep


def mod_file(title: str, samples: list[ModSample], grid: Grid, patterns: int, tables) -> bytes:
    tracks = len(grid.cells)
    out = bytearray(title.encode("latin-1")[:20].ljust(20, b"\0"))
    for i in range(31):
        if i < len(samples):
            s = samples[i]
            raw = s.mod_bytes()
            if len(raw) % 2:
                raw += b"\0"
            ls, ll = (s.loop_start // 2, s.loop_len // 2) if s.loop_len else (0, 1)
            out += s.name.encode("latin-1")[:22].ljust(22, b"\0")
            out += struct.pack(">HBBHH", len(raw) // 2, s.finetune & 0x0F, s.default_volume, ls, ll)
        else:
            out += bytes(22) + struct.pack(">HBBHH", 0, 0, 0, 0, 1)
    out += bytes([patterns, 127]) + bytes(range(patterns)) + bytes(128 - patterns)
    # channel tag: "nCHN" below 10 channels, "nnCH" from 10 (what trackers and NTP's converter expect)
    out += (f"{tracks}CHN" if tracks < 10 else f"{tracks:02d}CH").encode()
    per0 = tables[0]
    for p in range(patterns):
        for row in range(p * ROWS, (p + 1) * ROWS):
            for tr in range(tracks):
                note, inst, eff, par = grid.cells[tr].get(row, [0, 0, -1, 0])
                period = per0[note - 1] if note else 0
                eff, par = (0, 0) if eff < 0 else (eff, par)
                out += struct.pack(">HH", (inst & 0xF0) << 8 | period, (inst & 0x0F) << 12 | eff << 8 | par)
    for s in samples:
        raw = s.mod_bytes()
        out += raw + (b"\0" if len(raw) % 2 else b"")
    return bytes(out)


def ntp_spec(spec: dict, conf: dict, mode: str | None = None) -> dict:
    """The MI1 bank spec plus gs/ntp.toml's NTP-only `[[melodic]]` sounds. An extra sound takes
    its `covers` away from the shared bank's merges (NTP has DOC RAM the 16-slot MIDI Synth
    bank doesn't), e.g. Pipe Org 2, which MI1.BNK plays with Elec Org 2's sample."""
    extra = [m for m in conf.get("melodic", []) if mode is None or mode in m.get("modes", [mode])]
    taken = {p for m in extra for p in m["covers"]}
    melodic = [{**m, "covers": [p for p in m["covers"] if p not in taken]} for m in spec["melodic"]]
    return {**spec, "melodic": [m for m in melodic if m["covers"]] + list(extra)}


def loop_summary(samples: list[ModSample], cfg: dict) -> str:
    return " ".join(f"{s.name}:{s.loop_len}{'r' if s.loop_len > cfg['loop_bytes'] else ''}"
                    for s in samples if s.kind == "melodic")


def sample_set(spec: dict, cfg: dict, songs: list[tuple[list, dict]], last_page: int, tables, args):
    """(samples, programs, drums) for the sounds these songs play, with loop sizes planned into
    the DOC RAM they leave free (pages up to `last_page`). One song: a per-song set; all songs:
    the shared set."""
    used, ranges, keys = {}, {}, set()
    usage_notes = []
    for notes, chan_prog in songs:
        for n in notes:
            if n.channel == RHYTHM:
                keys.add(n.key)
                continue
            m = melodic_entry(spec, chan_prog.get(n.channel, -1), n.key)
            if m is None:
                continue
            used[m["slot"]] = m
            lo, hi = ranges.get(m["slot"], (n.key, n.key))
            ranges[m["slot"]] = (min(lo, n.key), max(hi, n.key))
            usage_notes.append((chan_prog.get(n.channel, -1), n))
    song_spec = {**spec, "melodic": [m for m in spec["melodic"] if m["slot"] in used],
                 "drum": [d for d in spec["drum"] if keys & set(d["covers"])]}
    build = lambda sp: build_samples(sp, cfg, ranges, args.smf2wav, args.roms, args.cache, tables, log=lambda *a: None)
    samples, programs, drums = build(song_spec)
    usage: dict[int, float] = {}
    for prog, n in usage_notes:
        si = sample_for(programs, prog, n.key)
        if si is not None:
            usage[si] = usage.get(si, 0.0) + (n.off - n.t)
    sizes = plan_loops(samples, usage, last_page, cfg.get("max_loop_bytes", 16384))
    if any(sizes[i] != samples[i].loop_len for i in sizes):
        by_name = {samples[i].name: sz for i, sz in sizes.items()}
        song_spec = {**song_spec, "melodic": [{**m, "ntp_loop_bytes": by_name.get(m["name"], cfg["loop_bytes"])}
                                              for m in song_spec["melodic"]]}
        samples, programs, drums = build(song_spec)
    return samples, programs, drums


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="MT-32 MIDI files (music/midi/mt32)")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--config", type=Path, default=Path("gs/ntp.toml"))
    ap.add_argument("--spec", type=Path, default=Path("gs/mt32bank.toml"))
    ap.add_argument("--make-tables", type=Path, default=Path("vendor/ntp/src/make_tables.php"))
    ap.add_argument("--smf2wav", type=Path, default=Path("vendor/munt/bin/mt32emu-smf2wav"))
    ap.add_argument("--roms", type=Path, default=Path("original files/mt-32 roms"))
    ap.add_argument("--cache", type=Path, default=Path("gs/build/mt32samples"))
    ap.add_argument("--samples", choices=("per-song", "shared"), help="override gs/ntp.toml `samples`")
    args = ap.parse_args(argv)
    conf = tomllib.loads(args.config.read_text())
    mode = args.samples or conf["defaults"].get("samples", "per-song")
    if mode not in ("per-song", "shared"):
        raise SystemExit(f"samples must be per-song or shared, not {mode!r}")
    cfg = {**conf["defaults"], **conf.get("modes", {}).get(mode, {})}    # e.g. [modes.shared]
    spec = ntp_spec(tomllib.loads(args.spec.read_text()), conf, mode)
    over = cfg.get("ntp_attack_bytes", {})               # per-mode attack lengths, by sound name
    spec = {**spec, "melodic": [{**m, "ntp_attack_bytes": over[m["name"]]} if m["name"] in over else m
                                for m in spec["melodic"]]}
    tables = period_tables(args.make_tables)
    cc7 = cc7_curve(args.smf2wav, args.roms, args.cache)
    print("MT-32 CC7 curve: " + " ".join(f"{int(v)}:{g:.2f}" for v, g in zip(*cc7)))

    # NTP files carry their own samples (loaded into DOC RAM by NTPprepare), so by default
    # each song gets only the sounds it plays and its free DOC RAM goes to longer loops
    # (`samples = "per-song"`). `samples = "shared"` builds one set for every song, like the
    # MIDI Synth bank (a game that can't afford per-song sample loads).
    last_page = 255 - cfg.get("reserve_pages", 16)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    songs = [(p, *read_notes(p)[:2]) for p in args.inputs]
    shared = sample_set(spec, cfg, [(n, cp) for _, n, cp in songs], last_page, tables, args) if mode == "shared" else None
    if shared:
        print(f"shared samples ({len(shared[0])}, DOC {ntp_doc_pages(shared[0], last_page)}/256 pages, "
              f"reserve {255 - last_page}): {loop_summary(shared[0], cfg)}")
    if shared:
        # A resident bank: every shared .NTP must carry byte-identical instruments and samples,
        # so a game can load them once and then only each song's header + patterns. So: one
        # loudness reference for all songs (also consistent levels between songs), and each
        # sample's default volume = its median note-on volume over all songs.
        # Each song is still normalised on its own (loudest moment = volume 64): a common
        # reference left quiet songs at MOD volume ~4, where one step is ~2.5 dB (choppy). The
        # MT-32's song-to-song balance goes to levels.toml for the game (NTPsetplayvolume).
        refs, vols = {}, {}
        for p, notes, chan_prog in songs:
            sid = p.stem.split("-")[0]
            _, _, r = convert_song(p, *shared, cfg, {**cfg, **conf.get("songs", {}).get(sid, {})}, cc7)
            refs[sid] = r.level_ref
            for si, v in r.sample_volumes.items():
                vols.setdefault(si, []).extend(v)
        bank = {"default_volumes": {si: int(np.median(v)) for si, v in vols.items()}}
        top = max(refs.values())
        (args.out_dir / "levels.toml").write_text(
            "# Song loudness relative to the loudest (MT-32 balance). Each shared .NTP is normalised\n"
            "# to its own loudest moment; a game restores the balance with NTPsetplayvolume.\n"
            + "".join(f'MI{sid} = {{ gain_db = {20 * math.log10(ref / top):.2f}, play_volume_scale = {ref / top:.3f} }}\n'
                      for sid, ref in refs.items()))
    reports = []
    for p, notes, chan_prog in songs:
        sid = p.stem.split("-")[0]
        song_cfg = {**cfg, **conf.get("songs", {}).get(sid, {}), **(bank if shared else {})}
        samples, programs, drums = shared or sample_set(spec, cfg, [(notes, chan_prog)], last_page, tables, args)
        if not shared:
            print(f"MI{sid}: {len(samples)} samples, DOC {ntp_doc_pages(samples, last_page)}/256 pages "
                  f"(reserve {255 - last_page}); loops {loop_summary(samples, cfg)}")
        grid, patterns, rep = convert_song(p, samples, programs, drums, cfg, song_cfg, cc7)
        if ntp_oscillators([2 if k == "melodic" else 1 for k in rep.track_kinds]) is None:
            raise SystemExit(f"MI{sid}: NTP's converter can't assign oscillators to these tracks")
        (args.out_dir / f"MI{sid}.mod").write_bytes(mod_file(f"MI1 {sid}", samples, grid, patterns, tables))
        reports.append((sid, rep))
    print(f"{'song':<6} {'tempo':>5} {'spd':>3} {'row ms':>6} {'pat':>3} {'trk':>3} {'osc':>3} {'notes':>5} "
          f"{'drop':>4} {'steal':>5} {'tchg':>4} {'delay':>5} {'v:cell':>6} {'v:prev':>6} {'v:dflt':>6} {'err ms':>6}")
    for sid, rep in reports:
        print(f"{sid:<6} {rep.tempo:5d} {rep.speed:3d} {rep.row_ms:6.1f} {rep.patterns:3d} {rep.tracks_used:3d} "
              f"{rep.oscillators:3d} {rep.notes:5d} {rep.dropped:4d} {rep.stolen:5d} {rep.tempo_changes:4d} {rep.delayed:5d} "
              f"{rep.vel_in_cell:6d} {rep.vel_preceding:6d} "
              f"{rep.vel_default:6d} {rep.timing_err_ms:6.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
