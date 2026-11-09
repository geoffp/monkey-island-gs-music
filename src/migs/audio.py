# SPDX-License-Identifier: GPL-3.0-or-later
"""Analysis of rendered MT-32 notes: loudness envelope, onset, pitch period, zero crossings,
averaged cycles and relative pitch. Signals are mono floats at munt.SR."""

from __future__ import annotations

import numpy as np
from scipy.signal import resample

from .munt import SR


def rms_envelope(x: np.ndarray, win: float = 0.01) -> np.ndarray:
    """RMS over a centred `win`-second window, per sample."""
    n = max(1, int(win * SR))
    return np.sqrt(np.convolve(x * x, np.ones(n) / n, mode="same"))


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
