# Prototype of docsim 2.0 from the 2026-11-13 bake-off (docs/similarity-measures.md).
# Reference only, not imported by the build: plans/docsim2.md says how it becomes src/migs/docsim2.py.
"""docsim 2.0 candidates: a fast auditory comparison of a DOC playback with Munt's note.

Pipeline (all numpy, both signals at DOC_RATE):
  STFT (Hann, 1024 = 39 ms, hop 512) over the held note
  -> outer/middle-ear weighting (Terhardt 1979 threshold-in-quiet curve, as in PEAQ's basic model)
  -> power summed into critical bands (0.5 Bark, Traunmueller's Bark formula)
  -> spreading across bands (Schroeder et al. 1979 spreading function: masking)
  -> specific loudness, Zwicker's power law with threshold in quiet: N' = ((1 + E/E_t)^0.23 - 1)
  -> distance between the two loudness patterns, relative to the reference's loudness.
Level-free: each signal is scaled to the same RMS (the NTP sets level per row) before the model,
at a nominal listening level, so threshold in quiet matters about as it would.
"""
from __future__ import annotations

import functools

import numpy as np

FRAME, HOP = 1024, 512
LEVEL_DB = 70.0                  # nominal SPL of the RMS-normalised note


def bark(f):
    """Traunmueller (1990)."""
    return 26.81 * f / (1960.0 + f) - 0.53


def terhardt_db(f):
    """Threshold in quiet (dB SPL), Terhardt (1979); its negative is the ear's weighting."""
    k = np.maximum(f, 20.0) / 1000.0
    return 3.64 * k ** -0.8 - 6.5 * np.exp(-0.6 * (k - 3.3) ** 2) + 1e-3 * k ** 4


@functools.lru_cache(maxsize=None)
def _model(fs: float, dz: float = 0.5, fmax: float = 12000.0):
    f = np.fft.rfftfreq(FRAME, 1 / fs)
    keep = (f >= 40) & (f <= fmax)
    z = bark(f)
    edges = np.arange(bark(40.0), bark(fmax) + dz, dz)
    band = np.clip(np.searchsorted(edges, z) - 1, 0, len(edges) - 2)
    nb = len(edges) - 1
    M = np.zeros((nb, len(f)))
    w = 10 ** (-terhardt_db(f) / 10)                     # ear weighting (power)
    M[band[keep], np.nonzero(keep)[0]] = w[keep]
    zc = (edges[:-1] + edges[1:]) / 2
    d = zc[None, :] - zc[:, None]                         # maskee - masker (Bark)
    S_db = 15.81 + 7.5 * (d + 0.474) - 17.5 * np.sqrt(1 + (d + 0.474) ** 2)
    S = 10 ** (S_db / 10)                                 # rows: masker band -> columns: maskee
    fc = 1960.0 * (zc + 0.53) / (26.81 - (zc + 0.53))     # band centres (Hz)
    thr = 10 ** ((terhardt_db(fc) * 0 + 0.0) / 10)        # threshold after ear weighting ~ 0 dB SPL flat
    return M, S, thr, zc


def loudness_pattern(y: np.ndarray, fs: float, spread: bool = True) -> np.ndarray:
    """Specific loudness (bands x frames) of `y`, RMS-normalised to LEVEL_DB SPL."""
    M, S, thr, _ = _model(fs)
    y = y / (np.sqrt(np.mean(y ** 2)) or 1.0)
    n = 1 + max(0, (len(y) - FRAME) // HOP)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    fr = np.pad(y, (0, max(0, idx.max() + 1 - len(y))))[idx] * np.hanning(FRAME)
    P = np.abs(np.fft.rfft(fr, axis=1)) ** 2              # frames x bins
    # calibrate: a full-scale-RMS sine -> LEVEL_DB SPL (power per unit RMS^2)
    P *= 10 ** (LEVEL_DB / 10) / (np.sum(np.hanning(FRAME) ** 2) * FRAME / 2)
    E = M @ P.T                                           # bands x frames
    if spread:
        E = S.T @ E
    return (1 + E / thr[:, None]) ** 0.23 - 1


def distance(ref: np.ndarray, fs_ref: float, got: np.ndarray, fs_got: float, *, spread=True,
             timed=True, asym=1.0, t0=0.03, t1=None) -> float:
    """Relative loudness-pattern difference: sum |N'got - N'ref| / sum N'ref (0 = same).
    asym > 1 weighs loudness the playback adds (images, noise, buzz) more than loudness it
    lacks, as PEAQ and PESQ do. Both signals must be at the same rate (resample first)."""
    assert fs_ref == fs_got
    a, b = loudness_pattern(ref, fs_ref, spread), loudness_pattern(got, fs_got, spread)
    f0, f1 = int(t0 * fs_ref / HOP), None if t1 is None else int(t1 * fs_ref / HOP)
    t = min(a.shape[1], b.shape[1])
    a, b = a[:, f0:min(t, f1 or t)], b[:, f0:min(t, f1 or t)]
    if not timed:
        a, b = a.mean(axis=1, keepdims=True), b.mean(axis=1, keepdims=True)
    d = b - a
    w = np.where(d > 0, asym, 1.0)
    return float(np.sum(np.abs(d) * w) / (np.sum(a) or 1.0))


# --- phase-free components ------------------------------------------------------------------
SEGMENTS = ((0.03, 0.15), (0.15, 0.6), (0.6, 1.5), (1.5, None))     # attack, early, mid, late
MOD_BANDS = ((0.5, 2.0), (2.0, 4.0), (4.0, 8.0), (8.0, 16.0))      # Hz: slow swell .. roughness edge


def seg_distance(a: np.ndarray, b: np.ndarray, fs: float) -> float:
    """Loudness-pattern difference of segment means (attack, early, mid, late sustain), each
    segment weighted equally: time shape at coarse resolution, but no penalty for modulation
    that runs out of phase with the reference."""
    fr = fs / HOP
    out = []
    for t0, t1 in SEGMENTS:
        i0, i1 = int(t0 * fr), (None if t1 is None else int(t1 * fr))
        sa, sb = a[:, i0:i1], b[:, i0:i1]
        if sa.shape[1] < 2 or sb.shape[1] < 2:
            continue
        ma, mb = sa.mean(axis=1), sb.mean(axis=1)
        out.append(np.sum(np.abs(mb - ma)) / (np.sum(ma) or 1.0))
    return float(np.mean(out)) if out else 0.0


def modulation(p: np.ndarray, fs: float, t0: float = 0.15, t1: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(depth per band x modulation band, band weight): each band's loudness fluctuation
    (RMS of its envelope within a modulation band, over its mean) in the sustain."""
    fr = fs / HOP
    seg = p[:, int(t0 * fr):(None if t1 is None else int(t1 * fr))]
    mean = seg.mean(axis=1)
    x = seg - mean[:, None]
    n = seg.shape[1]
    win = np.hanning(n)
    X = np.abs(np.fft.rfft(x * win, axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1 / fr)
    norm = np.sum(win ** 2)
    D = np.stack([np.sqrt(2 * X[:, (f >= lo) & (f < hi)].sum(axis=1) / (n * norm)) for lo, hi in MOD_BANDS], axis=1)
    return D / np.maximum(mean, 1e-9)[:, None], mean


def mod_distance(a: np.ndarray, b: np.ndarray, fs: float) -> float:
    """Phase-free movement difference: |depth_got - depth_ref| per band and modulation band,
    weighted by the reference's loudness in that band."""
    da, w = modulation(a, fs)
    db_, _ = modulation(b, fs)
    return float(np.sum(w[:, None] * np.abs(db_ - da)) / (np.sum(w) * len(MOD_BANDS) or 1.0))


def components(ref: np.ndarray, got: np.ndarray, fs: float) -> dict:
    a, b = loudness_pattern(ref, fs), loudness_pattern(got, fs)
    t = min(a.shape[1], b.shape[1])
    a, b = a[:, :t], b[:, :t]
    return {"ds2_seg": seg_distance(a, b, fs), "ds2_mod": mod_distance(a, b, fs)}


# --- faster loudness pattern (same model) ---------------------------------------------------
@functools.lru_cache(maxsize=None)
def _fast_model(fs: float, dz: float = 0.5, fmax: float = 12000.0):
    M, S, thr, zc = _model(fs, dz, fmax)
    f = np.fft.rfftfreq(FRAME, 1 / fs)
    keep = np.nonzero(M.sum(axis=0) > 0)[0]              # bins in some band, ascending in Bark
    band = np.argmax(M[:, keep] > 0, axis=0)
    starts = np.concatenate([[0], np.nonzero(np.diff(band))[0] + 1])
    used = band[starts]                                    # bands that hold at least one bin
    w = M[band, keep]
    cal = 10 ** (LEVEL_DB / 10) / (np.sum(np.hanning(FRAME) ** 2) * FRAME / 2)
    return keep, starts, used, (w * cal).astype(np.float32), S.T.astype(np.float32), thr.astype(np.float32), M.shape[0]


def loudness_pattern_fast(y: np.ndarray, fs: float) -> np.ndarray:
    keep, starts, used, w, ST, thr, nb = _fast_model(fs)
    y = (y / (np.sqrt(np.mean(y ** 2)) or 1.0)).astype(np.float32)
    if len(y) < FRAME:
        y = np.pad(y, (0, FRAME - len(y)))
    fr = np.lib.stride_tricks.sliding_window_view(y, FRAME)[::HOP] * np.hanning(FRAME).astype(np.float32)
    X = np.fft.rfft(fr, axis=1)
    P = (X.real ** 2 + X.imag ** 2)[:, keep] * w            # frames x kept bins
    E = np.zeros((nb, P.shape[0]), dtype=np.float32)
    E[used] = np.add.reduceat(P, starts, axis=1).T
    E = ST @ E
    return (1 + E / thr[:, None]) ** 0.23 - 1
