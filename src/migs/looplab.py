# SPDX-License-Identifier: GPL-3.0-or-later
"""Loop lab: measure and compare ways to build the attack + sustain-loop samples of the NTP
bank from the Munt renders of the MT-32 sounds (config/mt32bank.toml, config/ntp.toml).

Research harness, not part of the build. See docs/loop-detection.md. The construction the
build uses (E'+: attack detection, power-averaged loop, aligned join) lives in loopbuild.

- Attack detection: a pitch-synchronous STFT (window = an integer number of periods, so
  harmonic h sits exactly on bin n*h) gives one amplitude track per harmonic. A sound has
  "settled" when its level and its spectral *shape* (harmonic levels relative to the total)
  stop changing; periodic wobble (chorus, beating, tremolo) is smoothed over its own period
  first, so it counts as steady rather than as a transient.
- Loop metrics: loop-rate buzz (energy off the f0 harmonics in the loop's line spectrum),
  seam step, seam spectral flux, attack->loop handover step, likeness to the source's
  sustain, DOC cost.
- Loop candidates: today's builders plus searched / constructed alternatives.

Writes <out>/report.md, a listening WAV per sound (<out>/wav/) and plots (<out>/plots/).
"""

from __future__ import annotations

import argparse
import math
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample, resample_poly

from . import audio, ntpmod, ntpsamples as smp
from .audio import interp_at
from .loopbuild import (Settle, Sound, Tracks, analyse, cycle_at_rate,  # noqa: F401  (the lab's toolkit)
                        harmonic_cycle, harmonic_tracks, join_aligned, loop_power,
                        modulation_period, settle, zero_start_exact)
from .munt import SR, Munt
from .ntpsamples import loop_buzz_db, loop_tremolo_db  # noqa: F401  (metrics the build gates loops with)
from .ntpsong import read_notes

DOC_RATE = 894886 / 34              # DOC output rate with 32 oscillators enabled (doc_tuning rounds it)
ATTACK_SIZES = (504, 1016, 2040, 4088)             # + 8 stopper bytes = one 512 B..4 KB block


def tune(freq: float, tables: dict, min_rate: float, max_rate: float, loop_bytes: int, key0: int,
         key_lo: int, key_hi: int, whole_cycles: bool = False) -> tuple[float, int, float, int, int]:
    """The build's tuning (ntpsamples.best_tuning): (err_cents, k, rate, base_note, finetune) for k periods
    in `loop_bytes`. `whole_cycles`: only k that divide loop_bytes, so the k cycles stay
    identical after 8-bit rounding."""
    return smp.best_tuning(freq, tables, min_rate, max_rate, loop_bytes, key0, key_lo, key_hi, whole_cycles)


# ---------------------------------------------------------------------------
# Loop builders: (x, sustain span [lo, hi) in source samples, period, k, out bytes) -> loop

def loop_snapshot(x, lo, hi, period, k, out, at=None):
    """A: today's averaged-cycle loop (ntpsamples.periodic_loop), taken at `at`."""
    return smp.periodic_loop(x, lo if at is None else at, period, k, out)


def loop_raw(x, lo, hi, period, k, out, at=None):
    """B: today's raw loop with a fixed 25 % linear crossfade (ntpsamples.raw_loop), at `at`."""
    return smp.raw_loop(x, lo if at is None else at, period, k, out)


def seam_score(x: np.ndarray, start: float, span: float, period: float, w_periods: float = 2.0) -> float:
    """How well the audio after `start + span` continues like the audio after `start`
    (1 - NCC over `w_periods` periods on either side of the seam, plus a level-mismatch term)."""
    m = max(8, int(w_periods * period))
    t = np.arange(-m, m)
    p = interp_at(x, start + t)
    q = interp_at(x, start + span + t)
    ep, eq = float(np.sqrt(np.mean(p ** 2))) or 1e-9, float(np.sqrt(np.mean(q ** 2))) or 1e-9
    ncc = float(np.dot(p, q)) / (len(t) * ep * eq)
    return (1 - ncc) + abs(20 * math.log10(eq / ep)) / 20


def loop_search(x, lo, hi, period, k, out, at=None, xfade=0.1, step_periods=0.25):
    """C: raw loop (k source periods, as recorded) from the start in [lo, hi) whose seam
    matches best (LoopAuditioneer / seam-finder style), then a short crossfade whose gains
    adapt to how correlated the two sides are (a^2 + b^2 + 2ab r = 1: linear when r = 1,
    equal power when r = 0, so no level dip either way)."""
    span = k * period
    starts = np.arange(lo, max(lo + 1, hi - span - 4 * period), step_periods * period)
    best = min(starts, key=lambda s0: seam_score(x, s0, span, period))
    M = out * 4
    st = span / M
    body = interp_at(x, best + np.arange(M) * st)
    nx = int(M * xfade)
    if nx > 1:
        pre = interp_at(x, best - (nx - np.arange(nx)) * st)
        tail = body[-nx:]
        r = float(np.dot(pre, tail) / ((np.linalg.norm(pre) * np.linalg.norm(tail)) or 1.0))
        u = np.linspace(0, 1, nx + 2)[1:-1]
        b = np.sin(0.5 * np.pi * u)                 # fade-in shape; a solves constant power given r
        a = -b * r + np.sqrt(np.maximum(b * b * r * r - b * b + 1, 0))
        body[-nx:] = tail * a + pre * b
    return resample(body, out)


def loop_snapshot_whole(x, lo, hi, period, k, out, at=None, cycles=4):
    """A': today's averaged snapshot cycle, but band-limited once and tiled (k | out)."""
    fine = 1024
    s0 = lo if at is None else at
    cyc = np.mean([interp_at(x, s0 + period * (c + np.arange(fine) / fine)) for c in range(cycles)], axis=0)
    loop = cycle_at_rate(cyc, k, out)
    src = interp_at(x, s0 + np.arange(fine * cycles) * (period / fine))
    e_src, e_loop = float(np.sqrt(np.mean(src ** 2))), float(np.sqrt(np.mean(loop ** 2)))
    return loop * (e_src / e_loop) if e_loop > 0 else loop


# ---------------------------------------------------------------------------
# Joining an attack to a loop (as ntpsamples.attack_and_loop, for any loop)

def join(x: np.ndarray, a: int, period: float, rate: float, k: int, attack_bytes: int, loop: np.ndarray,
         xfade_cycles: float = 4.0, level_match: bool = True, exact_zero: bool = False
         ) -> tuple[np.ndarray, np.ndarray]:
    """The source's first `attack_bytes` at `rate`, crossfaded over its last few loop cycles
    into the loop, which is rotated to the rising zero crossing that continues the attack's
    tail best (`exact_zero`: on the band-limited waveform, loopbuild.zero_start_exact)."""
    zero_start = zero_start_exact if exact_zero else smp.zero_start
    L = len(loop)
    att_end = a + attack_bytes / rate * SR
    hi = min(len(x), int(round(att_end)) + int(4 * period))
    seg = x[a:hi]
    attack = resample(seg, int(round(len(seg) * rate / SR)))[:attack_bytes]
    if len(attack) < attack_bytes:
        attack = np.pad(attack, (0, attack_bytes - len(attack)))
    n = min(attack_bytes // 4, max(4, int(round(xfade_cycles * L / k))))
    tail = attack[-n:]
    if level_match:
        e_tail, e_lp = float(np.sqrt(np.mean(tail ** 2))), float(np.sqrt(np.mean(loop ** 2)))
        if e_tail > 0 and e_lp > 0:
            loop = loop * (e_tail / e_lp)
    idx = np.nonzero((np.roll(loop, 1) < 0) & (loop >= 0))[0]
    if not len(idx):
        idx = np.array([int(np.argmin(np.abs(loop)))])

    def score(c):
        ext = np.roll(loop, -c)[np.arange(-n, 0) % L]
        return float(np.dot(tail, ext)) / ((np.linalg.norm(tail) * np.linalg.norm(ext)) or 1.0)
    loop = zero_start(loop, int(max(idx, key=score)))
    ext = loop[np.arange(-n, 0) % L]
    w = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n + 1)[1:])
    attack = attack.copy()
    attack[-n:] = tail * (1 - w) + ext * w
    return attack, loop


def play(attack: np.ndarray, loop: np.ndarray, rate: float, seconds: float, semis: float = 0.0,
         env: tuple[np.ndarray, np.ndarray] | None = None, out_rate: float = DOC_RATE) -> np.ndarray:
    """The sample as the DOC plays it: a phase accumulator stepping rate*2^(semis/12)/out_rate
    through attack then loop (no interpolation), with an optional smooth gain contour
    (seconds, gain) applied after the attack, and a 30 ms release."""
    n = int(seconds * out_rate)
    pos = np.arange(n) * (rate * 2 ** (semis / 12) / out_rate)
    A, L = len(attack), len(loop)
    i = pos.astype(np.int64)
    in_loop = loop[np.maximum(i - A, 0) % L]
    y = in_loop if A == 0 else np.where(i < A, attack[np.minimum(i, A - 1)], in_loop)
    if env is not None:
        t = np.arange(n) / out_rate
        t_att = A / (rate * 2 ** (semis / 12))
        g = np.interp(t, *env)
        g0 = float(np.interp(t_att, *env)) or 1.0
        y = y * np.where(t < t_att, 1.0, g / g0)
    r = int(0.03 * out_rate)
    y[-r:] *= np.linspace(1, 0, r)
    return y / 128.0


def to_bytes(attack: np.ndarray, loop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """8-bit as the MOD stores it (ModSample.mod_bytes): one scale for attack and loop."""
    d = np.concatenate([attack, loop])
    q = np.clip(np.round(d / (float(np.max(np.abs(d))) or 1.0) * 127), -128, 127)
    return q[:len(attack)], q[len(attack):]


# ---------------------------------------------------------------------------
# Metrics (loop_buzz_db and loop_tremolo_db are the build's, from ntpmod)

def attack_error_db(snd: Sound, attack: np.ndarray, loop: np.ndarray, rate: float, window_s: float = 0.5) -> float:
    """How far the played note's spectral shape is from the Munt source over the first
    `window_s`: per frame, harmonic levels relative to the frame's total (dB), compared with
    the source's, RMS over harmonics (weighted by the source's sustain shares) and frames
    (weighted by the source frame's energy). Level is left to the Cxx envelope; this is
    what the attack bytes buy."""
    y = play(attack, loop, rate, window_s + 0.1, out_rate=SR)
    tr_y = harmonic_tracks(y, 0, snd.period, end=len(y))
    tr_x = harmonic_tracks(snd.x, snd.a, snd.period, end=snd.a + len(y))
    T = min(tr_y.amp.shape[1], tr_x.amp.shape[1])
    sel = tr_x.t[:T] <= window_s
    H = min(tr_y.amp.shape[0], tr_x.amp.shape[0])

    def shape(tr: Tracks) -> tuple[np.ndarray, np.ndarray]:
        e = tr.amp[:H, :T][:, sel] ** 2
        tot = e.sum(axis=0, keepdims=True)
        return 10 * np.log10(np.maximum(e / np.maximum(tot, 1e-20), 1e-4)), tot[0]
    sy, _ = shape(tr_y)
    sx, ex = shape(tr_x)
    w = snd.st.weights[:H] / (snd.st.weights[:H].sum() or 1.0)
    per_frame = np.sqrt(np.sum(w[:, None] * (sy - sx) ** 2, axis=0))
    fw = ex / (ex.sum() or 1.0)
    return float(np.sum(fw * per_frame))


def seam_step(loop: np.ndarray) -> float:
    """Kink at the wrap: |second difference across it| / the 99th percentile of |second
    difference| inside the loop (<= 1: smoother than almost every other point)."""
    y = np.concatenate([loop[-2:], loop[:2]])
    dd_wrap = max(abs(y[2] - 2 * y[1] + y[0]), abs(y[3] - 2 * y[2] + y[1]))
    dd = np.abs(np.diff(loop, 2))
    return float(dd_wrap / max(float(np.percentile(dd, 99)), 1.0))


def _frames(y: np.ndarray, per: float, hop: float, n_per: int = 2, H: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """Harmonic dB per frame (pitch-synchronous, `n_per` periods), frame centres in samples."""
    points = 64
    N = n_per * points
    span = n_per * per
    c = np.arange(span / 2, len(y) - span / 2, hop)
    pos = c[:, None] - span / 2 + np.arange(N)[None, :] * (per / points)
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N)     # periodic Hann: no leakage between harmonic bins
    X = np.fft.rfft(interp_at(y, pos) * w[None, :], axis=1)
    H = min(H, points // 2 - 1)
    amp = 2 * np.abs(X[:, n_per * np.arange(1, H + 1)]) / w.sum()
    return c, 20 * np.log10(np.maximum(amp, 1e-6))


def _flux(db: np.ndarray, floor: float) -> np.ndarray:
    """RMS harmonic-level change between consecutive frames (dB), levels floored at `floor`."""
    d = np.maximum(db, floor)
    return np.sqrt(np.mean(np.diff(d, axis=0) ** 2, axis=1))


def seam_flux(loop: np.ndarray, k: int, reps: int = 6) -> float:
    """Harmonic-spectrum change across the wrap, in dB (RMS over harmonics within 60 dB of
    the strongest, frames of 2 periods hopped by 1/4 period), minus the median change
    elsewhere in the loop: 0 = the seam changes the spectrum no more than the loop does."""
    up = 8                                              # band-limited (periodic) upsampling, so
    per = len(loop) * up / k                            # framing at any phase sees the same cycle
    y = np.tile(resample(loop, len(loop) * up), reps)
    n = len(loop) * up
    c, db = _frames(y, per, per / 4)
    fl = _flux(db, float(db.max()) - 60)
    mid = (c[:-1] + c[1:]) / 2
    ph = np.mod(mid, n)
    near = np.minimum(ph, n - ph) < per
    if not near.any() or near.all():
        return 0.0
    return float(fl[near].max() - np.median(fl[~near]))


def handover(attack: np.ndarray, loop: np.ndarray, k: int) -> tuple[float, float]:
    """(level step dB, spectral jump dB) at the attack->loop join: RMS of the last two periods
    of the attack vs the first two of the loop; the largest harmonic flux (as seam_flux) at
    the join, minus the median in the loop."""
    per = len(loop) / k
    m = max(2, int(round(2 * per)))
    e1 = float(np.sqrt(np.mean(attack[-m:] ** 2))) or 1e-9
    e2 = float(np.sqrt(np.mean(loop[:m] ** 2))) or 1e-9
    up = 8
    y = resample_poly(np.concatenate([attack, loop, loop]), up, 1)
    per *= up
    join_at = len(attack) * up
    c, db = _frames(y, per, per / 4)
    fl = _flux(db, float(db.max()) - 60)
    mid = (c[:-1] + c[1:]) / 2
    near = np.abs(mid - join_at) < per
    late = mid > join_at + 2 * per
    jump = float(fl[near].max() - np.median(fl[late])) if near.any() and late.any() else 0.0
    return 20 * math.log10(e2 / e1), jump


def likeness_db(loop: np.ndarray, k: int, ref_amp: np.ndarray, weights: np.ndarray) -> float:
    """Weighted RMS dB difference between the loop's harmonic levels and the source sustain's
    RMS harmonic levels, after matching overall level (timbre only)."""
    X = np.abs(np.fft.rfft(loop))
    H = min(len(ref_amp), (len(X) - 1) // k)
    la = 20 * np.log10(np.maximum(X[k * np.arange(1, H + 1)], 1e-9))
    lr = 20 * np.log10(np.maximum(ref_amp[:H], 1e-9))
    w = weights[:H] / (weights[:H].sum() or 1.0)
    d = la - lr
    d = d - float(np.sum(w * d))
    return float(np.sqrt(np.sum(w * d * d)))


# ---------------------------------------------------------------------------
# Trial on the sample set

@dataclass(frozen=True)
class Candidate:
    build: Callable[..., np.ndarray]
    whole: bool             # only whole samples per cycle (k | loop bytes)
    source: str             # "fixed": today's 0.4 s point; "sustain": the measured sustain span
    exact_zero: bool = False    # band-limited zero crossing at loop[0]
    aligned: bool = False       # attack moved into phase with the loop (loopbuild.join_aligned)


CANDIDATES = {
    "A": Candidate(loop_snapshot, False, "fixed"),                   # today (short loops)
    "A'": Candidate(loop_snapshot_whole, True, "fixed", exact_zero=True),  # whole samples per cycle
    "B": Candidate(loop_raw, False, "fixed"),                        # today (long loops)
    "C": Candidate(loop_search, False, "sustain"),                   # searched raw loop + adaptive xfade
    "E": Candidate(loop_power, False, "sustain"),                    # power-averaged harmonic cycle
    "E'": Candidate(loop_power, True, "sustain", exact_zero=True),   # ... whole samples per cycle
    "E'+": Candidate(loop_power, True, "sustain", exact_zero=True, aligned=True),  # ... attack aligned
}


@dataclass
class Trial:
    sound: str
    cand: str
    bytes: int              # loop bytes
    k: int                  # periods in the loop
    rate: float
    cents: float            # tuning error
    spc: float              # samples per cycle
    rms_lsb: float          # loop level in 8-bit steps
    buzz: float             # loop_buzz_db of the 8-bit loop
    buzz_float: float       # ... before rounding
    step: float             # seam_step
    trem: float             # loop_tremolo_db
    sflux: float            # seam_flux
    hand_db: float          # handover level step
    hand_flux: float        # handover spectral jump
    like: float             # likeness_db
    att_err: float          # attack_error_db
    attack: np.ndarray      # 8-bit values, as stored
    loop: np.ndarray
    same: bool | None = None    # (candidate A) identical to the build's sample

    @property
    def handover_ok(self) -> bool:
        return abs(self.hand_db) < 1.0 and self.hand_flux < 1.5


def _fixed_loop_point(snd: Sound, att_end: float, sustain_s: float) -> float:
    """Where ntpsamples.attack_and_loop takes the loop: `sustain_s` after the onset, moved back
    in 10 ms steps (not before the attack's end) while the level there is under 20 % of the peak."""
    env = audio.rms_envelope(snd.x)
    top = float(np.max(env[snd.a:])) or 1.0
    at = snd.a + sustain_s * SR
    while at > att_end and env[int(at)] < 0.2 * top:
        at -= 0.01 * SR
    return max(at, att_end)


def trial(snd: Sound, cand: str, loop_bytes: int, attack_bytes: int, tables: dict, cfg: dict,
          key_lo: int, key_hi: int, sustain_s: float = 0.4) -> Trial:
    c = CANDIDATES[cand]
    key = snd.m["key"]
    err, k, rate, _, _ = tune(SR / snd.period, tables, cfg["min_rate"], cfg["max_rate"], loop_bytes, key,
                              min(key_lo, key), max(key_hi, key), whole_cycles=c.whole)
    att_end = snd.a + attack_bytes / rate * SR
    if c.source == "fixed":
        at = _fixed_loop_point(snd, att_end, sustain_s)
        loop = c.build(snd.x, at, at, snd.period, k, loop_bytes)
    else:
        lo = max(snd.lo, att_end)
        hi = max(snd.hi, lo + int(k * snd.period) + int(8 * snd.period))
        loop = c.build(snd.x, lo, hi, snd.period, k, loop_bytes, at=att_end if c.build is loop_power else None)
    if c.aligned:
        attack, loop = join_aligned(snd.x, snd.a, snd.period, rate, k, attack_bytes, loop)
    else:
        attack, loop = join(snd.x, snd.a, snd.period, rate, k, attack_bytes, loop, exact_zero=c.exact_zero)
    qa, ql = to_bytes(attack, loop)
    hand_db, hand_flux = handover(qa, ql, k)
    return Trial(
        sound=snd.name, cand=cand, bytes=loop_bytes, k=k, rate=rate, cents=err, spc=loop_bytes / k,
        rms_lsb=float(np.sqrt(np.mean(ql ** 2))), buzz=loop_buzz_db(ql, k), buzz_float=loop_buzz_db(loop, k),
        step=seam_step(ql), trem=loop_tremolo_db(ql, k), sflux=seam_flux(ql, k), hand_db=hand_db,
        hand_flux=hand_flux, like=likeness_db(ql, k, snd.ref_amp, snd.st.weights),
        att_err=attack_error_db(snd, qa, ql, rate), attack=qa, loop=ql)


def attack_bytes_for(st: Settle, rate: float, period: float, xfade_cycles: float = 4.0) -> int:
    """Smallest attack block that holds the measured attack plus the handover crossfade."""
    need = (st.attack_s + xfade_cycles * period / SR) * rate
    return next((b for b in ATTACK_SIZES if b >= need), ATTACK_SIZES[-1])


def pages(attack_bytes: int, loop_bytes: int) -> int:
    """DOC pages of an attack (plus its 8 stopper bytes) and a loop, each a power-of-two block."""
    def block(n):
        return 1 << max(8, math.ceil(math.log2(n)))
    return (block(attack_bytes + 8) + block(loop_bytes)) // 256


def allocate_attacks(sounds: dict[str, tuple[int, dict[int, Trial]]], budget: int) -> dict[str, int]:
    """Attack block per sound: start every sound at its smallest size whose handover passes,
    then repeatedly grow the one that saves the most attack error per extra page, while the
    total stays within `budget` pages."""
    pick = {}
    for name, (_, per) in sounds.items():
        ok = [b for b in ATTACK_SIZES if per[b].handover_ok]
        pick[name] = ok[0] if ok else ATTACK_SIZES[0]

    def used() -> int:
        return sum(pages(pick[n], lb) for n, (lb, _) in sounds.items())
    while True:
        best, gain = None, 0.0
        for name, (lb, per) in sounds.items():
            for b in ATTACK_SIZES:
                if b <= pick[name] or not per[b].handover_ok:
                    continue
                dp = pages(b, lb) - pages(pick[name], lb)
                de = per[pick[name]].att_err - per[b].att_err
                if dp > 0 and de > 0 and used() + dp <= budget and de / dp > gain:
                    best, gain = (name, b), de / dp
        if best is None:
            return pick
        pick[best[0]] = best[1]


# ---------------------------------------------------------------------------
# Outputs

def _wav(path: Path, y: np.ndarray, rate: float) -> None:
    wavfile.write(path, int(round(rate)), (np.clip(y, -1, 1) * 32767).astype(np.int16))


def _plot(path: Path, snd: Sound, rows: list[Trial]) -> None:
    import matplotlib                       # the "lab" dependency group; only the lab plots
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 6), gridspec_kw=dict(height_ratios=[2, 1]))
    tr, st = snd.tr, snd.st
    for h in range(min(8, tr.amp.shape[0])):
        if st.weights[h] > 1e-3:
            ax.plot(tr.t, tr.db[h], lw=0.6, label=f"h{h + 1}")
    ax.plot(tr.t, tr.level_db, "k", lw=1.0, label="level")
    ax.plot(tr.t, st.level_db, "k--", lw=1.0, label="beat-averaged")
    ax.axvline(st.attack_s, color="g", lw=1.2, label=f"attack end {st.attack_s * 1000:.0f} ms")
    ax.axvline(0.4, color="r", lw=0.8, ls=":", label="today's loop source (0.4 s)")
    top = float(tr.level_db.max())
    ax.set_ylim(top - 50, top + 3)
    ax.set_xlim(0, min(3.0, tr.t[-1]))
    ax.set_title(f"{snd.name}: {'steady' if st.steady else 'decaying'}"
                 + (f", wobble {st.mod_s:.2f} s / {st.depth_db:.1f} dB" if st.mod_s else "")
                 + (f", -40 dB at {st.dead_s:.2f} s" if st.dead_s else ""))
    ax.legend(fontsize=6, ncol=6)
    ax.set_xlabel("s after onset")
    for r in rows:
        y = np.tile(r.loop, 3)
        ax2.plot(np.arange(len(y)) / r.rate * 1000, y, lw=0.6, label=f"{r.cand} ({r.bytes} B, k={r.k})")
    ax2.set_xlabel("loop x3, ms")
    ax2.legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def _listening(snd: Sound, rows: dict[str, Trial], listen: list[str], keys: tuple[int, int]) -> np.ndarray:
    """The source, then each listened candidate at the sampled key and the lowest and highest
    keys played, with gaps, as the DOC plays them."""
    st = snd.st
    env = (snd.tr.t, 10 ** ((st.level_db - st.level_db.max()) / 20))
    gap = np.zeros(int(0.3 * DOC_RATE))
    src = snd.x[snd.a:snd.a + int(2.0 * SR)]
    src = resample_poly(src, 3290, 4000)[:int(2.0 * DOC_RATE)]       # 32000 -> 26320 Hz
    parts = [src / (np.max(np.abs(src)) or 1.0), gap]
    key = snd.m["key"]
    for c in listen:
        if c in rows:
            r = rows[c]
            for semis in (0, keys[0] - key, keys[1] - key):
                parts += [play(r.attack, r.loop, r.rate, 2.0, semis, env), gap]
    y = np.concatenate(parts)
    return y / (np.max(np.abs(y)) or 1.0) * 0.9


def _passes(r: Trial, slow_hz: float) -> bool:
    slow = r.rate / r.bytes <= slow_hz
    return ((slow or (r.buzz < -50 and r.trem <= 1.0)) and r.step <= 2 and r.sflux < 0.5
            and abs(r.hand_db) < 1 and r.hand_flux < 1.5)


def report(mode: str, det: list, res: list[Trial], sizing: list, shot_note: dict[str, str], cfg: dict) -> str:
    lines = ["# Loop lab report", "",
             f"Mode `{mode}`; generated by `just looplab`. Metrics: docs/loop-detection.md.", "",
             "## Attack detection (6 s Munt renders, pitch-synchronous harmonic tracks)", "",
             "| sound | kind | attack end ms | wobble s | depth dB | -40 dB at s | attack now B (ms) |",
             "|---|---|---|---|---|---|---|"]
    for snd, s in det:
        st = snd.st
        lines.append(f"| {snd.name} | {'steady' if st.steady else f'decay {st.decay_db_s:.0f} dB/s'} | "
                     f"{st.attack_s * 1000:.0f} | {st.mod_s or 0:.2f} | {st.depth_db:.1f} | "
                     f"{'' if st.dead_s is None else f'{st.dead_s:.2f}'} | {s.loop_start} ({s.loop_start / s.rate * 1000:.0f}) |")
    lines += ["", "## Loop candidates", "",
              "buzz: off-harmonic energy of the 8-bit loop (dB, -200 = exactly periodic); tremolo: level range "
              "across the loop's cycles, repeated at the loop rate; step: kink at the wrap "
              "(<= 1 normal); seam/hand jump: harmonic flux at the wrap / attack join minus the loop's own (dB); "
              "hand dB: level step at the join; like: timbre vs the source sustain (dB RMS).", "",
              "| sound | cand | loop B | k | samples/cycle | cents | LSB rms | buzz | tremolo dB @ Hz | step | seam jump | hand dB | hand jump | like | pass |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    slow_hz = cfg.get("raw_loop_slow_hz", 2.0)
    for r in res:
        extra = {True: " (= build)", False: " (!= build)", None: ""}[r.same]
        lines.append(f"| {r.sound} | {r.cand}{extra} | {r.bytes} | {r.k} | {r.spc:.2f} | {r.cents:+.1f} | "
                     f"{r.rms_lsb:.0f} | {r.buzz:.0f} | {r.trem:.1f} @ {r.rate / r.bytes:.1f} | {r.step:.1f} | {r.sflux:.2f} | {r.hand_db:+.1f} | "
                     f"{r.hand_flux:.2f} | {r.like:.1f} | {'yes' if _passes(r, slow_hz) else 'no'} |")
    if sizing:
        budget = sum(pages(s.loop_start, s.loop_len) for _, s, _, _ in sizing)
        alloc = allocate_attacks({snd.name: (s.loop_len, per) for snd, s, _, per in sizing}, budget)
        lines += ["", "## Attack sizing (E'+)", "",
                  "Attack error: spectral-shape distance (dB) of the played note from the Munt source over the "
                  "first 0.5 s (what attack bytes buy). Handover: level step / spectral jump at the join. "
                  f"Allocation: greedy by error saved per page, within today's {budget} pages for these sounds.", "",
                  "| sound | now B | detected attack needs B | " + " | ".join(f"{b} B: err / hand dB / jump" for b in ATTACK_SIZES)
                  + " | allocated B | note |",
                  "|---|---|---|" + "---|" * len(ATTACK_SIZES) + "---|---|"]
        for snd, s, need, per in sizing:
            cells = " | ".join(f"{per[b].att_err:.1f} / {per[b].hand_db:+.1f} / {per[b].hand_flux:.1f}"
                               + ("" if per[b].handover_ok else " ✗") for b in ATTACK_SIZES)
            lines.append(f"| {snd.name} | {s.loop_start} | {need} | {cells} | {alloc[snd.name]} | "
                         f"{shot_note.get(snd.name, '')} |")
        used = sum(pages(alloc[snd.name], s.loop_len) for snd, s, _, _ in sizing)
        e0 = sum(per[s.loop_start].att_err for _, s, _, per in sizing)
        e1 = sum(per[alloc[snd.name]].att_err for snd, s, _, per in sizing)
        lines.append(f"\nPages: today {budget}, allocated {used}. Sum of attack errors: today {e0:.1f} dB, allocated {e1:.1f} dB.")
    return "\n".join(lines) + "\n"


def _one_shot_note(m: dict, s: smp.ModSample, render, tables: dict, cfg: dict, hold: float,
                   keys: tuple[int, int]) -> str:
    """What the build's one-shot rule (ntpsamples.floor_time + one_shot_tuning) decides for a
    decaying sound."""
    x = render(m["program"], m["key"], hold)
    a = audio.onset(x)
    t_floor = smp.floor_time(x, a, a + int(hold * SR), cfg.get("one_shot_floor_lsb", 1.5))
    shot = smp.one_shot_tuning(t_floor, s.rate, sum(s.ntp_blocks()) // 256, tables, m["key"], *keys,
                              cfg["min_rate"], cfg.get("one_shot_max_rate_drop", 0.1))
    return (f"one-shot: {shot[1]} B @ {shot[0]:.0f} Hz ({t_floor * 1000:.0f} ms to the 8-bit floor)"
            if shot else f"looped: 8-bit floor at {t_floor:.2f} s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="MT-32 MIDI files (for key ranges and the shared set)")
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("build/looplab"))
    ap.add_argument("--config", type=Path, default=Path("config/ntp.toml"))
    ap.add_argument("--spec", type=Path, default=Path("config/mt32bank.toml"))
    ap.add_argument("--make-tables", type=Path, default=Path("vendor/ntp/src/make_tables.php"))
    ap.add_argument("--smf2wav", type=Path, default=Path("vendor/munt/bin/mt32emu-smf2wav"))
    ap.add_argument("--roms", type=Path, default=Path("original files/mt-32 roms"))
    ap.add_argument("--cache", type=Path, default=Path("build/cache/mt32samples"))
    ap.add_argument("--samples", choices=("per-song", "shared"), default="shared")
    ap.add_argument("--cands", default="A,A',B,C,E,E',E'+", help="candidates to trial")
    ap.add_argument("--listen", default="A,E'+", help="candidates to write WAVs for")
    args = ap.parse_args(argv)

    conf = tomllib.loads(args.config.read_text())
    cfg = {**conf["defaults"], **conf.get("modes", {}).get(args.samples, {})}
    spec = ntpmod.ntp_spec(tomllib.loads(args.spec.read_text()), conf, args.samples)
    spec = ntpmod.with_attack_bytes(spec, cfg)
    tables = smp.period_tables(args.make_tables)
    munt = Munt(args.smf2wav, args.roms, args.cache)
    songs = [read_notes(p)[:2] for p in args.inputs]
    # every melodic sound looped (the build's one-shot decision is reported separately)
    samples, _, _ = ntpmod.sample_set(spec, {**cfg, "one_shots": False}, songs, 255 - cfg.get("reserve_pages", 16),
                                      tables, munt)
    built = {s.name: s for s in samples if s.kind == "melodic"}
    render = lambda prog, key, hold: smp.cached_render(munt, prog, key, hold)
    out = args.out_dir
    (out / "wav").mkdir(parents=True, exist_ok=True)
    (out / "plots").mkdir(parents=True, exist_ok=True)
    cands = args.cands.split(",")
    listen = [c for c in args.listen.split(",") if c]

    det, res, sizing = [], [], []
    shot_note: dict[str, str] = {}
    for m in spec["melodic"]:
        s = built.get(m["name"])
        if s is None:
            continue
        snd = analyse(m, render)
        keys = (int(s.key_v.min()), int(s.key_v.max()))
        rows: dict[str, Trial] = {}
        for c in cands:
            # today's long loops are raw (B), not averaged
            r = trial(snd, "B" if c == "A" and s.loop_len > cfg["loop_bytes"] else c,
                      s.loop_len, s.loop_start, tables, cfg, *keys)
            r.cand = c
            if c == "A":
                built_bytes = np.frombuffer(s.mod_bytes(), np.int8).astype(float)
                r.same = bool(np.array_equal(built_bytes, np.concatenate([r.attack, r.loop])))
            rows[c] = r
            res.append(r)
        det.append((snd, s))
        if not s.steady:
            shot_note[m["name"]] = _one_shot_note(m, s, render, tables, cfg, spec.get("hold_seconds", 2.0), keys)
        # attack sizing for the recommended construction: every block size, measured
        if "E'+" in rows:
            per = {ab: rows["E'+"] if ab == s.loop_start else trial(snd, "E'+", s.loop_len, ab, tables, cfg, *keys)
                   for ab in ATTACK_SIZES}
            sizing.append((snd, s, attack_bytes_for(snd.st, rows["E'+"].rate, snd.period), per))
        stem = m["name"].replace(" ", "_")
        _plot(out / "plots" / f"{stem}.png", snd, [rows[c] for c in listen if c in rows])
        _wav(out / "wav" / f"{stem}.wav", _listening(snd, rows, listen, keys), DOC_RATE)

    (out / "report.md").write_text(report(args.samples, det, res, sizing, shot_note, cfg))
    print(f"wrote {out / 'report.md'}, {out / 'wav'}/, {out / 'plots'}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
