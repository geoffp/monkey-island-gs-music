# SPDX-License-Identifier: GPL-3.0-or-later
"""docsim 2.0: how close a DOC playback comes to the MT-32, as the ear compares them.

Both notes go through the same auditory model, at DOC_RATE, RMS-normalised to a nominal
70 dB SPL (level-free: the per-row Cxx sets the level):
  Hann STFT (1024 = 39 ms, hop 512) -> outer/middle-ear weighting by the threshold in quiet
  (Terhardt 1979) -> power in 0.5-Bark bands (Traunmueller 1990), 40 Hz - 12 kHz ->
  spreading across bands (Schroeder, Atal & Hall 1979: masking) -> specific loudness,
  Zwicker's power law with threshold, N' = (1 + E/E_t)^0.23 - 1.
The two loudness patterns are compared phase-free over the whole note:
  seg  the relative difference of their segment means (attack 0.03-0.15 s, early 0.15-0.6,
       mid 0.6-1.5, late 1.5 s on), segments weighted equally: coarse time shape, without
       penalising movement that runs out of phase with the reference (the short attack
       segment keeps a little phase of slow wobbles: 0.04 for 5 Hz AM at depth 0.5);
  mod  how much each band's loudness moves (modulation depth in 0.5-2, 2-4, 4-8, 8-16 Hz
       over the sustain), compared per band and weighted by the band's loudness (after Dau,
       Kollmeier & Kohlrausch 1997): what a raw loop's chorus or rotary wobble is for.
Melodic sounds score seg + mod; drums (one-shots) seg only, as a decay is not movement.
`db()` gives a score as 20 log10(distance): halving the distance gains 6 dB.

Why: docs/similarity-measures.md (bake-off against listening verdicts and ISO 532-1 Zwicker
loudness). docsim.py stays, unchanged, for the default builds.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .docram import DOC_RATE
from .loopbuild import play

FRAME, HOP = 1024, 512
LEVEL_DB = 70.0                  # nominal SPL of the RMS-normalised note
BAND_BARK = 0.5
F_LO, F_HI = 40.0, 12000.0
SEGMENTS = ((0.03, 0.15), (0.15, 0.6), (0.6, 1.5), (1.5, None))
MOD_BANDS = ((0.5, 2.0), (2.0, 4.0), (4.0, 8.0), (8.0, 16.0))
MOD_FROM_S = 0.15                # modulation is measured in the sustain, after the attack
VERSION = 1                      # bump when the measure changes (invalidates the disk cache)


def bark(f):
    """Traunmueller (1990)."""
    return 26.81 * f / (1960.0 + f) - 0.53


def threshold_db(f):
    """Threshold in quiet, dB SPL (Terhardt 1979); its negative weights the ear's input."""
    k = np.maximum(f, 20.0) / 1000.0
    return 3.64 * k ** -0.8 - 6.5 * np.exp(-0.6 * (k - 3.3) ** 2) + 1e-3 * k ** 4


@functools.lru_cache(maxsize=None)
def _model(fs: float):
    """(bins kept, band start offsets into them, bands used, weight per kept bin incl.
    calibration, spreading matrix (maskee x masker), band count)."""
    f = np.fft.rfftfreq(FRAME, 1 / fs)
    keep = np.nonzero((f >= F_LO) & (f <= F_HI))[0]
    edges = np.arange(bark(F_LO), bark(F_HI) + BAND_BARK, BAND_BARK)
    nb = len(edges) - 1
    band = np.clip(np.searchsorted(edges, bark(f[keep])) - 1, 0, nb - 1)
    starts = np.concatenate([[0], np.nonzero(np.diff(band))[0] + 1])
    used = band[starts]
    win = np.hanning(FRAME)
    # a full-scale-RMS signal -> LEVEL_DB SPL in total (Parseval over the one-sided spectrum)
    cal = 10 ** (LEVEL_DB / 10) / (np.sum(win ** 2) * FRAME / 2)
    w = 10 ** (-threshold_db(f[keep]) / 10) * cal
    zc = (edges[:-1] + edges[1:]) / 2
    d = zc[:, None] - zc[None, :]                       # maskee - masker (Bark)
    spread = 10 ** ((15.81 + 7.5 * (d + 0.474) - 17.5 * np.sqrt(1 + (d + 0.474) ** 2)) / 10)
    return keep, starts, used, w.astype(np.float32), spread.astype(np.float32), nb, win.astype(np.float32)


def loudness_pattern(y: np.ndarray, fs: float = DOC_RATE) -> np.ndarray:
    """Specific loudness (bands x frames, frame rate fs / HOP) of `y`, RMS-normalised."""
    keep, starts, used, w, spread, nb, win = _model(float(fs))
    y = np.asarray(y, dtype=np.float32)
    y = y / (np.sqrt(np.mean(y.astype(np.float64) ** 2)) or 1.0)
    if len(y) < FRAME:
        y = np.pad(y, (0, FRAME - len(y)))
    fr = np.lib.stride_tricks.sliding_window_view(y, FRAME)[::HOP] * win
    X = np.fft.rfft(fr, axis=1)
    P = (X.real ** 2 + X.imag ** 2)[:, keep] * w
    E = np.zeros((nb, P.shape[0]), dtype=np.float32)
    E[used] = np.add.reduceat(P, starts, axis=1).T
    return (1 + spread @ E) ** 0.23 - 1                 # threshold after ear weighting: 0 dB SPL


def _frames(t: float, fs: float) -> int:
    return int(round(t * fs / HOP))


def seg_distance(a: np.ndarray, b: np.ndarray, fs: float = DOC_RATE) -> float:
    """Mean over segments of sum|mean_b - mean_a| / sum mean_a (0: same)."""
    t = min(a.shape[1], b.shape[1])
    out = []
    for t0, t1 in SEGMENTS:
        i0, i1 = _frames(t0, fs), min(t, _frames(t1, fs) if t1 is not None else t)
        if i1 - i0 < 2:
            continue
        ma, mb = a[:, i0:i1].mean(axis=1), b[:, i0:i1].mean(axis=1)
        out.append(float(np.sum(np.abs(mb - ma)) / (np.sum(ma) or 1.0)))
    return float(np.mean(out)) if out else 0.0


def modulation(p: np.ndarray, fs: float = DOC_RATE) -> tuple[np.ndarray, np.ndarray]:
    """(depth: bands x MOD_BANDS, band mean loudness) over the sustain: the RMS of each band's
    loudness fluctuation within a modulation band, over the band's mean."""
    seg = p[:, _frames(MOD_FROM_S, fs):].astype(np.float64)
    n = seg.shape[1]
    mean = seg.mean(axis=1) if n else np.zeros(p.shape[0])
    if n < 8:
        return np.zeros((p.shape[0], len(MOD_BANDS))), mean
    win = np.hanning(n)
    X = np.abs(np.fft.rfft((seg - mean[:, None]) * win, axis=1)) ** 2
    f = np.fft.rfftfreq(n, HOP / fs)
    norm = n * np.sum(win ** 2)
    D = np.stack([np.sqrt(2 * X[:, (f >= lo) & (f < hi)].sum(axis=1) / norm) for lo, hi in MOD_BANDS], axis=1)
    return D / np.maximum(mean, 1e-9)[:, None], mean


def mod_distance(a: np.ndarray, b: np.ndarray, fs: float = DOC_RATE) -> float:
    t = min(a.shape[1], b.shape[1])
    da, w = modulation(a[:, :t], fs)
    dg, _ = modulation(b[:, :t], fs)
    return float(np.sum(w[:, None] * np.abs(dg - da)) / ((np.sum(w) * len(MOD_BANDS)) or 1.0))


def distance(ref: np.ndarray, got: np.ndarray, drum: bool = False, fs: float = DOC_RATE) -> float:
    """seg (+ mod unless a drum) between two loudness patterns (reference first)."""
    d = seg_distance(ref, got, fs)
    return d if drum else d + mod_distance(ref, got, fs)


def db(d: float) -> float:
    return float(20 * np.log10(max(d, 1e-6)))


def to_doc_rate(x: np.ndarray, sr: int) -> np.ndarray:
    """A Munt render (at `sr`) resampled to DOC_RATE."""
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(DOC_RATE), int(sr))
    return resample_poly(x, int(DOC_RATE) // g, int(sr) // g)


def play_sample(s, semis: float, seconds: float) -> np.ndarray:
    """A built ModSample as the NTP plays it (DOC_RATE): its 8-bit bytes through the DOC's
    drop-sample stepping, attack then loop (or once, for a one-shot), with its per-row
    loudness contour (env_t, env_g) by time after note-on."""
    q = np.clip(np.round(np.asarray(s.data) * 127), -128, 127)
    if s.loop_len:
        att, loop = q[:s.loop_start], q[s.loop_start:s.loop_start + s.loop_len]
        env = (np.asarray(s.env_t), np.asarray(s.env_g)) if len(s.env_t) > 1 else None
        return play(att, loop, s.rate, seconds, semis, env=env)
    step = s.rate * 2 ** (semis / 12) / DOC_RATE
    idx = (np.arange(int(seconds * DOC_RATE)) * step).astype(np.int64)
    y = np.zeros(len(idx))
    ok = idx < len(q)
    y[ok] = q[idx[ok]] / 128.0
    return y


def sample_tag(s) -> str:
    """What decides a sample's playback: its bytes, loop, rate, key and contour."""
    h = hashlib.sha1()
    h.update(np.clip(np.round(np.asarray(s.data) * 127), -128, 127).astype(np.int8).tobytes())
    h.update(f"{s.loop_start}:{s.loop_len}:{round(float(s.rate), 2)}:{s.key0}".encode())
    h.update(np.round(np.asarray(s.env_t, dtype=np.float64), 4).tobytes())
    h.update(np.round(np.asarray(s.env_g, dtype=np.float64), 4).tobytes())
    return h.hexdigest()[:16]


class Cache:
    """Scores on disk (JSON, keyed by string), merged on save like docsim.NoteError's."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, float] = {}
        self._dirty = False
        if path is not None and path.exists():
            d = json.loads(path.read_text())
            if d.get("version") == VERSION:
                self.data = d["results"]

    def get(self, key: str, compute: Callable[[], float]) -> float:
        if key not in self.data:
            self.data[key] = float(compute())
            self._dirty = True
        return self.data[key]

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        merged = {}
        if self.path.exists():
            d = json.loads(self.path.read_text())
            if d.get("version") == VERSION:
                merged = d["results"]
        merged.update(self.data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": VERSION, "results": merged}))
        tmp.replace(self.path)
        self._dirty = False


class Scorer:
    """Scores built samples against Munt. `render(tag, key)` returns the reference (a Munt
    render at `sr`, from its onset) of the sound named `tag` (a program, or a sound_tag) at
    `key`; references are held `seconds` (long enough to hear a 16 KB loop repeat)."""

    def __init__(self, render: Callable[[object, int], np.ndarray], sr: int, seconds: float = 3.0,
                 cache: Path | None = None):
        self.render, self.sr, self.seconds = render, sr, seconds
        self.cache = Cache(cache)
        self._ref = functools.lru_cache(maxsize=256)(self._ref_pattern)

    def _ref_pattern(self, tag, key: int) -> np.ndarray:
        x = self.render(tag, key)[:int(self.seconds * self.sr)]
        return loudness_pattern(to_doc_rate(x, self.sr))

    def note_db(self, s, tag, key: int, drum: bool = False, semis: float | None = None) -> float:
        """Score (dB) of sample `s` playing `key` against reference `tag` at `key`; `semis`
        defaults to key - s.key0 (a drum's offset is passed)."""
        sh = key - s.key0 if semis is None else semis
        k = f"{sample_tag(s)}:{tag}:{key}:{round(sh, 3)}:{int(drum)}:{self.seconds}"

        def compute() -> float:
            got = loudness_pattern(play_sample(s, sh, self.seconds))
            return db(distance(self._ref(tag, key), got, drum))
        return self.cache.get(k, compute)

    def sample_db(self, s, tag, keys: dict[int, float], drum: bool = False) -> float:
        """Weighted mean score over `keys` (key -> weight, e.g. held note-seconds)."""
        tot = sum(keys.values()) or 1.0
        return sum(w * self.note_db(s, tag, k, drum) for k, w in keys.items()) / tot


def pick_keys(weights: dict[int, float], n: int = 5) -> dict[int, float]:
    """At most `n` keys standing for a weighted key set: the weighted quantiles, each carrying
    the weight of the keys nearest it."""
    ks = sorted(k for k, w in weights.items() if w > 0)
    if len(ks) <= n:
        return {k: weights[k] for k in ks}
    w = np.array([weights[k] for k in ks], dtype=np.float64)
    c = np.cumsum(w) / w.sum()
    picks = sorted({ks[int(np.searchsorted(c, (i + 0.5) / n))] for i in range(n)})
    out = {k: 0.0 for k in picks}
    for k, wk in zip(ks, w):
        out[min(picks, key=lambda p: (abs(p - k), p))] += float(wk)
    return out
