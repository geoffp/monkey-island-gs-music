# SPDX-License-Identifier: GPL-3.0-or-later
"""How well a stored sample plays a note on the Ensoniq DOC, measured against the MT-32.

A sample of a sound is the Munt render at its sampled key `k`, resampled to its rate `r` and
rounded to 8 bits. The DOC plays key `n` by stepping through it `r * 2^((n - k)/12) / DOC_RATE`
samples per output sample, without interpolation (loopbuild.play does the same). Two things go
wrong away from the sampled key:

- the sound's timbre is the MT-32's at `k`, transposed: the top end above `r/2` (moved with the
  pitch) is missing, and the MT-32's own register changes are lost;
- drop-sample stepping adds images of the spectrum (aliasing), worst when a sample plays far
  below its rate (a low note from a high sample) or skips samples (a high note).

`note_error_db` puts both into one number: the A-weighted distortion-to-signal ratio of the
played note's time-averaged spectrum against Munt's render of key `n`, after level
normalisation (the per-row Cxx handle level). Harmonic levels are compared as magnitudes;
energy between the harmonics (images, noise) is compared as a total. Time averaging keeps
modulation (a rotary wobble, chorus) out: two renders differ in its phase, not in quality.
Measurements: docs/ninjatrackerplus.md "Sampled pitch and rate".
"""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
from scipy.signal import resample, resample_poly

from .doc_tuning import DOC_RATE
from .munt import SR

N_FFT = 4096                     # at DOC_RATE: 6.4 Hz bins, so harmonics of key 28 (41 Hz) resolve
BAND_HZ = 12000                  # compared up to here (the DOC's Nyquist is 13.16 kHz)
WINDOW_S = (0.03, 0.6)           # the stretch of each note compared (onset + attack + early sustain)
HOP_S = 0.05
VERSION = 1                      # bump when the measure changes (invalidates the disk cache)


def a_weight(f: np.ndarray) -> np.ndarray:
    """IEC 61672 A-weighting as a power gain, 1 at 1 kHz."""
    f2 = np.asarray(f, dtype=np.float64) ** 2
    ra = 12194.0 ** 2 * f2 ** 2 / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
                                   * (f2 + 12194.0 ** 2))
    return (ra / 0.79434) ** 2


_FREQ = np.fft.rfftfreq(N_FFT, 1 / DOC_RATE)
_WEIGHT = a_weight(_FREQ) * (_FREQ <= BAND_HZ)
_HANN = np.hanning(N_FFT)


def to_doc_rate(x: np.ndarray) -> np.ndarray:
    """A 32 kHz render at the DOC's output rate (26320 = 32000 * 329/400)."""
    assert SR == 32000 and DOC_RATE == 26320
    return resample_poly(x, 329, 400)


def stored(x: np.ndarray, rate: float) -> np.ndarray:
    """A render (from its onset, at SR) as the sample bytes: resampled to `rate`, peak-normalised
    and rounded to 8 bits (values -127..127)."""
    s = resample(x, max(1, int(round(len(x) * rate / SR))))
    return np.clip(np.round(s / (float(np.max(np.abs(s))) or 1.0) * 127), -127, 127)


def doc_play(sample: np.ndarray, rate: float, semis: float, seconds: float) -> np.ndarray:
    """The DOC's output (at DOC_RATE) playing `sample` (stored at `rate`) `semis` above its key:
    a phase accumulator, no interpolation. Stops at the sample's end."""
    step = rate * 2 ** (semis / 12) / DOC_RATE
    idx = (np.arange(int(seconds * DOC_RATE)) * step).astype(np.int64)
    return sample[idx[idx < len(sample)]] / 127.0


def mean_spectrum(y: np.ndarray) -> np.ndarray:
    """A-weighted power spectrum (at DOC_RATE) averaged over the compared window, normalised to
    total 1 (level-free). Zeros if the signal is too short or silent."""
    starts = (np.arange(*WINDOW_S, HOP_S) * DOC_RATE).astype(int)
    frames = [y[i:i + N_FFT] for i in starts if i + N_FFT <= len(y)]
    if not frames:
        return np.zeros_like(_FREQ)
    p = np.mean([np.abs(np.fft.rfft(f * _HANN)) ** 2 for f in frames], axis=0) * _WEIGHT
    tot = p.sum()
    return p / tot if tot > 0 else p


def _harmonics(f0: float) -> tuple[np.ndarray, np.ndarray]:
    """(bin mask, harmonic number per bin) of the bins within a harmonic's main lobe."""
    h = np.round(_FREQ / f0)
    near = (h >= 1) & (np.abs(_FREQ - h * f0) <= max(3 * DOC_RATE / N_FFT, 0.04 * f0))
    return near, (h * near).astype(int)


def spectrum_error(ref: np.ndarray, got: np.ndarray, f0: float) -> tuple[float, float]:
    """(harmonic, between-harmonic) distortion of `got` against `ref` (mean_spectrum outputs,
    fundamental f0), as power ratios: sum over harmonics of (|got| - |ref|)^2, and the same for
    the total energy between the harmonics."""
    near, h = _harmonics(f0)
    n = int(h.max()) + 1
    hr, hg = np.bincount(h[near], ref[near], n)[1:], np.bincount(h[near], got[near], n)[1:]
    harm = float(np.sum((np.sqrt(hg) - np.sqrt(hr)) ** 2))
    rest = float((np.sqrt(got[~near].sum()) - np.sqrt(ref[~near].sum())) ** 2)
    return harm, rest


def drum_error_db(ref: np.ndarray, sample: np.ndarray, rate: float, semis: float, seconds: float,
                  frame: int = 1024, hop: int = 256) -> float:
    """A drum's version of note_error_db: the A-weighted distortion-to-signal ratio of the DOC
    playing `sample` (stored at `rate`, values -1..1, rounded to 8 bits here) `semis` above its
    key, against `ref` (Munt's render of the played key, at SR, from its onset), over the
    drum's own `seconds`. Drums are inharmonic and short: short frames (1024 at DOC_RATE,
    39 ms) averaged over the hit, power spectra compared as magnitudes, level-free. What a
    sample loses to a low rate (the top end, drop-sample images: Kick at 5872 Hz -9.6 dB,
    at 26273 Hz -23.2 dB) and to being cut short both count."""
    n = max(2 * frame, int((seconds + 0.02) * DOC_RATE))
    q = np.clip(np.round(sample / (float(np.max(np.abs(sample))) or 1.0) * 127), -127, 127)
    got = doc_play(q, rate, semis, n / DOC_RATE)
    want = to_doc_rate(ref[:int(n / DOC_RATE * SR) + 1])
    win = np.hanning(frame)

    def spec(y: np.ndarray) -> np.ndarray:
        y = np.pad(y, (0, max(0, n - len(y))))[:n]
        p = np.mean([np.abs(np.fft.rfft(y[i:i + frame] * win, N_FFT)) ** 2 for i in range(0, n - frame + 1, hop)],
                    axis=0) * _WEIGHT
        return p / (p.sum() or 1.0)
    return db(float(np.sum((np.sqrt(spec(got)) - np.sqrt(spec(want))) ** 2)))


def db(power_ratio: float) -> float:
    return float(10 * np.log10(power_ratio + 1e-12))


def key_hz(key: int) -> float:
    return 440.0 * 2 ** ((key - 69) / 12)


class NoteError:
    """note_error_db(k, rate, n) for one MT-32 program: renders come from `render(key)` (a
    Munt render at SR, cut at its onset). Spectra and samples are kept in memory; results go
    to an optional JSON cache file (`save()`), keyed by program, k, rate (Hz) and n."""

    def __init__(self, program: int, render: Callable[[int], np.ndarray], cache: Path | None = None,
                 seconds: float = WINDOW_S[1] + N_FFT / DOC_RATE):
        self.program, self.render, self.seconds = program, render, seconds
        self.cache_path = cache
        self._cache: dict[str, float] = {}
        if cache is not None and cache.exists():
            data = json.loads(cache.read_text())
            if data.get("version") == VERSION:
                self._cache = data["results"]
        self._dirty = False
        self._ref = functools.lru_cache(maxsize=None)(lambda n: mean_spectrum(to_doc_rate(self.render(n))))
        self._sample = functools.lru_cache(maxsize=64)(lambda k, r: stored(self.render(k), r))

    def __call__(self, k: int, rate: float, n: int) -> float:
        """Distortion/signal (dB) of key n played from the sample of key k at `rate`."""
        tag = f"{self.program}:{k}:{round(rate)}:{n}"
        if tag not in self._cache:
            y = doc_play(self._sample(k, round(rate)), round(rate), n - k, self.seconds)
            self._cache[tag] = db(sum(spectrum_error(self._ref(n), mean_spectrum(y), key_hz(n))))
            self._dirty = True
        return self._cache[tag]

    def save(self) -> None:
        """Merge this program's results into the cache file (atomic replace)."""
        if self.cache_path is None or not self._dirty:
            return
        merged = {}
        if self.cache_path.exists():
            data = json.loads(self.cache_path.read_text())
            if data.get("version") == VERSION:
                merged = data["results"]
        merged.update(self._cache)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": VERSION, "results": merged}))
        tmp.replace(self.cache_path)
        self._dirty = False
