# SPDX-License-Identifier: GPL-3.0-or-later
"""NinjaTrackerPlus samples from Munt renders of the sounds in a game's mt32sounds.toml.

Melodic sample = raw MT-32 attack (`attack_bytes`, loop header) + a loop holding k identical
sustain cycles at a rate that makes the loop exactly k periods; note base + finetune make it
play in tune. Short loops are E'+ (loopbuild) or the original averaged cycle; steady sounds
may grow long raw loops into free DOC RAM (plan_loops). Decaying sounds that reach the 8-bit
floor within their attack + loop budget become one-shots; drums are one-shots ending at that
floor. Levels are measured in Munt, not guessed: each sample's peak, its velocity and key
responses, and the MT-32's channel-volume (CC7) curve. Everything is sized and packed the way
NTP packs DOC RAM. See docs/ninjatrackerplus.md and docs/sample-construction.md.
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import resample

from . import audio, docram, docsim, loopbuild
from .audio import smooth
from .munt import SR, Munt

PAL = 7093789.2 / 2                       # Amiga PAL clock / 2: rate = PAL / period (NTP)
STOPPER = 8                               # bytes NTP appends to a non-power-of-two block
NOTES = 72                                # NTP/ProTracker 6-octave table
BLOCK_SIZES = tuple(256 << i for i in range(8))   # 256 B .. 32 KB
MAX_SAMPLES = 31                          # a MOD's sample slots


# ---------------------------------------------------------------------------
# Period tables (ProTracker, extended to 6 octaves, all finetunes) from NTP's make_tables.php

def period_tables(make_tables: Path) -> dict[int, list[int]]:
    """Finetune (-8..7) -> the 72 note periods, read from NTP's make_tables.php."""
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
    kind: str = "melodic"       # "melodic", "drum", or the silent "rest"
    level: float = 1.0          # peak amplitude of the source (Munt, velocity 100, CC7 100) / data peak
    vel_v: np.ndarray = field(default_factory=lambda: np.array([0.0, 127.0]))  # velocity response:
    vel_g: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.27]))   # gain vs velocity 100
    key_v: np.ndarray = field(default_factory=lambda: np.array([0.0]))         # key response:
    key_g: np.ndarray = field(default_factory=lambda: np.array([1.0]))         # gain vs key0
    env_t: np.ndarray = field(default_factory=lambda: np.zeros(1))   # seconds after note-on
    env_g: np.ndarray = field(default_factory=lambda: np.ones(1))    # loudness vs note-on (may exceed 1: swells)
    rel_g: np.ndarray = field(default_factory=lambda: np.zeros(1))   # release, per 10 ms
    seconds: float = 0.0        # one-shot length
    default_volume: int = 48    # set per song by ntpsong.convert_song
    tune_cents: float = 0.0
    steady: bool = False        # sustain holds its level (a raw multi-cycle loop won't pump)
    rate: float = 0.0           # sample rate at key0 (Hz)
    max_loop: int = 512         # longest loop the recording allows (bytes, power of two)
    raw_ok: tuple = ()          # raw (long) loop sizes that pass raw_loop_ok (bytes)
    cycles: int = 0             # source periods in the loop (k)

    def mod_bytes(self) -> bytes:
        return bytes(np.clip(np.round(self.data * 127), -128, 127).astype(np.int8).tobytes())

    def ntp_blocks(self) -> list[int]:
        """DOC bytes this sample takes once NTP prepares it (power-of-two blocks). A loop
        starting at 0 is one block (the converter gives it 1 oscillator)."""
        if self.loop_len and not self.loop_start:
            return [ntp_block(self.loop_len)]
        if self.loop_len:
            return [ntp_block(self.loop_start), ntp_block(self.loop_len)]
        return [ntp_block(len(self.data))]


def ntp_block(n: int) -> int:
    """The DOC block NTP stores n sample bytes in: a power of two from 256 B, after adding
    the stopper bytes unless n is already a block size."""
    n = n if n in BLOCK_SIZES else n + STOPPER
    return 1 << max(8, math.ceil(math.log2(n)))


def best_tuning(freq: float, tables, min_rate, max_rate, loop_bytes, key0, key_lo, key_hi,
                whole_cycles: bool = False, high_within_cents: float | None = None
                ) -> tuple[float, int, float, int, int]:
    """(err_cents, k, rate, base_note, finetune): `loop_bytes` = k periods at `rate`, played
    by note base_note with finetune so that period gives exactly that rate. `whole_cycles`:
    only k that divide loop_bytes (every cycle the same whole number of samples, so the k
    cycles stay identical after 8-bit rounding: no loop-rate buzz). `high_within_cents`: the
    highest rate tuned within this many cents (else the closest tuning). A long loop has
    hundreds of k, and the closest of them is anywhere in the window: Panpipes 16384 B at
    14903 Hz, Elec Organ 8192 B at 9165 Hz, where the rate costs the attack too."""
    ks = np.arange(max(1, math.ceil(loop_bytes * freq / max_rate)), math.floor(loop_bytes * freq / min_rate) + 1)
    if whole_cycles:
        ks = ks[loop_bytes % ks == 0]
    n = np.arange(NOTES)
    valid = (n + key_lo - key0 >= 0) & (n + key_hi - key0 < NOTES)
    if not len(ks) or not valid.any():
        raise ValueError(f"no tuning for {freq:.1f} Hz over keys {key_lo}-{key_hi}")
    fts = list(tables)
    per = np.array([tables[ft] for ft in fts], dtype=np.float64)          # (finetune, note)
    rates = loop_bytes * freq / ks                                        # (k,)
    err = 1200 * np.log2((PAL / per)[None, :, :] / rates[:, None, None])  # (k, finetune, note)
    a = np.where(valid[None, None, :], np.abs(err), np.inf)
    if high_within_cents is not None:
        tuned = np.nonzero(a.min(axis=(1, 2)) <= high_within_cents)[0]
        if len(tuned):                                                # ks ascend: rates descend
            ki = int(tuned[0])
            fi, ni = np.unravel_index(int(np.argmin(a[ki])), a[ki].shape)
            return float(err[ki, fi, ni]), int(ks[ki]), float(rates[ki]), int(ni), fts[fi]
    # first minimum in (k, finetune, note) scan order, as the original nested loops chose
    ki, fi, ni = np.unravel_index(int(np.argmin(a)), a.shape)
    return float(err[ki, fi, ni]), int(ks[ki]), float(rates[ki]), int(ni), fts[fi]


# ---------------------------------------------------------------------------
# Loops

def periodic_loop(x: np.ndarray, start: float, period: float, k: int, out: int, cycles: int = 4) -> np.ndarray:
    """`out` points holding k identical cycles: the average of `cycles` source periods from
    `start`, tiled k times and band-limited. Exactly periodic, so the loop seam is continuous;
    the source's own cycle-to-cycle drift (chorus, vibrato) would otherwise jump at the seam
    and buzz at the loop rate."""
    fine = 1024
    acc = np.zeros(fine)
    for c in range(cycles):
        acc += audio.interp_at(x, start + period * (c + np.arange(fine) / fine))
    loop = resample(np.tile(acc / cycles, k), out)
    # averaging detuned partials loses level: match the source's RMS over the same span
    src = audio.interp_at(x, start + np.arange(fine * cycles) * (period / fine))
    e_src, e_loop = float(np.sqrt(np.mean(src ** 2))), float(np.sqrt(np.mean(loop ** 2)))
    return loop * (e_src / e_loop) if e_loop > 0 else loop


def raw_loop(x: np.ndarray, start: float, period: float, k: int, out: int, xfade: float = 0.25) -> np.ndarray:
    """`out` points holding k source periods from `start`, as recorded (chorus, beating and
    vibrato kept), with a crossfade seam: the last `xfade` of the loop fades into the audio
    just before `start`, so the end flows into the start. Used for long loops of steady
    sounds; short loops use periodic_loop (one averaged cycle, tiled)."""
    M = out * 4
    step = k * period / M
    body = audio.interp_at(x, start + np.arange(M) * step)
    nx = int(M * xfade)
    pre = audio.interp_at(x, start - (nx - np.arange(nx)) * step)
    w = np.linspace(0, 1, nx + 2)[1:-1]
    body[-nx:] = body[-nx:] * (1 - w) + pre * w
    return resample(body, out)


def loop_tremolo_db(loop: np.ndarray, k: int) -> float:
    """Level range (dB) across the k cycles of a loop: the amplitude modulation the loop adds
    every pass. A raw loop shorter than the sound's own wobble (Elec Organ's rotary, ~0.5 s)
    holds a slice of it, and replaying that slice 15 times a second is a tremolo the MT-32
    doesn't have (shared Elec Organ, 1024 B: 6.3 dB at 15.3 Hz)."""
    n = len(loop)
    edges = np.round(np.arange(k + 1) * n / k).astype(int)
    r = [float(np.sqrt(np.mean(loop[edges[i]:edges[i + 1]] ** 2))) for i in range(k) if edges[i + 1] > edges[i]]
    r = np.maximum(np.array(r), 1e-9)
    return float(20 * np.log10(r.max() / r.min())) if len(r) > 1 else 0.0


def loop_buzz_db(loop: np.ndarray, k: int) -> float:
    """Energy off the f0 harmonics in a k-cycle loop's line spectrum (lines at multiples of
    f0/k), relative to the energy on them: cycle-to-cycle change, heard as roughness or buzz at
    the loop rate. An exactly periodic loop: -inf (reported -200)."""
    X = np.abs(np.fft.rfft(loop)) ** 2
    X[0] = 0
    i = np.arange(len(X))
    on, off = float(X[i % k == 0].sum()), float(X[i % k != 0].sum())
    return 10 * math.log10(off / on) if off > 0 and on > 0 else -200.0


def raw_loop_ok(loop: np.ndarray, k: int, rate: float, cfg: dict) -> bool:
    """May a raw loop of this size replace the averaged loop? Only if it repeats so slowly
    (`raw_loop_slow_hz`) that its movement reads as the sound's own wobble, or if it is as
    clean as the averaged loop: no tremolo (`raw_loop_max_tremolo_db`) and no loop-rate buzz
    (`raw_loop_max_buzz_db`). Measured: shared Elec Organ 1024 B had a 6.3 dB tremolo at
    15 Hz, Trombone 1024 B -39 dB of buzz, where the averaged loops have neither."""
    if rate / len(loop) <= cfg.get("raw_loop_slow_hz", 2.0):
        return True
    return (loop_tremolo_db(loop, k) <= cfg.get("raw_loop_max_tremolo_db", 1.0)
            and loop_buzz_db(loop, k) <= cfg.get("raw_loop_max_buzz_db", -50.0))


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
    att_end = a + attack_bytes / rate * SR
    env = audio.rms_envelope(x)
    top = float(np.max(env[a:])) or 1.0
    at = a + sustain_s * SR
    while at > att_end and env[int(at)] < 0.2 * top:
        at -= 0.01 * SR
    at = max(at, att_end)
    loop = (raw_loop if raw else periodic_loop)(x, at, period, k, loop_bytes)
    if raw:
        # the attack moves into phase with the loop (E'+'s join): it ends at whatever phase
        # attack_bytes / rate gives, and a half-cycle-off tail crossfaded in anti-phase
        # (Panpipes 16384 B at 21770 Hz: every rising crossing correlated -0.97, a 13 dB dip)
        attack, loop = loopbuild.join_aligned(x, a, period, rate, k, attack_bytes, loop, xfade_cycles)
        return attack, loop, att_end, at
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
    # The envelope then starts from gain 1 here (_row_envelope eases it to the contour).
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


LOOP_CONSTRUCTIONS = ("eprime", "averaged")


def loop_construction(cfg: dict) -> str:
    """The configured short-loop construction; an unknown name is an error, not a fallback."""
    c = cfg.get("loop_construction", "eprime")
    if c not in LOOP_CONSTRUCTIONS:
        raise ValueError(f"loop_construction = {c!r}: expected one of {', '.join(LOOP_CONSTRUCTIONS)}")
    return c


def eprime_attack_and_loop(m: dict, x: np.ndarray, a: int, period: float, rate: float, k: int,
                           attack_bytes: int, loop_bytes: int, hold: float, render, notes: int = 1
                           ) -> tuple[np.ndarray, np.ndarray, float, float]:
    """E'+ (loop_construction = "eprime", the default; docs/sample-construction.md, code in loopbuild):
    - one cycle with each harmonic at its RMS level over the sustain (power-averaged: beating
      partials keep their mean power), phases from the period where the attack ends;
    - tiled k times with whole samples per cycle (k | loop_bytes: see best_tuning), and an
      exact band-limited zero crossing at loop[0], so it is exactly periodic after 8-bit rounding;
    - the attack's source start moved earlier by up to one period so it ends in phase with
      loop[0]: the crossfade joins aligned waveforms (no level dip at the handover).
    The sustain span comes from the attack detection on a 6 s render (identical to the 2 s one
    up to note-off). Same return as attack_and_loop."""
    snd = loopbuild.analyse(m, render, build_hold=hold)
    att_end = a + attack_bytes / rate * SR
    lo = max(snd.lo, att_end)
    hi = max(snd.hi, lo + k * period + 8 * period)
    loop = loopbuild.loop_power(snd.x, lo, hi, period, k, loop_bytes, at=att_end, notes=notes)
    attack, loop = loopbuild.join_aligned(x, a, period, rate, k, attack_bytes, loop)
    return attack, loop, att_end, lo


def drum_rate(seg: np.ndarray, nbytes: int, lo: float, hi: float) -> float:
    """Sample rate for a one-shot of `nbytes`: fast enough for the sound's bandwidth (2.2 x the
    frequency below which 95% of its energy lies), then lowered, by at most half, if the bytes
    can't hold 95% of its energy at that rate. Filling the bytes with the whole -46 dB tail
    instead put hats and shakers (energy ~14 kHz) at 6 kHz, which made them all but silent."""
    P = np.abs(np.fft.rfft(seg)) ** 2
    c = np.cumsum(P) / (P.sum() or 1.0)
    f95 = float(np.fft.rfftfreq(len(seg), 1 / SR)[min(np.searchsorted(c, 0.95), len(c) - 1)])
    bw = min(hi, max(lo, 2.2 * f95))
    e = np.cumsum(seg ** 2) / (float(np.sum(seg ** 2)) or 1.0)
    d95 = max(int(np.searchsorted(e, 0.95)), 1) / SR
    fits = nbytes / d95                                  # rate at which the bytes hold 95%
    return float(min(hi, max(lo, bw if fits >= bw else max(fits, bw / 2))))


# ---------------------------------------------------------------------------
# Levels, measured in Munt

VEL_POINTS = (20, 40, 64, 80, 100, 127)
CC7_POINTS = (0, 10, 20, 32, 40, 52, 64, 77, 90, 100, 110, 120, 127)
_PEAKS: dict[int, float] = {}


@functools.lru_cache(maxsize=None)
def cached_render(munt: Munt, program: int | None, key: int, hold: float, velocity: int = 100,
                  volume: int = 100) -> np.ndarray:
    """munt.render, cached in-process (read-only arrays): each sound's velocity and key
    responses were re-measured from the same WAVs for every song."""
    x = munt.render(program=program, key=key, hold=hold, velocity=velocity, volume=volume)
    x.flags.writeable = False
    return x


def _peak(x: np.ndarray) -> float:
    """Peak 5 ms RMS (memoised for cached renders, which cached_render keeps alive)."""
    if not x.flags.writeable and id(x) in _PEAKS:
        return _PEAKS[id(x)]
    v = float(np.max(audio.rms_envelope(x, 0.005)))
    if not x.flags.writeable:
        _PEAKS[id(x)] = v
    return v


def velocity_response(render_at: Callable[[int], np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Peak level vs velocity, relative to velocity 100, measured from renders (the MT-32's
    velocity sensitivity differs per timbre: closed hat x2.5 at 127, bass x1.4)."""
    ref = _peak(render_at(100)) or 1.0
    return (np.array([0.0, *VEL_POINTS], float),
            np.array([0.0, *(_peak(render_at(v)) / ref for v in VEL_POINTS)]))


def key_response(render_at: Callable[[int], np.ndarray], key0: int, lo: int, hi: int
                 ) -> tuple[np.ndarray, np.ndarray]:
    """Peak level vs key, relative to the sampled key `key0`: MT-32 timbres change level with
    key (TVA key follow, partial balance), so one sample's level doesn't fit its whole range."""
    keys = sorted({lo, hi, key0, *range(lo, hi, 12)})
    ref = _peak(render_at(key0)) or 1.0
    return np.array(keys, float), np.array([_peak(render_at(k)) / ref for k in keys])


def cc7_curve(munt: Munt, program: int = 50, key: int = 67) -> tuple[np.ndarray, np.ndarray]:
    """The MT-32 part-volume (CC7) curve, gain relative to CC7 100, measured in Munt. It is
    steep (about 0.35 at 64, 0.11 at 40) and the same on melodic and rhythm parts."""
    lv = [_peak(cached_render(munt, program, key, 0.5, volume=v)) for v in CC7_POINTS]
    ref = lv[CC7_POINTS.index(100)] or 1.0
    return np.array(CC7_POINTS, float), np.array(lv) / ref


# ---------------------------------------------------------------------------
# Envelopes and lengths

def sustain_contour(env: np.ndarray, held: int, win_s: float) -> np.ndarray:
    """The envelope's slow contour (log-domain moving average over `win_s`) up to sample
    `held` (the note-off), smoothed over the held part only: a window reaching past note-off
    averaged the release in and pulled the last 0.4 s of the contour down (Elec Organ
    -14.5 dB, Strings -9.3, Chorale -4.0 at 1.95 s), and envelopes hold their last value, so
    every steady note held past ~1.7 s played that much too quiet. Near `held` the window
    shrinks (`smooth`'s edge handling)."""
    out = np.exp(smooth(np.log(np.maximum(env[:held], 1e-6)), int(win_s * SR)))
    return np.concatenate([out, np.full(max(0, len(env) - held), out[-1] if len(out) else 1.0)])


def is_steady(env: np.ndarray, a: int, off: int, sustain_s: float, max_drop_db: float = 6.0) -> bool:
    """True if the sound holds its level from the sustain start to note-off (organs, strings,
    winds). Decaying sounds (marimba, bass, vibes) keep the short averaged loop: a raw
    multi-cycle loop would bake the decay in and pump on every pass.

    Judged by the level's trend (least-squares line through the dB envelope), not its range:
    the MT-32 organ's rotary wobble swings +-10 dB at every key, so a range test passed or
    failed by where the wobble fell (Elec Organ steady at keys 60-64, not at 40-58). Measured
    trend drops: decaying sounds 10-50 dB, steady ones within +-5 dB, at every key +-12."""
    i0, i1 = a + int(sustain_s * SR), off - int(0.15 * SR)
    if i1 - i0 < 2:
        return False
    y = 20 * np.log10(np.maximum(env[i0:i1], 1e-9))
    t = np.arange(len(y)) / SR
    return -float(np.polyfit(t, y, 1)[0]) * (t[-1] - t[0]) <= max_drop_db


def _floor_envelope(x: np.ndarray, a: int, off: int) -> np.ndarray:
    """20 ms RMS of x[a:off], relative to its peak sample."""
    seg = x[a:off]
    pk = float(np.max(np.abs(seg))) or 1.0
    return np.sqrt(np.maximum(smooth(seg * seg, int(0.02 * SR)), 0.0)) / pk


def floor_time(x: np.ndarray, a: int, off: int, floor_lsb: float = 1.5) -> float:
    """Seconds after the onset until the sound stays below `floor_lsb` RMS (20 ms) in a sample
    normalised to its peak, i.e. below what 8-bit DOC samples can carry (1.5 LSB of 127:
    -38.5 dB re peak). `off - a` (the whole note) if it never gets there before note-off."""
    above = np.nonzero(_floor_envelope(x, a, off) > floor_lsb / 127)[0]
    return (above[-1] + 1) / SR if len(above) else 0.0


def drum_end(x: np.ndarray, a: int, off: int, floor_lsb: float = 1.5, junk: float = 2.0) -> float:
    """Seconds after the onset where a drum's decay first falls below `floor_lsb` (as in
    floor_time) after its last point above `junk` x the floor. Late low-level junk that
    hovers around the floor (the MT-32 snare has ~1 KB of it at 1-1.7 LSB after a clean
    decay) doesn't hold the end back, as it would with floor_time's last crossing."""
    env = _floor_envelope(x, a, off)
    loud = np.nonzero(env > junk * floor_lsb / 127)[0]
    if not len(loud):
        return floor_time(x, a, off, floor_lsb)
    below = np.nonzero(env[loud[-1]:] <= floor_lsb / 127)[0]
    return (loud[-1] + (below[0] if len(below) else len(env) - loud[-1])) / SR


def one_shot_tuning(t_floor: float, rate: float, budget_pages: int, tables, key0: int, key_lo: int,
                    key_hi: int, min_rate: float, max_drop: float = 0.1) -> tuple[float, int, int, int] | None:
    """Can a decaying sound be a one-shot instead of attack + loop? It can when the whole sound,
    up to the 8-bit floor (`t_floor` s), fits in one DOC block no larger than the attack + loop
    blocks (`budget_pages`), at a rate no more than `max_drop` below the looped sample's rate.
    Returns (rate, bytes, base_note, finetune): the highest exact table rate that fits (a
    one-shot has no loop to fit, so it plays exactly in tune), or None."""
    block = 1 << int(math.log2(budget_pages * 256))
    floor_rate = max(min_rate, rate * (1 - max_drop))
    cap = min(rate, (block - STOPPER) / max(t_floor, 1e-6))
    if cap < floor_rate:
        return None
    best = None
    for ft, per in tables.items():
        for i in range(NOTES):
            if not (0 <= i + key_lo - key0 and i + key_hi - key0 < NOTES):
                continue
            r = PAL / per[i]
            if r <= cap and (best is None or r > best[0]):
                best = (r, i, ft)
    if best is None or best[0] < floor_rate:
        return None
    r, i, ft = best
    return r, min(block - STOPPER, int(math.ceil(t_floor * r))), i, ft


def _faded(seg: np.ndarray, frac_div: int) -> np.ndarray:
    """seg with a linear fade-out over its last 1/frac_div."""
    fade = np.ones(len(seg))
    k = max(1, len(seg) // frac_div)
    fade[-k:] = np.linspace(1, 0, k)
    return seg * fade


# ---------------------------------------------------------------------------
# Building the samples

def plan_loops(samples: list[ModSample], usage: dict[int, float], last_page: int,
               max_loop: int) -> dict[int, int]:
    """Loop size per melodic sample index, spending the song's free DOC RAM (pages up to
    `last_page`) on its steady sounds: repeatedly double the loop with the most sustained
    playing time per byte, as long as everything still packs the way NTP packs it. A loop only
    grows to sizes in its `raw_ok` (raw loops that add no tremolo), skipping the others."""
    sizes = {i: s.loop_len for i, s in enumerate(samples) if s.kind == "melodic" and s.loop_len}
    grow = {i for i, s in enumerate(samples) if s.kind == "melodic" and s.steady and usage.get(i, 0) > 0}

    def fits(sz: dict[int, int]) -> bool:
        blocks = []
        for i, s in enumerate(samples):
            b = s.ntp_blocks()
            if i in sz:
                b = [sz[i]] if not s.loop_start else [b[0], sz[i]]
            blocks += b
        try:
            docram.allocate([docram.Wave(f"b{j}", bytes(b)) for j, b in enumerate(blocks)], 0, last_page)
            return True
        except ValueError:
            return False

    while grow:
        i = max(grow, key=lambda j: usage[j] / sizes[j])
        nxt = [z for z in samples[i].raw_ok if z > sizes[i] and z <= min(max_loop, samples[i].max_loop)]
        if not nxt:
            grow.discard(i)
            continue
        trial = {**sizes, i: min(nxt)}
        if fits(trial):
            sizes = trial
        else:
            grow.discard(i)
    return sizes


@dataclass
class _Source:
    """A melodic sound's render and what the build measures on it."""
    x: np.ndarray
    a: int                  # onset sample
    off: int                # note-off sample (onset + hold: smf2wav drops the leading silence)
    env: np.ndarray         # 10 ms RMS envelope
    period: float           # samples: the loop's cycle, `notes` note periods
    keys: tuple[int, int]   # lowest and highest key it plays (incl. the sampled key)
    notes: int = 1          # note periods per cycle (2: a sub-octave partial, loop_notes)

    @property
    def freq(self) -> float:
        return SR / self.period


def rate_window(m: dict, cfg: dict, loop_bytes: int) -> tuple[float, float]:
    """(min, max) sample rate for a melodic sound: the config's window, or for a short loop +-3 %
    around the rate the pitch plan chose for it (`ntp_rate`, pitchplan). Raw loops keep the
    config's window: the planned rate is a short loop's, and a raw loop's length in seconds
    (and so whether it passes raw_loop_ok) depends on its own rate."""
    if "ntp_rate" in m and loop_bytes <= cfg["loop_bytes"]:
        return m["ntp_rate"] * 0.97, m["ntp_rate"] * 1.03
    return cfg["min_rate"], cfg["max_rate"]


def _tune_loop(m: dict, src: _Source, cfg: dict, tables, steady: bool, sustain_s: float, eprime: bool
               ) -> tuple[int, tuple[float, int, float, int, int]]:
    """(loop bytes, best_tuning result): a steady sound's planned loop (`ntp_loop_bytes`),
    halved until it fits in the recording between the sustain start and note-off."""
    loop_bytes = m.get("ntp_loop_bytes", cfg["loop_bytes"]) if steady else cfg["loop_bytes"]
    att_bytes = m.get("ntp_attack_bytes", cfg["attack_bytes"])
    while loop_bytes > cfg["loop_bytes"]:
        tuning = _raw_tuning(m, src, cfg, tables, loop_bytes, att_bytes, sustain_s)
        if tuning is not None:
            return loop_bytes, tuning
        loop_bytes //= 2
    return loop_bytes, best_tuning(src.freq, tables, *rate_window(m, cfg, loop_bytes), loop_bytes, m["key"],
                                   *src.keys, whole_cycles=eprime)


def _raw_tuning(m: dict, src: _Source, cfg: dict, tables, z: int, att_bytes: int, sustain_s: float
                ) -> tuple[float, int, float, int, int] | None:
    """best_tuning for a raw loop of z bytes, or None if none fits the recording and passes
    raw_loop_ok. The rate is the highest tuned within `raw_loop_tune_cents` (the rate is the
    attack's too); if the loop isn't clean there, the highest so tuned at which it repeats
    slowly enough (`raw_loop_slow_hz`) to read as the sound's own movement, and never more
    than `raw_loop_max_rate_drop` below the short loop's rate. Panpipes 16384 B:
    14903 -> 20621 Hz, -24.8 -> -27.6 dB (docs/ninjatrackerplus.md "Raw-loop rate")."""
    lo, hi = rate_window(m, cfg, z)
    # nor much below the short loop's rate: the measure prefers the short loop's higher rate
    # to a raw loop's movement (Sax 512 B at 18891 Hz -21.7 dB, 8192 B at 16338 Hz -20.3 dB)
    short = best_tuning(src.freq, tables, *rate_window(m, cfg, cfg["loop_bytes"]), cfg["loop_bytes"], m["key"],
                        *src.keys, whole_cycles=loop_construction(cfg) == "eprime")[2]
    lo = max(lo, short * (1 - cfg.get("raw_loop_max_rate_drop", 0.1)))
    tol = cfg.get("raw_loop_tune_cents", 1.0) or None
    tops = [hi] if tol is None else [hi, min(hi, z * cfg.get("raw_loop_slow_hz", 2.0))]
    for top in dict.fromkeys(tops):
        if top < lo:
            continue
        try:
            t = best_tuning(src.freq, tables, lo, top, z, m["key"], *src.keys, high_within_cents=tol)
        except ValueError:              # no whole number of periods tunes in this window: no fit
            continue
        _, k, r, _, _ = t
        if src.a + (sustain_s + z / r + 0.05) * SR > src.off:
            continue
        _, loop, _, _ = attack_and_loop(src.x, src.a, src.period, r, k, att_bytes, z, sustain_s,
                                        cfg.get("attack_xfade_cycles", 4.0), raw=True)
        if raw_loop_ok(loop, k, r, cfg):
            return t
    return None


def _row_envelope(src: _Source, att_end: float, e_loop: float, steady: bool, loop_bytes: int, rate: float,
                  cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """(t, gain) per 10 ms from the attack's end to note-off: the loudness contour relative to
    the loop's own level, for the per-row Cxx.

    Smoothed in the log domain over 250 ms: MT-32 organs/strings have tremolo and partial
    beating (Pipe Org 2: gain 0.7-2.3, ~4 Hz) that the static loop can't follow and that
    per-row Cxx would alias into random jumps; exponential decays stay unbiased in log.
    Steady sounds: only the slow contour. Their beating/chorus (Chorale: -1..-8 dB at
    2.5 Hz) as per-row Cxx is a stepped tremolo on a static loop (choppy), and a raw loop
    already contains it (applying it again doubles it, out of phase). Smooth over 0.8 s,
    and at least the raw loop's own length. Decaying sounds keep 250 ms for their decays."""
    held = src.off - int(0.02 * SR)          # less 20 ms for the onset threshold and RMS window
    t = np.arange(int(att_end), held, int(0.01 * SR))
    win = 0.25
    if steady:
        win = max(cfg.get("env_smooth_steady_s", 0.8), loop_bytes / rate if loop_bytes > cfg["loop_bytes"] else 0.0)
    g = sustain_contour(src.env, held, min(win, 1.5))[t] / e_loop
    # the loop continues the attack's tail at gain 1; ease to the contour over 150 ms
    # (geometric: linear in dB) instead of jumping to it at the first loop row
    w = np.clip((t - att_end) / (cfg.get("handover_s", 0.15) * SR), 0.0, 1.0)
    return t, g ** w


def _raw_loop_sizes(m: dict, src: _Source, cfg: dict, tables, att_bytes: int, sustain_s: float,
                    max_loop: int) -> tuple[int, ...]:
    """Raw loop sizes a steady sound may grow to: each is built and measured (raw_loop_ok)."""
    ok = []
    z = cfg["loop_bytes"] * 2
    while z <= min(cfg.get("max_loop_bytes", 16384), max_loop):
        if _raw_tuning(m, src, cfg, tables, z, att_bytes, sustain_s) is not None:
            ok.append(z)
        z *= 2
    return tuple(ok)


def loop_notes(x: np.ndarray, a: int, off: int, period: float, sustain_s: float, cfg: dict) -> int:
    """Note periods a loop cycle spans: 2 when the sustain has a sub-octave partial
    (loopbuild.sub_octave at least `sub_octave_min_share`, while the note's own harmonics hold
    most of the power: not an inharmonic sound like Taiko), else 1."""
    lo, hi = a + int(sustain_s * SR), off - int(0.1 * SR)
    if hi - lo < 16 * period:
        return 1
    share = loopbuild.sub_octave(x, lo, hi, period)
    return 2 if cfg.get("sub_octave_min_share", 0.02) <= share < 0.5 else 1


def build_melodic(m: dict, cfg: dict, keys: tuple[int, int], munt: Munt, tables, hold: float
                  ) -> tuple[ModSample, str]:
    """A melodic sample (attack + loop, or a one-shot) and its build-log line."""
    x = cached_render(munt, m["program"], m["key"], hold)
    a = audio.onset(x)
    if len(x) < a + int((hold + 0.1) * SR):
        # smf2wav drops trailing silence: a sound that ends before its note-off (the MT-32's
        # effect presets 118-127, which MI2 plays) comes back shorter than the note
        x = np.pad(x, (0, a + int((hold + 0.5) * SR) - len(x)))
    f0 = 440 * 2 ** ((m["key"] - 69) / 12)
    sustain_s = cfg.get("sustain_s", 0.4)
    period = audio.refine_period(x, a + int(0.25 * SR), f0)
    notes = loop_notes(x, a, a + int(hold * SR), period, sustain_s, cfg)
    src = _Source(x, a, a + int(hold * SR), audio.rms_envelope(x), period * notes,
                  (min(keys[0], m["key"]), max(keys[1], m["key"])), notes)
    steady = is_steady(src.env, a, src.off, sustain_s)
    # short loops: "eprime" (default) = E'+ (docs/sample-construction.md); "averaged" = the
    # original averaged cycle (attack_and_loop), fractional samples per cycle
    eprime = loop_construction(cfg) == "eprime"
    loop_bytes, (err, k, rate, n0, ft) = _tune_loop(m, src, cfg, tables, steady, sustain_s, eprime)
    # attack = source [a, a + attack_bytes/rate); loop = k periods from the sustain
    att_bytes = m.get("ntp_attack_bytes", cfg["attack_bytes"])
    if eprime and loop_bytes <= cfg["loop_bytes"]:
        attack, loop, att_end, _ = eprime_attack_and_loop(
            m, x, a, src.period, rate, k, att_bytes, loop_bytes, hold,
            lambda p, kk, h: cached_render(munt, p, kk, h), src.notes)
    else:
        attack, loop, att_end, _ = attack_and_loop(x, a, src.period, rate, k, att_bytes, loop_bytes, sustain_s,
                                                   cfg.get("attack_xfade_cycles", 4.0),
                                                   raw=loop_bytes > cfg["loop_bytes"])
    e_loop = float(np.sqrt(np.mean(loop ** 2))) or 1e-9
    t, g = _row_envelope(src, att_end, e_loop, steady, loop_bytes, rate, cfg)
    g_max = float(g.max()) if len(g) else 1.0
    rel = src.env[src.off:src.off + int(cfg["release_max"] * SR):int(0.01 * SR)]
    rel_g = np.clip(rel / max(src.env[src.off], 1e-9), 0, 1)
    data = np.concatenate([attack, loop])
    peak = float(np.max(np.abs(data))) or 1.0
    s = ModSample(m["name"], data / peak, loop_start=att_bytes, loop_len=loop_bytes,
                  finetune=ft, base_note=n0, key0=m["key"], kind="melodic",
                  env_t=(t - a) / SR, env_g=g, rel_g=rel_g, tune_cents=err, level=peak)
    s.steady, s.rate, s.cycles = steady, rate, k
    # at the window's top rate (raw loops tune high; _raw_tuning checks each size's fit)
    top = max(rate, rate_window(m, cfg, 2 * cfg["loop_bytes"])[1])
    s.max_loop = 1 << int(math.log2(max(1.0, ((src.off - a) / SR - sustain_s - 0.05) * top)))
    s.max_loop = s.max_loop if steady else cfg["loop_bytes"]
    if steady:
        s.raw_ok = _raw_loop_sizes(m, src, cfg, tables, att_bytes, sustain_s, s.max_loop)
    lo, hi = keys
    line = (f"  {m['name']:<12} loop {loop_bytes:5d} B{' raw' if loop_bytes > cfg['loop_bytes'] else '    '} "
            f"k={k:3d}{' x2 (sub-octave)' if src.notes == 2 else ''} rate {rate:7.0f} Hz note {n0:2d} ft {ft:+d} "
            f"err {err:+5.1f} cents, swell x{g_max:.2f}, keys {lo}-{hi}, "
            f"tremolo {loop_tremolo_db(loop, k):.1f} dB @ {rate / loop_bytes:.1f} Hz")
    # One-shot instead? A decaying sound that falls below the 8-bit floor while its attack
    # still plays only loops quantisation noise (Xylophone: 2.6 LSB RMS). If the whole
    # sound fits in no more DOC RAM than attack + loop, store it whole: it frees the loop's
    # oscillator and plays exactly in tune.
    if not steady and cfg.get("one_shots", True):
        t_floor = floor_time(x, a, src.off, cfg.get("one_shot_floor_lsb", 1.5))
        # the pitch plan's one-shot rate (ntp_shot_rate) if it has one, else the looped rate
        shot = one_shot_tuning(t_floor, m.get("ntp_shot_rate", rate), sum(s.ntp_blocks()) // 256, tables,
                               m["key"], *src.keys, cfg["min_rate"], cfg.get("one_shot_max_rate_drop", 0.1))
        if shot is not None:
            r1, nb, n0, ft = shot
            data = resample(_faded(x[a:a + int(round(nb / r1 * SR))], 20), nb)
            peak = float(np.max(np.abs(data))) or 1.0
            s = ModSample(m["name"], data / peak, finetune=ft, base_note=n0, key0=m["key"], kind="melodic",
                          env_t=np.zeros(1), env_g=np.ones(1), rel_g=rel_g,
                          seconds=nb / r1, tune_cents=0.0, level=peak)
            s.rate, s.max_loop = r1, 0
            line = (f"  {m['name']:<12} one-shot {len(s.data):5d} B @ {r1:6.0f} Hz ({s.seconds * 1000:4.0f} ms to "
                    f"the 8-bit floor) note {n0:2d} ft {ft:+d}, keys {lo}-{hi}")
    s.vel_v, s.vel_g = velocity_response(lambda v: cached_render(munt, m["program"], m["key"], 0.5, velocity=v))
    s.key_v, s.key_g = key_response(lambda kk: cached_render(munt, m["program"], kk, 0.5), m["key"], *src.keys)
    return s, line


def build_drum(d: dict, cfg: dict, munt: Munt, tables) -> tuple[ModSample, dict[int, tuple[int, float]], str]:
    """A drum one-shot, the rhythm keys it covers (key -> (semitone offset, level gain)), and its
    build-log line."""
    x = cached_render(munt, None, d["key"], 0.5)
    a = audio.onset(x)
    env = audio.rms_envelope(x, 0.005)
    tail = np.nonzero(env[a:] > 0.005 * np.max(env))[0]
    dur = (tail[-1] + 1) / SR if len(tail) else 0.1
    # End at the 8-bit floor, as melodic one-shots do: the -46 dB tail above was mostly
    # silence once stored (bongo 80%, mute conga 65%, snare 39% under 1.5 LSB) and held
    # the drum's track busy for it. Junk hovering near the floor after the decay is cut
    # too (drum_end). A floor of 0 keeps the old length.
    floor = cfg.get("drum_floor_lsb", cfg.get("one_shot_floor_lsb", 1.5))
    if floor:
        dur = min(dur, max(drum_end(x, a, a + int(dur * SR), floor), 0.01))
    budget = d.get("ntp_bytes", d["bytes"])          # ntp_bytes: a block the value budget chose
    lo_rate, hi_rate = cfg.get("drum_min_rate", 6000), cfg.get("drum_max_rate", 26000)
    want = drum_rate(x[a:a + int(dur * SR)], budget - STOPPER, lo_rate, hi_rate)
    renders = {c: x if c == d["key"] else cached_render(munt, None, c, 0.5) for c in d["covers"]}
    offs = {c: 0 if c == d["key"] else audio.pitch_offset(x, y) for c, y in renders.items()}
    lo_off, hi_off = min(offs.values()), max(offs.values())
    notes = range(-lo_off, NOTES - hi_off)
    fill = False
    if cfg.get("drum_fill_block"):
        # The DOC stores a one-shot in a power-of-two block: take the highest rate at which the
        # whole drum still fits the block it takes (or the one the budget gave it). The
        # bandwidth rule follows where the energy is, which for drums is mostly bass: Kick
        # and six others sat at 5872 Hz (Nyquist 2.9 kHz, drop-sample images) in a block
        # they half filled (docs/ninjatrackerplus.md "Drums").
        block = d.get("ntp_bytes") or ntp_block(min(budget - STOPPER, int(dur * want)))
        top = min(hi_rate, (block - STOPPER) / dur)
        if top > want:
            budget, want, fill = block, top, True
    if fill:      # the highest period-table rate at or below `want`: the drum must fit
        n0 = max((n for n in notes if PAL / tables[0][n] <= want), key=lambda n: PAL / tables[0][n],
                 default=min(notes, key=lambda n: abs(PAL / tables[0][n] - want)))
    else:         # the note whose finetune-0 period gives a rate closest to `want`
        n0 = min(notes, key=lambda n: abs(PAL / tables[0][n] - want))
    rate = PAL / tables[0][n0]
    seg = x[a:a + int(min(dur, (budget - STOPPER) / rate) * SR)]
    data = resample(_faded(seg, 10), max(2, int(len(seg) / SR * rate) // 2 * 2))
    peak = float(np.max(np.abs(data))) or 1.0
    s = ModSample(d["name"], data / peak, kind="drum", base_note=n0, key0=d["key"],
                  seconds=len(data) / rate, level=peak)
    s.rate = rate
    s.vel_v, s.vel_g = velocity_response(lambda v: cached_render(munt, None, d["key"], 0.5, velocity=v))
    ref = _peak(x) or 1.0                            # each rhythm key has its own MT-32 level
    covers = {c: (o, 1.0 if c == d["key"] else _peak(renders[c]) / ref) for c, o in offs.items()}
    line = (f"  {d['name']:<12} {len(data)} B @ {rate:6.0f} Hz ({len(data) / rate * 1000:4.0f} ms) note {n0:2d}, "
            f"covers {offs}")
    return s, covers, line


def drum_steps(d: dict, cfg: dict, munt: Munt, tables, hits: dict[int, float]
               ) -> list[tuple[int, float, float]]:
    """The blocks a drum can take with `drum_fill_block` (its own first, then each doubling
    until the rate reaches drum_max_rate) as (bytes, error dB, stored seconds). The error is
    docsim.drum_error_db against Munt, averaged over the keys it covers (each played at its
    offset) weighted by `hits` (rhythm key -> how often it's struck)."""
    cfg = {**cfg, "drum_fill_block": True}
    out = []
    block = None
    while True:
        s, covers, _ = build_drum({**d, "ntp_bytes": block} if block else d, cfg, munt, tables)
        size = ntp_block(len(s.data))
        if out and size <= out[-1][0]:
            break                                       # no bigger block helps (top rate, whole drum)
        ws = {c: hits.get(c, 0.0) for c in covers} if any(hits.get(c) for c in covers) else {c: 1.0 for c in covers}
        err = sum(w * docsim.drum_error_db(_onset_cut(cached_render(munt, None, c, 0.5)), s.data, s.rate, off,
                                           s.seconds)
                  for c, (off, _) in covers.items() if (w := ws[c])) / sum(ws.values())
        out.append((size, err, s.seconds))
        block = 2 * size
    return out


def _onset_cut(x: np.ndarray) -> np.ndarray:
    return x[audio.onset(x):]


def rest_sample() -> ModSample:
    """A silent looped sample ("rest") that ends each release on a 2-oscillator track once it
    reaches volume 0.

    NinjaTrackerPlus applies a cell's instrument number (and its default volume) at tick 0
    but delays only the note under EDx, so the next delayed note on the track turned the
    previous note's loop, still running silently after C00, back up for 1-2 ticks: a ghost of
    the old note, then an abrupt cut (confirmed in GSSquared's DOC registers; desktop players
    delay the instrument too). The rest replaces that loop with silence.

    It is looped (a 248 B header + a 256 B loop, both silent), so NTP treats it as a
    2-oscillator instrument: at a delayed note's tick 0 the player writes the new default
    volume to both oscillators (`talk_to_doc_2osc`). The first rest was a one-shot, and the
    player wrote only the attack oscillator's volume (`talk_to_doc_1osc`). The loop oscillator
    kept the old release level, so after the attack the new note's loop played at it until
    the next row (MI153: 120 -> 28 for 65 ms, a flicker). 1-oscillator tracks never get the
    rest: it would make them 2-oscillator. MOD 0 = DOC 128 = silence (DOC 0 would stop at once)."""
    return ModSample("rest", np.zeros(248 + 256), kind="rest", loop_start=248, loop_len=256,
                     base_note=24, key0=60, default_volume=0)


def build_samples(spec: dict, cfg: dict, key_ranges: dict[int, tuple[int, int]], munt: Munt, tables,
                  log=print) -> tuple[list[ModSample], dict, dict]:
    """Samples per the game's mt32sounds.toml; returns (samples, program -> index (or key-split
    list of (lo, hi, index)), rhythm key -> (index, note, level gain))."""
    hold = spec.get("hold_seconds", 2.0)
    samples: list[ModSample] = []
    programs: dict = {}
    drums: dict[int, tuple[int, int, float]] = {}
    for m in spec["melodic"]:
        s, line = build_melodic(m, cfg, key_ranges.get(m["slot"], (m["key"], m["key"])), munt, tables, hold)
        idx = len(samples)
        samples.append(s)
        for p in m["covers"]:
            if "keys" in m:                          # key-split: (lo, hi, sample) per range
                programs.setdefault(p, []).append((*m["keys"], idx))
            else:
                programs[p] = idx
        log(line)
    for d in spec["drum"]:
        s, covers, line = build_drum(d, cfg, munt, tables)
        idx = len(samples)
        samples.append(s)
        for c, (off, gain) in covers.items():
            drums[c] = (idx, s.base_note + off, gain)
        log(line)
    if cfg.get("halt_released_loops", True):
        samples.append(rest_sample())
    if len(samples) > MAX_SAMPLES:
        raise ValueError(f"{len(samples)} samples > {MAX_SAMPLES} (MOD limit)")
    return samples, programs, drums


def ntp_doc_pages(samples: list[ModSample], last_page: int = 255) -> int:
    """DOC pages NTP needs for these samples (aligned blocks, packed largest first); raises
    ValueError if they don't fit below `last_page`."""
    blocks = sorted((b for s in samples for b in s.ntp_blocks()), reverse=True)
    docram.allocate([docram.Wave(f"b{i}", bytes(b)) for i, b in enumerate(blocks)], 0, last_page)
    return sum(b // 256 for b in blocks)


def loop_summary(samples: list[ModSample], cfg: dict) -> str:
    """name:loop bytes per melodic sample ('1shot' for one-shots, 'r' for raw loops)."""
    return " ".join(f"{s.name}:{s.loop_len or '1shot'}{'r' if s.loop_len > cfg['loop_bytes'] else ''}"
                    for s in samples if s.kind == "melodic")


def loop_problems(samples: list[ModSample], cfg: dict) -> list[str]:
    """Loops that would add a tremolo or a loop-rate buzz (raw_loop_ok on the stored loop:
    averaged loops are exactly periodic and pass; raw ones must repeat slowly or be as clean)."""
    out = []
    for s in samples:
        if s.kind == "melodic" and s.loop_len and s.cycles:
            lp = s.data[s.loop_start:s.loop_start + s.loop_len]
            if not raw_loop_ok(lp, s.cycles, s.rate, cfg):
                out.append(f"{s.name} {s.loop_len} B: tremolo {loop_tremolo_db(lp, s.cycles):.1f} dB, "
                           f"buzz {loop_buzz_db(lp, s.cycles):.0f} dB at {s.rate / s.loop_len:.1f} Hz")
    return out
