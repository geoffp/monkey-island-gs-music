# SPDX-License-Identifier: GPL-3.0-or-later
"""Loop lab: measure and compare ways to build the attack + sustain-loop samples of the NTP
bank from the Munt renders of the MT-32 sounds (config/mt32bank.toml, config/ntp.toml).

Research harness, not part of the build. See docs/loop-detection.md.

- Attack detection: a pitch-synchronous STFT (window = an integer number of periods, so
  harmonic h sits exactly on bin n*h) gives one amplitude track per harmonic. A sound has
  "settled" when its level and its spectral *shape* (harmonic levels relative to the total)
  stop changing; periodic wobble (chorus, beating, tremolo) is smoothed over its own period
  first, so it counts as steady rather than as a transient.
- Loop metrics: loop-rate buzz (energy off the f0 harmonics in the loop's line spectrum),
  seam step, seam spectral flux, attack->loop handover step, likeness to the source's
  sustain, DOC cost.
- Loop candidates: today's builders plus searched / constructed alternatives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import mt32bank as mb

SR = mb.SR


# ---------------------------------------------------------------------------
# Harmonic tracks (pitch-synchronous STFT)

@dataclass
class Tracks:
    t: np.ndarray           # frame centres, seconds after `a`
    amp: np.ndarray         # (H, T) amplitude of harmonic h+1 (linear, sine peak)
    period: float           # samples

    @property
    def db(self) -> np.ndarray:
        return 20 * np.log10(np.maximum(self.amp, 1e-7))

    @property
    def level_db(self) -> np.ndarray:
        return 10 * np.log10(np.maximum(np.sum(self.amp ** 2, axis=0) / 2, 1e-14))


def harmonic_tracks(x: np.ndarray, a: int, period: float, end: int | None = None, harmonics: int = 32,
                    n_per: int = 4, hop_s: float = 0.005, points: int = 64) -> Tracks:
    """Amplitude of each harmonic of a (quasi-)periodic sound over time. Each frame takes
    `n_per` periods, resampled to `points` per period, under a Hann window: harmonic h falls on
    bin n_per*h, and the window's leakage (+-1 bin) never reaches the neighbours (n_per apart)."""
    end = len(x) if end is None else end
    H = max(1, min(harmonics, points // 2 - 1, int(SR / 2 / (SR / period))))
    N = n_per * points
    span = n_per * period
    centres = np.arange(a + span / 2, end - span / 2, hop_s * SR)
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N)     # periodic Hann: no leakage between harmonic bins
    pos = centres[:, None] - span / 2 + np.arange(N)[None, :] * (period / points)
    seg = np.interp(pos, np.arange(len(x)), x) * w[None, :]
    X = np.fft.rfft(seg, axis=1)
    bins = n_per * np.arange(1, H + 1)
    amp = 2 * np.abs(X[:, bins]).T / w.sum()
    return Tracks((centres - a) / SR, amp, period)


def _smooth(y: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return y
    c = np.concatenate([[0.0], np.cumsum(y)])
    i = np.arange(len(y))
    lo, hi = np.maximum(i - n // 2, 0), np.minimum(i + (n - 1) // 2, len(y) - 1) + 1
    return (c[hi] - c[lo]) / (hi - lo)


def modulation_period(y: np.ndarray, dt: float, lo: float = 0.06, hi: float = 1.2, min_r: float = 0.3) -> float | None:
    """Period (s) of a periodic wobble in a track (chorus/beating/tremolo): the first strong
    autocorrelation peak of the track minus its slow trend, or None."""
    n = len(y)
    if n * dt < 2 * lo:
        return None
    d = y - _smooth(y, int(round(min(hi * 1.5, n * dt / 2) / dt)))
    d = d - d.mean()
    r = np.correlate(d, d, "full")[n - 1:]
    if r[0] <= 0:
        return None
    r = r / r[0]
    i0, i1 = max(1, int(lo / dt)), min(n - 1, int(hi / dt))
    for i in range(i0, i1):
        if r[i] >= min_r and r[i] >= r[i - 1] and r[i] >= r[i + 1]:
            return i * dt
    return None


@dataclass
class Settle:
    attack_s: float         # the attack is over (see `settle`)
    peak_s: float           # loudest moment of the beat-averaged level
    mod_s: float | None     # wobble (beat/chorus/tremolo) period of the level, s
    depth_db: float         # 5-95 % range of the level in the sustain (wobble depth)
    steady: bool            # the beat-averaged level holds (slope above `decay` dB/s after `sus_from`)
    decay_db_s: float       # level slope in the sustain (dB/s)
    dead_s: float | None    # level 40 dB below its peak, or None (the build's one-shot rule is
                            # ntpmod.floor_time / one_shot_tuning, reported by `main`)
    level_db: np.ndarray    # beat-averaged level (dB), on Tracks.t
    weights: np.ndarray     # harmonic power shares in the sustain (sum 1)


def settle(tr: Tracks, band: float = 1.5, decay: float = -2.0, dead_db: float = 40.0,
           sus_from: float = 0.5, sus_frac: float = 0.4, short_s: float = 0.02, line_s: float = 0.25) -> Settle:
    """Where the attack ends, from per-harmonic tracks (analyse a long render, e.g. 6 s, so a
    slow beat isn't mistaken for a decay).

    - Steady sounds: once every harmonic that matters (> -30 dB of the total) stays inside its
      own sustained min..max range (+-`band` dB), measured over the tracks' last 60 % (from
      `sus_frac` of the length, at least `sus_from`), so a slow swell isn't counted as
      sustain. The range includes the beat's nulls: beating is sustain, not attack. Very deep
      beating makes the ranges wide, so the attack end can come out early (Strings: 30 ms).
    - Decaying sounds: once each harmonic's dB track is a straight line (an exponential decay):
      weighted RMS deviation from a line fit over the next `line_s` under 1 dB.
    Tracks are smoothed over `short_s` only, so the onset isn't blurred."""
    dt = float(tr.t[1] - tr.t[0])
    P = np.sum(tr.amp ** 2, axis=0)
    L = 10 * np.log10(np.maximum(P, 1e-14))
    mod = modulation_period(L, dt, lo=0.1, hi=2.0)
    n = max(1, int(round(max(0.05, mod or 0.0) / dt)))
    Ls = 10 * np.log10(np.maximum(_smooth(P, n), 1e-14))
    ip = int(np.argmax(Ls))
    below = np.nonzero(Ls[ip:] < Ls[ip] - dead_db)[0]
    dead = float(tr.t[ip + below[0]]) if len(below) else None
    end = ip + below[0] if len(below) else len(Ls)
    t_end = float(tr.t[end - 1]) if end > 0 else 0.0
    i0 = min(int(np.searchsorted(tr.t, max(sus_from, sus_frac * t_end))), max(0, end - 2))
    sus = Ls[i0:end]
    slope = float(np.polyfit(tr.t[i0:end], sus, 1)[0]) if len(sus) > 2 else 0.0
    steady = len(sus) > 2 and slope > decay and dead is None
    depth = float(np.percentile(L[i0:end], 95) - np.percentile(L[i0:end], 5)) if end > i0 + 2 else 0.0
    e = tr.amp ** 2
    w = e[:, i0:end].mean(axis=1) if end > i0 else e.mean(axis=1)
    w = w / (w.sum() or 1.0)
    keep = w > 1e-3
    k = max(1, int(round(short_s / dt)))
    db = 10 * np.log10(np.maximum(np.vstack([_smooth(r, k) for r in e]), 1e-14))
    if steady:
        lo = db[:, i0:end].min(axis=1) - band
        hi = db[:, i0:end].max(axis=1) + band
        bad = ((db < lo[:, None]) | (db > hi[:, None]))[keep].any(axis=0)
        bad[i0:] = False
        idx = np.nonzero(bad)[0]
        attack = float(tr.t[min(idx[-1] + 1, len(tr.t) - 1)]) if len(idx) else float(tr.t[0])
    else:
        m = max(3, int(round(line_s / dt)))
        tt = tr.t[:m] - tr.t[:m].mean()
        attack = float(tr.t[ip])
        for i in range(ip, max(ip + 1, end - m)):
            seg = db[keep, i:i + m]
            if seg.shape[1] < m:
                break
            c = seg - seg.mean(axis=1, keepdims=True)
            b = c @ tt / (tt @ tt)
            res = np.sqrt(np.mean((c - b[:, None] * tt[None, :]) ** 2, axis=1))
            if float(np.sum(w[keep] * res) / np.sum(w[keep])) < 1.0:
                attack = float(tr.t[i])
                break
    return Settle(attack, float(tr.t[ip]), mod, depth, steady, slope, dead, Ls, w)


# ---------------------------------------------------------------------------
# Tuning: k periods in `loop_bytes` at a playable rate

def tune(freq: float, tables: dict, min_rate: float, max_rate: float, loop_bytes: int, key0: int,
         key_lo: int, key_hi: int, whole_cycles: bool = False) -> tuple[float, int, float, int, int]:
    """ntpmod._best_tuning: (err_cents, k, rate, base_note, finetune). `whole_cycles`: only k
    that divide loop_bytes, so the k cycles stay identical after 8-bit rounding."""
    from .ntpmod import _best_tuning
    return _best_tuning(freq, tables, min_rate, max_rate, loop_bytes, key0, key_lo, key_hi, whole_cycles)


# ---------------------------------------------------------------------------
# Loop builders: (x, sustain span [lo, hi) in source samples, period, k, out bytes) -> loop

def _at(x: np.ndarray, pos: np.ndarray) -> np.ndarray:
    return np.interp(pos, np.arange(len(x)), x)


def loop_snapshot(x, lo, hi, period, k, out, at=None):
    """A: today's averaged-cycle loop (ntpmod.periodic_loop), taken at `at`."""
    from .ntpmod import periodic_loop
    return periodic_loop(x, lo if at is None else at, period, k, out)


def loop_raw(x, lo, hi, period, k, out, at=None):
    """B: today's raw loop with a fixed 25 % linear crossfade (ntpmod.raw_loop), at `at`."""
    from .ntpmod import raw_loop
    return raw_loop(x, lo if at is None else at, period, k, out)


def _cycle_at_rate(cyc_fine: np.ndarray, k: int, out: int) -> np.ndarray:
    """k copies of one cycle in `out` points. With out % k == 0 each copy is the same whole
    number of samples (band-limited once, then tiled), so they stay identical when rounded."""
    from scipy.signal import resample
    if out % k == 0:
        return np.tile(resample(cyc_fine, out // k), k)
    return resample(np.tile(cyc_fine, k), out)


def seam_score(x: np.ndarray, start: float, span: float, period: float, w_periods: float = 2.0) -> float:
    """How well the audio after `start + span` continues like the audio after `start`
    (1 - NCC over `w_periods` periods on either side of the seam, plus a level-mismatch term)."""
    m = max(8, int(w_periods * period))
    t = np.arange(-m, m)
    p = _at(x, start + t)
    q = _at(x, start + span + t)
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
    body = _at(x, best + np.arange(M) * st)
    nx = int(M * xfade)
    if nx > 1:
        pre = _at(x, best - (nx - np.arange(nx)) * st)
        tail = body[-nx:]
        r = float(np.dot(pre, tail) / ((np.linalg.norm(pre) * np.linalg.norm(tail)) or 1.0))
        u = np.linspace(0, 1, nx + 2)[1:-1]
        # fade-in b(u) = sin-like shape; solve a for constant power given r
        b = np.sin(0.5 * np.pi * u)
        a = -b * r + np.sqrt(np.maximum(b * b * r * r - b * b + 1, 0))
        body[-nx:] = tail * a + pre * b
    from scipy.signal import resample
    return resample(body, out)


def harmonic_cycle(x, lo, hi, period, fine=1024, phase_at=None, harmonics=None):
    """One cycle whose harmonic amplitudes are the RMS of each harmonic over [lo, hi)
    (power-averaged: beating partials keep their mean power instead of cancelling) and whose
    phases are those of the period at `phase_at` (default lo)."""
    tr = harmonic_tracks(x, int(lo), period, end=int(hi))
    amp = np.sqrt(np.mean(tr.amp ** 2, axis=1))
    p0 = lo if phase_at is None else phase_at
    cyc = _at(x, p0 + np.arange(fine) * period / fine)
    X = np.fft.rfft(cyc)
    H = len(amp) if harmonics is None else min(harmonics, len(amp))
    Y = np.zeros_like(X)
    ph = np.angle(X[1:H + 1])
    Y[1:H + 1] = amp[:H] * fine / 2 * np.exp(1j * ph)
    return np.fft.irfft(Y, fine)


def loop_power(x, lo, hi, period, k, out, at=None):
    """E: constructed loop, exactly periodic: one power-averaged harmonic cycle over the
    sustain [lo, hi), phases from `at`, tiled k times (whole samples per cycle when k | out)."""
    return _cycle_at_rate(harmonic_cycle(x, lo, hi, period, phase_at=at), k, out)


def loop_snapshot_whole(x, lo, hi, period, k, out, at=None, cycles=4):
    """A': today's averaged snapshot cycle, but band-limited once and tiled (k | out)."""
    fine = 1024
    s0 = lo if at is None else at
    cyc = np.mean([_at(x, s0 + period * (c + np.arange(fine) / fine)) for c in range(cycles)], axis=0)
    loop = _cycle_at_rate(cyc, k, out)
    src = _at(x, s0 + np.arange(fine * cycles) * (period / fine))
    e_src, e_loop = float(np.sqrt(np.mean(src ** 2))), float(np.sqrt(np.mean(loop ** 2)))
    return loop * (e_src / e_loop) if e_loop > 0 else loop


def zero_start_exact(loop: np.ndarray, c: int, iters: int = 8) -> np.ndarray:
    """Rotate a periodic loop so a rising zero crossing of its *band-limited* waveform lands
    on index 0. ntpmod.zero_start puts the linear-interpolation crossing there and then forces
    loop[0] = 0; on steep or short cycles the band-limited value there isn't 0 (Strings at 32
    samples/cycle: -0.012, i.e. 1 LSB), so one sample per pass was wrong: a loop-rate buzz.
    Newton steps on the Fourier series find the true crossing; the forced 0 is then exact."""
    n = len(loop)
    X = np.fft.rfft(loop)
    f = np.fft.rfftfreq(n, 1 / n)
    wgt = np.full(len(X), 2.0)
    wgt[0] = 1.0
    if n % 2 == 0:
        wgt[-1] = 1.0

    def val(p):
        e = X * np.exp(2j * np.pi * f * p / n)
        return float(np.sum(wgt * e.real)) / n, float(np.sum(wgt * (e * 2j * np.pi * f / n).real)) / n
    y0, y1 = loop[(c - 1) % n], loop[c]
    p = (c - 1) + (-y0 / (y1 - y0) if y1 != y0 else 1.0)
    for _ in range(iters):
        v, d = val(p)
        if d == 0:
            break
        step = v / d
        p -= float(np.clip(step, -0.5, 0.5))
        if abs(step) < 1e-9:
            break
    out = np.fft.irfft(X * np.exp(2j * np.pi * f * p / n), n)
    out[0] = 0.0
    return out


# ---------------------------------------------------------------------------
# Joining an attack to a loop (as ntpmod.attack_and_loop, for any loop)

def join(x: np.ndarray, a: int, period: float, rate: float, k: int, attack_bytes: int, loop: np.ndarray,
         xfade_cycles: float = 4.0, level_match: bool = True, exact_zero: bool = False
         ) -> tuple[np.ndarray, np.ndarray]:
    from scipy.signal import resample
    from .ntpmod import zero_start as zs
    zero_start = zero_start_exact if exact_zero else zs
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


def join_aligned(x: np.ndarray, a: int, period: float, rate: float, k: int, attack_bytes: int,
                 loop: np.ndarray, xfade_cycles: float = 4.0, steps: int = 64) -> tuple[np.ndarray, np.ndarray]:
    """Like `join`, but the loop keeps its exact zero crossing at index 0 and the *attack*
    moves instead: the attack's source start shifts earlier by 0..1 period (pre-onset
    silence) so that the attack ends in phase with the loop's start. The crossfade then joins
    two aligned waveforms (no partial cancellation, no level dip, no spectral jump)."""
    from scipy.signal import resample
    L = len(loop)
    n = min(attack_bytes // 4, max(4, int(round(xfade_cycles * L / k))))
    src_len = attack_bytes / rate * SR                      # source samples the attack covers
    idx = np.nonzero((np.roll(loop, 1) < 0) & (loop >= 0))[0]
    c = int(idx[0]) if len(idx) else int(np.argmin(np.abs(loop)))
    loop = zero_start_exact(loop, c)
    ext = loop[np.arange(-n, 0) % L]                        # the loop's continuation before index 0
    tpos = np.arange(-n, 0) * (SR / rate)                   # those points, in source samples

    def tail_at(s0):
        return _at(x, s0 + src_len + tpos)
    best_s0, best_r = float(a), -2.0
    for s0 in a - np.arange(steps + 1) * (period / steps):
        t = tail_at(s0)
        r = float(np.dot(t, ext) / ((np.linalg.norm(t) * np.linalg.norm(ext)) or 1.0))
        if r > best_r:
            best_r, best_s0 = r, float(s0)
    m = int(4 * period)
    pos = best_s0 + np.arange(int(np.ceil(src_len)) + m)
    seg = _at(x, pos)
    attack = resample(seg, int(round(len(seg) * rate / SR)))[:attack_bytes]
    if len(attack) < attack_bytes:
        attack = np.pad(attack, (0, attack_bytes - len(attack)))
    tail = attack[-n:]
    e_tail, e_lp = float(np.sqrt(np.mean(tail ** 2))), float(np.sqrt(np.mean(ext ** 2)))
    if e_tail > 0 and e_lp > 0:
        loop = loop * (e_tail / e_lp)
        ext = ext * (e_tail / e_lp)
    w = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n + 1)[1:])
    attack = attack.copy()
    attack[-n:] = tail * (1 - w) + ext * w
    return attack, loop


def attack_error_db(snd: "Sound", attack: np.ndarray, loop: np.ndarray, rate: float, window_s: float = 0.5) -> float:
    """How far the played note's spectral shape is from the Munt source over the first
    `window_s`: per frame, harmonic levels relative to the frame's total (dB), compared with
    the source's, RMS over harmonics (weighted by the source's sustain shares) and frames
    (weighted by the source frame's energy). Level is left to the Cxx envelope; this is
    what the attack bytes buy."""
    y = play(attack, loop, rate, window_s + 0.1, out_rate=SR)
    a = 0
    tr_y = harmonic_tracks(y, a, snd.period, end=len(y))
    tr_x = harmonic_tracks(snd.x, snd.a, snd.period, end=snd.a + len(y))
    T = min(tr_y.amp.shape[1], tr_x.amp.shape[1])
    sel = tr_x.t[:T] <= window_s
    H = min(tr_y.amp.shape[0], tr_x.amp.shape[0])

    def shape(tr):
        e = tr.amp[:H, :T][:, sel] ** 2
        tot = e.sum(axis=0, keepdims=True)
        return 10 * np.log10(np.maximum(e / np.maximum(tot, 1e-20), 1e-4)), tot[0]
    sy, _ = shape(tr_y)
    sx, ex = shape(tr_x)
    w = snd.st.weights[:H] / (snd.st.weights[:H].sum() or 1.0)
    per_frame = np.sqrt(np.sum(w[:, None] * (sy - sx) ** 2, axis=0))
    fw = ex / (ex.sum() or 1.0)
    return float(np.sum(fw * per_frame))


def to_bytes(attack: np.ndarray, loop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """8-bit as the MOD stores it (ModSample.mod_bytes): one scale for attack and loop."""
    d = np.concatenate([attack, loop])
    q = np.clip(np.round(d / (float(np.max(np.abs(d))) or 1.0) * 127), -128, 127)
    return q[:len(attack)], q[len(attack):]


# ---------------------------------------------------------------------------
# Metrics

def loop_tremolo_db(loop: np.ndarray, k: int) -> float:
    """ntpmod.loop_tremolo_db: level range across the loop's cycles (an added tremolo)."""
    from .ntpmod import loop_tremolo_db as f
    return f(loop, k)


def loop_buzz_db(loop: np.ndarray, k: int) -> float:
    """ntpmod.loop_buzz_db (the build uses it to gate raw loops)."""
    from .ntpmod import loop_buzz_db as f
    return f(loop, k)


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
    X = np.fft.rfft(np.interp(pos, np.arange(len(y)), y) * w[None, :], axis=1)
    H = min(H, points // 2 - 1)
    amp = 2 * np.abs(X[:, n_per * np.arange(1, H + 1)]) / w.sum()
    return c, 20 * np.log10(np.maximum(amp, 1e-6))


def _flux(db: np.ndarray, floor: float) -> np.ndarray:
    d = np.maximum(db, floor)
    return np.sqrt(np.mean(np.diff(d, axis=0) ** 2, axis=1))


def seam_flux(loop: np.ndarray, k: int, reps: int = 6) -> float:
    """Harmonic-spectrum change across the wrap, in dB (RMS over harmonics within 60 dB of
    the strongest, frames of 2 periods hopped by 1/4 period), minus the median change
    elsewhere in the loop: 0 = the seam changes the spectrum no more than the loop does."""
    from scipy.signal import resample
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
    from scipy.signal import resample_poly
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

CANDIDATES = {
    # name: (builder, whole cycles, where it takes its audio)
    "A": (loop_snapshot, False, "fixed"),          # today (short loops)
    "A'": (loop_snapshot_whole, True, "fixed"),    # today's cycle, whole samples per cycle
    "B": (loop_raw, False, "fixed"),               # today (long loops)
    "C": (loop_search, False, "sustain"),          # searched raw loop + adaptive crossfade
    "E": (loop_power, False, "sustain"),           # power-averaged harmonic cycle
    "E'": (loop_power, True, "sustain"),           # ... with whole samples per cycle
    "E'+": (loop_power, True, "sustain"),          # ... and the attack aligned to the loop
}
EXACT_ZERO = {"A'", "E'", "E'+"}                   # band-limited zero crossing at loop[0]
ALIGNED = {"E'+"}                                  # attack moved into phase with the loop


@dataclass
class Sound:
    name: str
    m: dict
    x: np.ndarray           # long render (analysis + audio)
    a: int
    period: float
    st: Settle
    tr: Tracks
    ref_amp: np.ndarray     # RMS harmonic amplitudes of the reference sustain
    lo: int                 # sustain span usable for loops (source samples)
    hi: int


def analyse(m: dict, render, hold: float = 6.0, build_hold: float = 2.0) -> Sound:
    x = render(m["program"], m["key"], hold)
    a = mb.onset(x)
    f0 = 440 * 2 ** ((m["key"] - 69) / 12)
    period = mb.refine_period(x, a + int(0.25 * SR), f0)
    tr = harmonic_tracks(x, a, period, end=a + int(hold * SR))
    st = settle(tr)
    if st.steady:
        r0, r1 = max(st.attack_s, 0.3), tr.t[-1]
    else:
        r0 = st.attack_s
        r1 = r0 + 0.1
    sel = (tr.t >= r0) & (tr.t <= r1)
    ref_amp = np.sqrt(np.mean(tr.amp[:, sel] ** 2, axis=1))
    lo = a + int(st.attack_s * SR)
    hi = a + int((build_hold - 0.1) * SR)          # what the build's 2 s recording holds
    if not st.steady:
        hi = min(hi, lo + int(0.15 * SR))          # decays: stay near the handover
    return Sound(m["name"], m, x, a, period, st, tr, ref_amp, lo, hi)


def trial(snd: Sound, cand: str, loop_bytes: int, attack_bytes: int, tables: dict, cfg: dict,
          key_lo: int, key_hi: int, sustain_s: float = 0.4) -> dict:
    build, whole, where = CANDIDATES[cand]          # where: "fixed" = today's 0.4 s point
    key = snd.m["key"]
    err, k, rate, n0, ft = tune(SR / snd.period, tables, cfg["min_rate"], cfg["max_rate"], loop_bytes, key,
                                min(key_lo, key), max(key_hi, key), whole_cycles=whole)
    att_end = snd.a + attack_bytes / rate * SR
    if where == "fixed":                           # as ntpmod.attack_and_loop picks it
        env = mb.rms_envelope(snd.x)
        top = float(np.max(env[snd.a:])) or 1.0
        at = snd.a + sustain_s * SR
        while at > att_end and env[int(at)] < 0.2 * top:
            at -= 0.01 * SR
        at = max(at, att_end)
        loop = build(snd.x, at, at, snd.period, k, loop_bytes)
    else:
        lo = max(snd.lo, att_end)
        hi = max(snd.hi, lo + int(k * snd.period) + int(8 * snd.period))
        loop = build(snd.x, lo, hi, snd.period, k, loop_bytes, at=att_end if build is loop_power else None)
    if cand in ALIGNED:
        attack, loop = join_aligned(snd.x, snd.a, snd.period, rate, k, attack_bytes, loop)
    else:
        attack, loop = join(snd.x, snd.a, snd.period, rate, k, attack_bytes, loop, exact_zero=cand in EXACT_ZERO)
    qa, ql = to_bytes(attack, loop)
    lvl, hflux = handover(qa, ql, k)
    att_err = attack_error_db(snd, qa, ql, rate)
    return dict(att_err=att_err, sound=snd.name, cand=cand, bytes=loop_bytes, k=k, rate=rate, cents=err,
                spc=loop_bytes / k, rms_lsb=float(np.sqrt(np.mean(ql ** 2))),
                buzz=loop_buzz_db(ql, k), buzz_float=loop_buzz_db(loop, k), step=seam_step(ql),
                trem=loop_tremolo_db(ql, k),
                sflux=seam_flux(ql, k), hand_db=lvl, hand_flux=hflux,
                like=likeness_db(ql, k, snd.ref_amp, snd.st.weights),
                attack=qa, loop=ql)


ATTACK_SIZES = (504, 1016, 2040, 4088)             # + 8 stopper bytes = one 512 B..4 KB block
DOC_RATE = 894886 / 34                             # DOC output rate with 32 oscillators enabled


def attack_bytes_for(st: Settle, rate: float, period: float, xfade_cycles: float = 4.0) -> int:
    """Smallest attack block that holds the measured attack plus the handover crossfade."""
    need = (st.attack_s + xfade_cycles * period / SR) * rate
    return next((b for b in ATTACK_SIZES if b >= need), ATTACK_SIZES[-1])


def handover_ok(r: dict) -> bool:
    return abs(r["hand_db"]) < 1.0 and r["hand_flux"] < 1.5


def allocate_attacks(sounds: dict[str, tuple[int, dict[int, dict]]], budget: int) -> dict[str, int]:
    """Attack block per sound: start every sound at its smallest size whose handover passes,
    then repeatedly grow the one that saves the most attack error per extra page, while the
    total stays within `budget` pages."""
    pick = {}
    for name, (loop_bytes, per) in sounds.items():
        ok = [b for b in ATTACK_SIZES if handover_ok(per[b])]
        pick[name] = ok[0] if ok else ATTACK_SIZES[0]

    def used():
        return sum(pages(pick[n], lb) for n, (lb, _) in sounds.items())
    while True:
        best, gain = None, 0.0
        for name, (lb, per) in sounds.items():
            for b in ATTACK_SIZES:
                if b <= pick[name] or not handover_ok(per[b]):
                    continue
                dp = pages(b, lb) - pages(pick[name], lb)
                de = per[pick[name]]["att_err"] - per[b]["att_err"]
                if dp > 0 and de > 0 and used() + dp <= budget and de / dp > gain:
                    best, gain = (name, b), de / dp
        if best is None:
            return pick
        pick[best[0]] = best[1]


def pages(attack_bytes: int, loop_bytes: int) -> int:
    def block(n):
        return 1 << max(8, math.ceil(math.log2(n)))
    return (block(attack_bytes + 8) + block(loop_bytes)) // 256


def play(attack: np.ndarray, loop: np.ndarray, rate: float, seconds: float, semis: float = 0.0,
         env: tuple[np.ndarray, np.ndarray] | None = None, out_rate: float = DOC_RATE) -> np.ndarray:
    """The sample as the DOC plays it: a phase accumulator stepping rate*2^(semis/12)/out_rate
    through attack then loop (no interpolation), with an optional smooth gain contour
    (seconds, gain) applied after the attack, and a 30 ms release."""
    n = int(seconds * out_rate)
    pos = np.arange(n) * (rate * 2 ** (semis / 12) / out_rate)
    A, L = len(attack), len(loop)
    i = pos.astype(np.int64)
    y = loop[(np.maximum(i - A, 0)) % L] if A == 0 else \
        np.where(i < A, attack[np.minimum(i, A - 1)], loop[(np.maximum(i - A, 0)) % L])
    if env is not None:
        t = np.arange(n) / out_rate
        t_att = A / (rate * 2 ** (semis / 12))
        g = np.interp(t, *env)
        g0 = float(np.interp(t_att, *env)) or 1.0
        y = y * np.where(t < t_att, 1.0, g / g0)
    r = int(0.03 * out_rate)
    y[-r:] *= np.linspace(1, 0, r)
    return y / 128.0


def _wav(path, y: np.ndarray, rate: float) -> None:
    from scipy.io import wavfile
    wavfile.write(path, int(round(rate)), (np.clip(y, -1, 1) * 32767).astype(np.int16))


def _plot(path, snd: Sound, rows: list[dict]) -> None:
    import matplotlib
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
        y = np.tile(r["loop"], 3)
        ax2.plot(np.arange(len(y)) / r["rate"] * 1000, y, lw=0.6, label=f"{r['cand']} ({r['bytes']} B, k={r['k']})")
    ax2.set_xlabel("loop x3, ms")
    ax2.legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tomllib
    from pathlib import Path
    from types import SimpleNamespace
    from . import ntpmod as nm
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
    spec = nm.ntp_spec(tomllib.loads(args.spec.read_text()), conf, args.samples)
    over = cfg.get("ntp_attack_bytes", {})
    spec = {**spec, "melodic": [{**m, "ntp_attack_bytes": over[m["name"]]} if m["name"] in over else m
                                for m in spec["melodic"]]}
    tables = nm.period_tables(args.make_tables)
    paths = SimpleNamespace(smf2wav=args.smf2wav, roms=args.roms, cache=args.cache)
    songs = [nm.read_notes(p)[:2] for p in args.inputs]
    # every melodic sound looped (the build's one-shot decision is reported separately)
    samples, _, _ = nm.sample_set(spec, {**cfg, "one_shots": False}, songs, 255 - cfg.get("reserve_pages", 16),
                                  tables, paths)
    built = {s.name: s for s in samples if s.kind == "melodic"}
    render = lambda prog, key, hold: nm._render(args.smf2wav, args.roms, args.cache, program=prog, key=key, hold=hold)
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
        st = snd.st
        klo, khi = int(s.key_v.min()), int(s.key_v.max())
        rows = {}
        for c in cands:
            build_c = c
            if c == "A" and s.loop_len > cfg["loop_bytes"]:
                build_c = "B"                                  # today's long loops are raw
            r = trial(snd, build_c, s.loop_len, s.loop_start, tables, cfg, klo, khi)
            r["cand"] = c
            if c == "A":
                mbx = np.frombuffer(s.mod_bytes(), np.int8).astype(float)
                r["same"] = bool(np.array_equal(mbx, np.concatenate([r["attack"], r["loop"]])))
            rows[c] = r
            res.append(r)
        det.append((snd, s))
        if not s.steady:                                   # the build's one-shot rule
            hold = spec.get("hold_seconds", 2.0)
            x2 = render(m["program"], m["key"], hold)
            a2 = mb.onset(x2)
            t_floor = nm.floor_time(x2, a2, a2 + int(hold * SR), cfg.get("one_shot_floor_lsb", 1.5))
            shot = nm.one_shot_tuning(t_floor, s.rate, sum(s.ntp_blocks()) // 256, tables, m["key"], klo, khi,
                                      cfg["min_rate"], cfg.get("one_shot_max_rate_drop", 0.1))
            shot_note[m["name"]] = (f"one-shot: {shot[1]} B @ {shot[0]:.0f} Hz ({t_floor * 1000:.0f} ms to the 8-bit floor)"
                                    if shot else f"looped: 8-bit floor at {t_floor:.2f} s")
        # attack sizing for the recommended construction: every block size, measured
        if "E'+" in rows:
            per = {}
            for ab in ATTACK_SIZES:
                per[ab] = rows["E'+"] if ab == s.loop_start else trial(snd, "E'+", s.loop_len, ab, tables, cfg, klo, khi)
            sizing.append((snd, s, attack_bytes_for(st, rows["E'+"]["rate"], snd.period), per))
        _plot(out / "plots" / f"{m['name'].replace(' ', '_')}.png", snd, [rows[c] for c in listen if c in rows])
        # listening file: source, then each candidate, at the sampled key; then lowest/highest key
        env = (snd.tr.t, 10 ** ((st.level_db - st.level_db.max()) / 20))
        gap = np.zeros(int(0.3 * DOC_RATE))
        src = snd.x[snd.a: snd.a + int(2.0 * SR)]
        from scipy.signal import resample_poly
        src = resample_poly(src, 3290, 4000)[: int(2.0 * DOC_RATE)]       # 32000 -> 26320 Hz
        parts = [src / (np.max(np.abs(src)) or 1.0), gap]
        for c in listen:
            if c in rows:
                r = rows[c]
                for semis in (0, klo - m["key"], khi - m["key"]):
                    parts += [play(r["attack"], r["loop"], r["rate"], 2.0, semis, env), gap]
        y = np.concatenate(parts)
        _wav(out / "wav" / f"{m['name'].replace(' ', '_')}.wav", y / (np.max(np.abs(y)) or 1.0) * 0.9, DOC_RATE)

    lines = ["# Loop lab report", "",
             f"Mode `{args.samples}`; generated by `just looplab`. Metrics: docs/loop-detection.md.", "",
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
    for r in res:
        slow = r["rate"] / r["bytes"] <= cfg.get("raw_loop_slow_hz", 2.0)
        ok = (slow or (r["buzz"] < -50 and r["trem"] <= 1.0)) and r["step"] <= 2 and r["sflux"] < 0.5 and abs(r["hand_db"]) < 1 and r["hand_flux"] < 1.5
        extra = " (= build)" if r.get("same") else (" (!= build)" if r.get("same") is False else "")
        lines.append(f"| {r['sound']} | {r['cand']}{extra} | {r['bytes']} | {r['k']} | {r['spc']:.2f} | {r['cents']:+.1f} | "
                     f"{r['rms_lsb']:.0f} | {r['buzz']:.0f} | {r['trem']:.1f} @ {r['rate'] / r['bytes']:.1f} | {r['step']:.1f} | {r['sflux']:.2f} | {r['hand_db']:+.1f} | "
                     f"{r['hand_flux']:.2f} | {r['like']:.1f} | {'yes' if ok else 'no'} |")
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
            note = shot_note.get(snd.name, "")
            cells = " | ".join(f"{per[b]['att_err']:.1f} / {per[b]['hand_db']:+.1f} / {per[b]['hand_flux']:.1f}"
                               + ("" if handover_ok(per[b]) else " ✗") for b in ATTACK_SIZES)
            lines.append(f"| {snd.name} | {s.loop_start} | {need} | {cells} | {alloc[snd.name]} | {note} |")
        used = sum(pages(alloc[snd.name], s.loop_len) for snd, s, _, _ in sizing)
        e0 = sum(per[s.loop_start]['att_err'] for _, s, _, per in sizing)
        e1 = sum(per[alloc[snd.name]]['att_err'] for snd, s, _, per in sizing)
        lines.append(f"\nPages: today {budget}, allocated {used}. Sum of attack errors: today {e0:.1f} dB, allocated {e1:.1f} dB.")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out / 'report.md'}, {out / 'wav'}/, {out / 'plots'}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
