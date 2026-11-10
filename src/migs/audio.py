# SPDX-License-Identifier: GPL-3.0-or-later
"""Analysis of rendered MT-32 notes: loudness envelope, onset, pitch period, zero crossings,
averaged cycles and relative pitch. Signals are mono floats at munt.SR."""

from __future__ import annotations

import numpy as np
from scipy.signal import resample

from .munt import SR


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


def interp_at(x: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """x at fractional sample positions (linear interpolation, held at the ends): the same
    arithmetic as np.interp(pos, arange(len(x)), x), but reading only the two neighbours of
    each position. np.interp scanned the whole render per call (1.2M calls on renders of
    ~300k samples were 40% of an NTP build)."""
    pos = np.asarray(pos, dtype=np.float64)
    if len(x) < 2:
        return np.full(pos.shape, float(x[0]) if len(x) else np.nan)
    p = np.clip(pos, 0.0, len(x) - 1.0)
    j = np.minimum(np.floor(p).astype(np.int64), len(x) - 2)
    y = (x[j + 1] - x[j]) * (p - j) + x[j]
    return np.where(p == len(x) - 1, x[-1], y)


def rms_envelope(x: np.ndarray, win: float = 0.01) -> np.ndarray:
    """RMS over a centred `win`-second window, per sample: the window of
    np.convolve(x * x, ones(n) / n, "same") (zeros beyond the ends), from a cumulative sum
    as in `smooth` (the convolution was 15% of an NTP build)."""
    n = max(1, int(win * SR))
    if n == 1:
        return np.abs(x)
    c = np.concatenate([[0.0], np.cumsum(x * x, dtype=np.float64)])
    i = np.arange(len(x))
    lo = np.maximum(i - n // 2, 0)
    hi = np.minimum(i + (n - 1) // 2, len(x) - 1) + 1
    return np.sqrt(np.maximum(c[hi] - c[lo], 0.0) / n)


def onset(x: np.ndarray, frac: float = 0.02) -> int:
    """First sample above `frac` of the peak."""
    peak = np.max(np.abs(x))
    return int(np.argmax(np.abs(x) > frac * peak))


def refine_period(x: np.ndarray, at: int, f0: float) -> float:
    """Period (samples) within 3% of SR/f0, by autocorrelation of 8 periods from sample `at`,
    refined by a parabola through the peak. SR/f0 itself if the peak sits at the edge of the
    search (no clear period near the nominal pitch: an inharmonic sound)."""
    p0 = SR / f0
    n = int(p0 * 8)
    seg = x[at:at + n + int(p0 * 1.1) + 2]
    lo, hi = int(p0 * 0.97), int(p0 * 1.03) + 1
    corr = [float(np.dot(seg[:n], seg[lag:lag + n])) for lag in range(lo, hi + 1)]
    i = int(np.argmax(corr))
    if i in (0, len(corr) - 1):
        return p0
    a, b, c = corr[i - 1], corr[i], corr[i + 1]
    den = a - 2 * b + c
    return lo + i + (0.5 * (a - c) / den if den else 0.0)


def rising_zero(x: np.ndarray, near: int, span: int) -> int:
    """The first sample after a rising zero crossing in [near, near + span), else `near`."""
    for i in range(near, min(len(x) - 1, near + span)):
        if x[i] <= 0 < x[i + 1]:
            return i + 1
    return near


def one_cycle(x: np.ndarray, start: int, period: float, cycles: int = 4, out: int = 256) -> np.ndarray:
    """Average `cycles` consecutive periods from `start`, band-limited to `out` points (periodic)."""
    fine = 4096
    acc = np.zeros(fine)
    for c in range(cycles):
        pos = start + period * (c + np.arange(fine) / fine)
        acc += np.interp(pos, np.arange(len(x)), x)
    return resample(acc / cycles, out)


def _log_spectrum(x: np.ndarray, window_s: float = 0.06) -> np.ndarray:
    """Log magnitude of the first `window_s` after the onset, on a quarter-semitone grid
    from 60 Hz to ~7.7 kHz."""
    a = onset(x)
    seg = x[a:a + int(window_s * SR)]
    seg = seg * np.hanning(int(window_s * SR))[:len(seg)]
    mag = np.abs(np.fft.rfft(seg, 8192))
    grid = 60 * 2 ** (np.arange(0, 7 * 12 * 4) / 48)
    return np.log1p(np.interp(grid, np.fft.rfftfreq(8192, 1 / SR), mag))


def pitch_offset(ref: np.ndarray, other: np.ndarray) -> int:
    """Semitone shift of `other` relative to `ref` (cross-correlation of their log-frequency
    spectra, so it works for unpitched sounds: rhythm keys that play one sample at
    different pitches). Negative: `other` is lower."""
    a, b = _log_spectrum(ref), _log_spectrum(other)
    a, b = a - a.mean(), b - b.mean()
    best = max(range(-48, 49), key=lambda s: float(np.dot(a[max(0, s):len(a) + min(0, s)],
                                                          b[max(0, -s):len(b) - max(0, s)])))
    return -round(best / 4)            # a[i+s] ~ b[i]: s > 0 means `other` is lower
