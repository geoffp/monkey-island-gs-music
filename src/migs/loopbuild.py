# SPDX-License-Identifier: GPL-3.0-or-later
"""E'+ attack + loop construction for the NTP build's short loops (docs/loop-detection.md).

- Attack detection: a pitch-synchronous STFT (window = an integer number of periods, so harmonic
  h sits exactly on bin n*h) gives one amplitude track per harmonic (`harmonic_tracks`); a sound
  has settled when its harmonics stay within their sustained range (`settle`).
- Loop: one cycle with each harmonic at its RMS level over the sustain (`harmonic_cycle`),
  tiled with whole samples per cycle and an exact band-limited zero crossing at loop[0]
  (`loop_power`, `zero_start_exact`).
- Join: the attack moved into phase with the loop (`join_aligned`).

Used by ntpmod (loop_construction = "eprime", the default). looplab measures it against the
other candidates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.signal import resample

from .audio import interp_at, onset, refine_period, smooth
from .munt import SR

DOC_RATE = 894886 / 34              # DOC output rate with 32 oscillators enabled (doc_tuning rounds it)


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


def modulation_period(y: np.ndarray, dt: float, lo: float = 0.06, hi: float = 1.2, min_r: float = 0.3) -> float | None:
    """Period (s) of a periodic wobble in a track (chorus/beating/tremolo): the first strong
    autocorrelation peak of the track minus its slow trend, or None."""
    n = len(y)
    if n * dt < 2 * lo:
        return None
    d = y - smooth(y, int(round(min(hi * 1.5, n * dt / 2) / dt)))
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
                            # ntpsamples.floor_time / one_shot_tuning, reported by `main`)
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
    Ls = 10 * np.log10(np.maximum(smooth(P, n), 1e-14))
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
    db = 10 * np.log10(np.maximum(np.vstack([smooth(r, k) for r in e]), 1e-14))
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


def cycle_at_rate(cyc_fine: np.ndarray, k: int, out: int) -> np.ndarray:
    """k copies of one cycle in `out` points. With out % k == 0 each copy is the same whole
    number of samples (band-limited once, then tiled), so they stay identical when rounded."""
    if out % k == 0:
        return np.tile(resample(cyc_fine, out // k), k)
    return resample(np.tile(cyc_fine, k), out)


def harmonic_cycle(x: np.ndarray, lo: float, hi: float, period: float, fine: int = 1024,
                   phase_at: float | None = None, harmonics: int | None = None, notes: int = 1) -> np.ndarray:
    """One cycle whose harmonic amplitudes are the RMS of each harmonic over [lo, hi)
    (power-averaged: beating partials keep their mean power instead of cancelling) and whose
    phases are those of the period at `phase_at` (default lo). `notes`: note periods the
    cycle spans (2 for a sound with a sub-octave partial, `sub_octave`): as many harmonics
    of the cycle, so the same bandwidth is kept."""
    tr = harmonic_tracks(x, int(lo), period, end=int(hi), harmonics=32 * notes, points=64 * notes)
    amp = np.sqrt(np.mean(tr.amp ** 2, axis=1))
    p0 = lo if phase_at is None else phase_at
    cyc = interp_at(x, p0 + np.arange(fine) * period / fine)
    X = np.fft.rfft(cyc)
    H = len(amp) if harmonics is None else min(harmonics, len(amp))
    Y = np.zeros_like(X)
    ph = np.angle(X[1:H + 1])
    Y[1:H + 1] = amp[:H] * fine / 2 * np.exp(1j * ph)
    return np.fft.irfft(Y, fine)


def loop_power(x: np.ndarray, lo: float, hi: float, period: float, k: int, out: int,
               at: float | None = None, notes: int = 1) -> np.ndarray:
    """E: constructed loop, exactly periodic: one power-averaged harmonic cycle over the
    sustain [lo, hi), phases from `at`, tiled k times (whole samples per cycle when k | out).
    `period` spans `notes` note periods (harmonic_cycle)."""
    return cycle_at_rate(harmonic_cycle(x, lo, hi, period, phase_at=at, notes=notes), k, out)


def sub_octave(x: np.ndarray, lo: float, hi: float, period: float) -> float:
    """Share of the power over [lo, hi) in the series an octave below the note (odd harmonics
    of twice its period). The MT-32 Pipe Org 2 has a steady partial there at every key
    (8-19 %: key 88 has peaks at 667, 1328, 2002, 2657, 4003 Hz); a loop of single note
    periods drops it, and the attack, taken as recorded, keeps it: the attack sounded an
    octave below the loop. Every other looped MI1 sound is under 0.01 %."""
    tr = harmonic_tracks(x, int(lo), 2 * period, end=int(hi), harmonics=64, points=128)
    pw = np.mean(tr.amp ** 2, axis=1)
    return float(pw[0::2].sum() / (pw.sum() or 1.0))


def exact_crossing(loop: np.ndarray, p0: float, iters: int = 8) -> tuple[float, float]:
    """(position, slope per sample) of the band-limited waveform's zero crossing nearest p0
    (samples, fractional): Newton steps on the loop's Fourier series."""
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
    p, d = p0, 0.0
    for _ in range(iters):
        v, d = val(p)
        if d == 0:
            break
        step = v / d
        p -= float(np.clip(step, -0.5, 0.5))
        if abs(step) < 1e-9:
            break
    return p, d


def rising_crossings(loop: np.ndarray, below: float, over: int = 8) -> list[tuple[float, float]]:
    """(position, slope) of the band-limited waveform's rising zero crossings before `below`
    (samples). Found on the `over`-times oversampled waveform: a crossing between two samples
    of the same sign (a shallow dip below zero) is a real crossing too."""
    n = len(loop)
    up = resample(loop, n * over)
    j = np.nonzero((np.roll(up, 1) < 0) & (up >= 0))[0]
    out = [exact_crossing(loop, (int(i) - 1 + (-up[i - 1] / (up[i] - up[i - 1]))) / over) for i in j
           if i / over < below]
    return [(p % n, d) for p, d in out if d > 0]


def rotate_to(loop: np.ndarray, p: float) -> np.ndarray:
    """The periodic loop rotated so position p (a zero crossing) lands on index 0, set to 0."""
    n = len(loop)
    f = np.fft.rfftfreq(n, 1 / n)
    out = np.fft.irfft(np.fft.rfft(loop) * np.exp(2j * np.pi * f * p / n), n)
    out[0] = 0.0
    return out


def zero_start_exact(loop: np.ndarray, c: int, iters: int = 8) -> np.ndarray:
    """Rotate a periodic loop so a rising zero crossing of its *band-limited* waveform lands
    on index 0. ntpsamples.zero_start puts the linear-interpolation crossing there and then forces
    loop[0] = 0; on steep or short cycles the band-limited value there isn't 0 (Strings at 32
    samples/cycle: -0.012, i.e. 1 LSB), so one sample per pass was wrong: a loop-rate buzz.
    Newton steps on the Fourier series find the true crossing; the forced 0 is then exact."""
    n = len(loop)
    y0, y1 = loop[(c - 1) % n], loop[c]
    p0 = (c - 1) + (-y0 / (y1 - y0) if y1 != y0 else 1.0)
    return rotate_to(loop, exact_crossing(loop, p0, iters)[0])

def join_aligned(x: np.ndarray, a: int, period: float, rate: float, k: int, attack_bytes: int,
                 loop: np.ndarray, xfade_cycles: float = 4.0, steps: int = 64) -> tuple[np.ndarray, np.ndarray]:
    """Like looplab.join, but the loop keeps its exact zero crossing at index 0 and the
    *attack* moves instead: the attack's source start shifts earlier by 0..1 period (pre-onset
    silence) so that the attack ends in phase with the loop's start. The crossfade then joins
    two aligned waveforms (no partial cancellation, no level dip, no spectral jump). The loop
    is scaled to the attack's tail level; returns (attack, loop)."""
    L = len(loop)
    n = min(attack_bytes // 4, max(4, int(round(xfade_cycles * L / k))))
    src_len = attack_bytes / rate * SR                      # source samples the attack covers
    # The gentlest rising crossing (band-limited slope) in the first cycle: the DOC hands over
    # from the attack oscillator to the loop's with a sub-sample slip (it reads the attack's
    # 0 stopper byte, the loop starts at phase 0), and the slip costs the waveform's slope
    # there. The first crossing was often the steepest: shared-pitch Pipe Organ stepped 74
    # (its largest step) where a crossing of a quarter that slope was on offer, and clicked
    # on every note in KEGS. Only the first cycle: a raw loop's level moves (wobble), and the
    # loop is level-matched to the attack around its start.
    cross = rising_crossings(loop, max(1.0, L / k))
    if cross:
        loop = rotate_to(loop, min(cross, key=lambda c: c[1])[0])
    else:
        idx = np.nonzero((np.roll(loop, 1) < 0) & (loop >= 0))[0]
        loop = zero_start_exact(loop, int(idx[0]) if len(idx) else int(np.argmin(np.abs(loop))))
    ext = loop[np.arange(-n, 0) % L]                        # the loop's continuation before index 0
    tpos = np.arange(-n, 0) * (SR / rate)                   # those points, in source samples

    def match(s0: float) -> float:
        """Correlation of the attack's tail, starting the attack at source sample s0, with
        the loop's continuation."""
        t = interp_at(x, s0 + src_len + tpos)
        return float(np.dot(t, ext) / ((np.linalg.norm(t) * np.linalg.norm(ext)) or 1.0))
    # first best wins, scanning from `a` back one period
    best_s0 = float(max(a - np.arange(steps + 1) * (period / steps), key=match))
    margin = int(4 * period)                                # so the resampler doesn't ring at the end
    seg = interp_at(x, best_s0 + np.arange(int(np.ceil(src_len)) + margin))
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


@dataclass
class Sound:
    name: str
    m: dict                 # its [[melodic]] entry in config/mt32bank.toml
    x: np.ndarray           # long render (analysis + audio)
    a: int
    period: float
    st: Settle
    tr: Tracks
    ref_amp: np.ndarray     # RMS harmonic amplitudes of the reference sustain
    lo: int                 # sustain span usable for loops (source samples)
    hi: int


def analyse(m: dict, render: Callable[[int, int, float], np.ndarray], hold: float = 6.0,
            build_hold: float = 2.0) -> Sound:
    """Analyse a sound from a `hold`-second render (`render(program, key, hold)`): its
    harmonic tracks, where its attack settles, and the sustain span a loop may come from
    (within what the build's `build_hold` recording holds)."""
    x = render(m["program"], m["key"], hold)
    a = onset(x)
    f0 = 440 * 2 ** ((m["key"] - 69) / 12)
    period = refine_period(x, a + int(0.25 * SR), f0)
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


# ---------------------------------------------------------------------------
# Playing a sample as the DOC does, and what its attack bytes buy

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
