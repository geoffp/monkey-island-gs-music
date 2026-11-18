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
  live how alive the whole sound is: per modulation band, the depth averaged over the
       auditory bands, compared as a ratio (|log10|, floor LIVE_FLOOR): losing nearly all of
       a sound's movement (a 512 B loop of a shimmering organ) is heard as flat, and per band
       the linear mod term scored it even with a 16 KB loop that adds a slow pulse.
Melodic sounds score seg + w x mod + l x live (`docsim2_mod_weight`, 1 by default;
`docsim2_live_weight`, 0 by default). The trials use w = 1, l = 0.2: all eight listening
verdicts hold, by 1.6 dB or more, and they hold for any w in 0-3 with l in 0.1-1. Without
live (w = 3, the first trials) MI153's opening organ loop came out even (docs/similarity-
measures.md). Drums (one-shots) score the mean over the whole hit only (a
decay is not movement, and a 37 ms bongo is one frame), with forward masking: each band's
excitation decays no faster than a 25 ms time constant after a loud frame, so the 8-bit noise
and drop-sample images in a hit's quiet tail count as the ear hears them (fitted to ISO 532-1
time-varying Zwicker loudness on Conga, Kick, Tom and Snare: within 0.05; without it they
scored 1.5-3x worse; PEAQ's time-domain spreading uses 8-30 ms).
`db()` gives a score as 20 log10(distance): halving the distance gains 6 dB.

Why: docs/similarity-measures.md (bake-off against listening verdicts and ISO 532-1 Zwicker
loudness). docsim.py stays, unchanged, for the default builds.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import docsim
from .audio import quantise8
from .docram import DOC_RATE
from .docsim import key_hz
from .loopbuild import play
from .scorecache import Cache

FRAME, HOP = 1024, 512
LEVEL_DB = 70.0                  # nominal SPL of the RMS-normalised note
BAND_BARK = 0.5
F_LO, F_HI = 40.0, 12000.0
SEGMENTS = ((0.03, 0.15), (0.15, 0.6), (0.6, 1.5), (1.5, None))
MOD_BANDS = ((0.5, 2.0), (2.0, 4.0), (4.0, 8.0), (8.0, 16.0))
MOD_FROM_S = 0.15                # modulation is measured in the sustain, after the attack
LIVE_FLOOR = 0.01                # loudness-depth floor of the live term, near AM detection threshold
VIB_BAND = (3.0, 9.0)             # Hz: vibrato's rates (the whistle's is about 5.5 Hz)
VIB_DEAD_CENTS = 12.0            # the tracker reads up to ~10 cents (12 clears it) on a steady note (organs, strings): not vibrato
VIB_FLOOR_CENTS = 5.0            # ratio floor above the dead zone
TRACK_RANGE_DB = 40.0           # the pitch track follows frames within this of the loudest
VIB_MIN_HZ = 100.0               # below this a 2048-sample frame holds too few periods to track
VERSION = 2                      # bump when the measure changes (invalidates the disk cache)


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


DRUM_FORWARD_S = 0.025


def loudness_pattern(y: np.ndarray, fs: float = DOC_RATE, forward_s: float = 0.0) -> np.ndarray:
    """Specific loudness (bands x frames, frame rate fs / HOP) of `y`, RMS-normalised;
    `forward_s` > 0 adds forward masking with that time constant (drums)."""
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
    E = spread @ E
    if forward_s > 0:
        a = np.float32(np.exp(-HOP / fs / forward_s))
        for t in range(1, E.shape[1]):
            np.maximum(E[:, t], E[:, t - 1] * a, out=E[:, t])
    return (1 + E) ** 0.23 - 1                          # threshold after ear weighting: 0 dB SPL


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


def live_distance(a: np.ndarray, b: np.ndarray, fs: float = DOC_RATE, floor: float = LIVE_FLOOR) -> float:
    """How alive the whole sound is: per modulation band, the depth averaged over the auditory
    bands (weighted by the reference's loudness), compared as a ratio, mean |log10| over the
    modulation bands. Depth is heard as a ratio (modulation-depth discrimination is roughly
    constant in dB: Wakefield & Viemeister 1990), so a loop that loses nearly all of a sound's
    movement (MI153's Pipe Organ shimmer: 2-8 Hz depth 0.034 -> 0.004 at 512 B) costs far more
    than one that adds a slow pulse to it (0.5-2 Hz 0.012 -> 0.050 at 16 KB). Per auditory band
    (mod_distance) the two came out even; the user heard the short loop as flat and buzzy."""
    t = min(a.shape[1], b.shape[1])
    da, w = modulation(a[:, :t], fs)
    dg, _ = modulation(b[:, :t], fs)
    if not np.sum(w):
        return 0.0
    ga, gg = (w[:, None] * da).sum(0) / w.sum(), (w[:, None] * dg).sum(0) / w.sum()
    return float(np.mean(np.abs(np.log10((gg + floor) / (ga + floor)))))


def pitch_vibrato(y: np.ndarray, fs: float, f0: float, t0: float = MOD_FROM_S + 0.25) -> float:
    """Amplitude (cents) of the pitch's swing at vibrato rates (VIB_BAND) over the sustain: the
    strongest partial within 10 % of the note's pitch `f0` is followed frame by frame (2048
    samples, hop 256, peak found on a 8x zero-padded FFT), its pitch in cents is band-passed to
    VIB_BAND, and the result is that band's RMS x sqrt(2) (the peak of a sine). 0 when the note
    is too low to track or has nothing near f0. The loudness measure can't hear this: a +-25
    cent vibrato moves a 0.5-Bark band's loudness only a little, and the MT-32's Whistle 2
    has it (Atlantis 48) while a 512 B loop of it has none."""
    from scipy.signal import butter, sosfiltfilt
    y = np.asarray(y, dtype=np.float64)[int(t0 * fs):]
    win, hop = 2048, 256
    if f0 < VIB_MIN_HZ or len(y) < win * 3:
        return 0.0
    w = np.hanning(win)
    fr = np.fft.rfftfreq(win * 8, 1 / fs)
    lo, hi = np.searchsorted(fr, f0 * 0.9), np.searchsorted(fr, f0 * 1.1)
    peaks, level = [], []
    for s0 in range(0, len(y) - win, hop):
        X = np.abs(np.fft.rfft(y[s0:s0 + win] * w, win * 8))[lo:hi]
        peaks.append(fr[lo + int(np.argmax(X))] if X.max() > 0 else 0.0)
        level.append(float(X.max()) if len(X) else 0.0)
    peaks, level = np.array(peaks), np.array(level)
    # The track stops at the last frame within TRACK_RANGE_DB of the loudest: a decayed or
    # ended note (a one-shot past its end, `long_shots`; a sound that stops before its
    # note-off) has no pitch left to follow, and its silence or 8-bit noise read as 40-1100
    # cents of "vibrato" (MI162's Ice Rain one-shots, Atlantis's effect presets).
    if len(level) and level.max() > 0:
        live = np.nonzero(level >= level.max() * 10 ** (-TRACK_RANGE_DB / 20))[0]
        peaks = peaks[:live[-1] + 1]
    if np.count_nonzero(peaks) < len(peaks) // 2 or len(peaks) < 16:
        return 0.0
    cents = 1200 * np.log2(np.maximum(peaks, 1.0) / np.median(peaks[peaks > 0]))
    rate = fs / hop
    sos = butter(2, VIB_BAND, "bp", fs=rate, output="sos")
    return float(np.std(sosfiltfilt(sos, cents)) * np.sqrt(2))


def vib_distance(ref_cents: float, got_cents: float, dead: float = VIB_DEAD_CENTS,
                 floor: float = VIB_FLOOR_CENTS) -> float:
    """|log10| of the two vibrato depths (depth is heard as a ratio, as for `live`), each as the
    excess over `dead` (the tracker's own wobble on a steady note: organs and strings read 5-10
    cents, which as a ratio would have charged their long loops for noise) plus `floor`: a lost
    27 cents vibrato is 0.7, any two steady notes 0."""
    return float(abs(np.log10((max(got_cents - dead, 0.0) + floor) / (max(ref_cents - dead, 0.0) + floor))))


MOVE_BAND = (0.7, 12.0)          # Hz: rates at which a sound's own movement repeats (LFO vibrato or tremolo, beating)
MOVE_MIN_CENTS = 10.0            # a sound moves if its pitch swings this far (Sax 12, Chorale 18, Whistle 2 28;
                                 # Panpipes and Trombone, 7, don't)
MOVE_MIN_DB = 2.0                # level swing below this isn't heard as movement
MOVE_MIN_SHARE = 0.6             # of the track's motion in MOVE_BAND at the one rate (and its 2nd harmonic)
MOVE_RATE_TOL = 0.04             # the rate found in each half of the sustain agrees within this


@dataclasses.dataclass(frozen=True)
class Movement:
    """A sound's own periodic movement, measured on the reference alone: its pitch swing
    (vibrato) and its level swing (tremolo, two partials beating), each as (rate Hz, peak
    depth, regular?: one rate holds the motion, the same through the note). `hz` is the rate a
    loop should hold whole cycles of: the vibrato's if it is periodic (regular and deep enough),
    else the level's, else 0 (still, or moving without a period: chorus)."""
    fm_hz: float = 0.0
    fm_cents: float = 0.0
    fm_regular: bool = False
    am_hz: float = 0.0
    am_db: float = 0.0
    am_regular: bool = False

    @property
    def fm_periodic(self) -> bool:
        return self.fm_regular and self.fm_cents >= MOVE_MIN_CENTS

    @property
    def am_periodic(self) -> bool:
        return self.am_regular and self.am_db >= MOVE_MIN_DB

    @property
    def hz(self) -> float:
        return self.fm_hz if self.fm_periodic else self.am_hz if self.am_periodic else 0.0

    def rate(self, kind: str) -> float:
        """The rate of `kind` ("fm" or "am") movement if it is regular here, else 0."""
        return (self.fm_hz if self.fm_regular else 0.0) if kind == "fm" else (self.am_hz if self.am_regular else 0.0)


def _line(track: np.ndarray, rate: float) -> tuple[float, float, float]:
    """(rate Hz, peak depth, share) of the strongest line of `track` (sampled at `rate`) in
    MOVE_BAND: depth is the line's amplitude, share the part of the band's power within the
    line's main lobe and its 2nd harmonic's."""
    y = track - np.mean(track)
    y = y - np.polyval(np.polyfit(np.arange(len(y)), y, 1), np.arange(len(y)))     # a drift isn't movement
    w = np.hanning(len(y))
    n = 1 << 15
    X = np.abs(np.fft.rfft(y * w, n))
    f = np.fft.rfftfreq(n, 1 / rate)
    band = (f >= MOVE_BAND[0]) & (f <= MOVE_BAND[1])
    if not band.any() or not X[band].max() > 0:
        return 0.0, 0.0, 0.0
    i = int(np.argmax(np.where(band, X, 0.0)))
    lobe = 2.0 * rate / len(y)                      # a Hann main lobe's half width (Hz)
    near = (np.abs(f - f[i]) <= lobe) | (np.abs(f - 2 * f[i]) <= lobe)
    wide = (f >= MOVE_BAND[0] / 2) & (f <= 2 * MOVE_BAND[1])
    share = float(np.sum(X[near & wide] ** 2) / (np.sum(X[wide] ** 2) or 1.0))
    return float(f[i]), float(2 * X[i] / np.sum(w)), share


def movement(x: np.ndarray, fs: float, f0: float, t0: float = 0.5, t1: float | None = None) -> Movement:
    """The periodic movement of a held note `x` (from its onset) between `t0` and `t1` s: the
    partial near `f0` is followed (1024-sample frames at 32 kHz, hop an eighth), giving a pitch
    track (cents) and a level track (dB, of that partial: an organ's beating moves its
    partials, not always its overall level). Each track's strongest line in MOVE_BAND counts as
    periodic movement when it is deep enough (MOVE_MIN_CENTS, MOVE_MIN_DB), holds most of the
    track's motion (MOVE_MIN_SHARE) and has the same rate in both halves of the span
    (MOVE_RATE_TOL). Whistle 2: 6.16 Hz +-28 cents at every key; Str Sect 3: 2.0 Hz +-8 dB."""
    y = np.asarray(x, dtype=np.float64)[int(t0 * fs):int(t1 * fs) if t1 else None]
    win = 1 << int(round(np.log2(fs * 0.032)))
    hop = win // 8
    if f0 * win / fs < 4 or len(y) < win * 8:           # under 4 periods a frame, or under 8 frames
        return Movement()
    w = np.hanning(win)
    fr = np.fft.rfftfreq(win * 8, 1 / fs)
    lo, hi = np.searchsorted(fr, f0 * 0.9), np.searchsorted(fr, f0 * 1.1)
    pk, amp = [], []
    for s0 in range(0, len(y) - win, hop):
        X = np.abs(np.fft.rfft(y[s0:s0 + win] * w, win * 8))[lo:hi]
        j = int(np.argmax(X))
        d = 0.0
        if 0 < j < len(X) - 1 and X[j - 1] > 0 and X[j + 1] > 0:    # parabola through the log peak: the bins
            a, b, c = np.log(X[j - 1:j + 2])                       # alone are 26 cents apart at middle C
            d = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0.0
        pk.append(fr[lo + j] + d * (fr[1] - fr[0]))
        amp.append(X[j])
    pk, amp = np.array(pk), np.array(amp)
    if not amp.max() > 0 or np.median(amp) < 0.05 * amp.max():
        return Movement()
    rate = fs / hop
    tracks = (1200 * np.log2(np.maximum(pk, 1.0) / np.median(pk)),
              20 * np.log10(np.maximum(amp, 1e-6 * amp.max()) / np.median(amp)))
    out = []
    for tr in tracks:
        hz, depth, share = _line(tr, rate)
        h = len(tr) // 2
        halves = [_line(tr[:h], rate)[0], _line(tr[h:], rate)[0]]
        same = hz > 0 and all(abs(v - hz) <= max(MOVE_RATE_TOL * hz, 0.6 * rate / h) for v in halves)
        out += [hz, depth, bool(share >= MOVE_MIN_SHARE and same)]
    return Movement(*out)


def sound_kind(moves: list[Movement]) -> str:
    """One verdict for a sound from its movement at several keys: "fm" (a vibrato), "am" (a
    level swing) or "" (none): the kind that is regular at most of the keys with a median depth
    over its threshold. Decided per sound, not per key: Sax's vibrato reads 11.3, 12.0 and 12.6
    cents at three keys, and a threshold per key would give its zones different loops."""
    for kind, depth, least in (("fm", lambda v: v.fm_cents, MOVE_MIN_CENTS), ("am", lambda v: v.am_db, MOVE_MIN_DB)):
        if moves and sum(bool(v.rate(kind)) for v in moves) * 2 > len(moves) and np.median([depth(v) for v in moves]) >= least:
            return kind
    return ""


FINE_S = 0.15                    # block length of the fine term (s)
FINE_FLOOR = 0.1                 # blocks whose loudness is below this x the loudest block's are left out (~40 dB)


def fine_distance(a: np.ndarray, b: np.ndarray, fs: float = DOC_RATE) -> float:
    """How the timbre changes through a decaying note: seg's relative difference of band
    means, over FINE_S blocks from 0.15 s while the reference still sounds (its block
    loudness at least FINE_FLOOR x its loudest), blocks weighted equally. seg's mid segment
    (0.6-1.5 s) averages a slide away: MI162's Ice Rain has a partial gliding 3.6 -> 2.5 kHz
    over 2.4 s, louder than the fundamental after 0.6 s, which a loop freezes; per block the
    loop is 0.30-0.43 off and the whole sound 0.09-0.34, and seg rated them 0.34 and 0.26.
    For decaying sounds only (`docsim2_fine_weight`): over a sustain, blocks shorter than a
    slow wobble's cycle would charge a loop for being out of phase with it."""
    t = min(a.shape[1], b.shape[1])
    n = max(2, _frames(FINE_S, fs))
    blocks = [(i, i + n) for i in range(_frames(0.15, fs), t - n + 1, n)]
    if not blocks:
        return 0.0
    loud = np.array([a[:, i0:i1].mean(axis=1).sum() for i0, i1 in blocks])
    out = []
    for (i0, i1), s in zip(blocks, loud):
        if s >= FINE_FLOOR * loud.max() and s > 0:
            ma, mb = a[:, i0:i1].mean(axis=1), b[:, i0:i1].mean(axis=1)
            out.append(float(np.sum(np.abs(mb - ma)) / s))
    return float(np.mean(out)) if out else 0.0


def mean_distance(a: np.ndarray, b: np.ndarray) -> float:
    """sum|mean_b - mean_a| / sum mean_a over the whole of both patterns (drums)."""
    t = min(a.shape[1], b.shape[1])
    ma, mb = a[:, :t].mean(axis=1), b[:, :t].mean(axis=1)
    return float(np.sum(np.abs(mb - ma)) / (np.sum(ma) or 1.0))


def distance(ref: np.ndarray, got: np.ndarray, drum: bool = False, fs: float = DOC_RATE,
             mod_weight: float = 1.0, live_weight: float = 0.0) -> float:
    """Melodic: seg + mod_weight x mod + live_weight x live between two loudness patterns
    (reference first). Drum:
    the whole hit's mean (make drum patterns with forward_s=DRUM_FORWARD_S)."""
    if drum:
        return mean_distance(ref, got)
    d = seg_distance(ref, got, fs) + mod_weight * mod_distance(ref, got, fs)
    return d + live_weight * live_distance(ref, got, fs) if live_weight else d


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
    if getattr(s, "shot_env", False) and len(s.env_t) > 1:
        # a flattened long one-shot: its decay from the row volumes, in sample time (a lower
        # note plays the flattened audio slower, and its decay with it: ntpsong does the same)
        tt = np.arange(len(y)) / DOC_RATE * 2 ** (semis / 12)
        y *= np.where(tt < s.env_t[0], 1.0, np.interp(tt, s.env_t, s.env_g))
    return y


def sample_tag(s) -> str:
    """What decides a sample's playback: its bytes, loop, rate, key and contour."""
    h = hashlib.sha1()
    h.update(s.mod_bytes())
    h.update(f"{s.loop_start}:{s.loop_len}:{round(float(s.rate), 2)}:{s.key0}".encode())
    h.update(np.round(np.asarray(s.env_t, dtype=np.float64), 4).tobytes())
    h.update(np.round(np.asarray(s.env_g, dtype=np.float64), 4).tobytes())
    return h.hexdigest()[:16]


class Scorer:
    """Scores built samples against Munt. `render(program, key)` returns the reference (a
    Munt render at `sr`, from its onset); references are held `seconds` (long enough to hear a
    16 KB loop repeat). `name(program)` names the sound in the cache (ntpmod.doc_key: a
    custom timbre or a driver set-up is not the preset of the same number)."""

    def __init__(self, render: Callable[[object, int], np.ndarray], sr: int, seconds: float = 3.0,
                 cache: Path | None = None, name: Callable[[object], object] = lambda p: p,
                 mod_weight: float = 1.0, live_weight: float = 0.0, vib_weight: float = 0.0,
                 fine_weight: float = 0.0):
        self.render, self.sr, self.seconds, self.name = render, sr, seconds, name
        self.mod_weight, self.live_weight, self.vib_weight = mod_weight, live_weight, vib_weight
        self.fine_weight = fine_weight           # decaying sounds' samples only (fine_distance)
        self.cache = Cache(cache, VERSION)
        self._ref = functools.lru_cache(maxsize=256)(self._ref_pattern)
        self._ref_vib = functools.lru_cache(maxsize=256)(self._ref_vibrato)

    def _ref_pattern(self, tag, key: int) -> np.ndarray:
        x = self.render(tag, key)[:int(self.seconds * self.sr)]
        return loudness_pattern(to_doc_rate(x, self.sr))

    def _ref_vibrato(self, tag, key: int) -> float:
        x = self.render(tag, key)[:int(self.seconds * self.sr)]
        return pitch_vibrato(to_doc_rate(x, self.sr), DOC_RATE, key_hz(key))

    def note_db(self, s, tag, key: int, drum: bool = False, semis: float | None = None) -> float:
        """Score (dB) of sample `s` playing `key` against program `tag`'s note `key` (another
        program than the sample's own prices a merge); `semis` defaults to key - s.key0 (a
        drum's offset is passed)."""
        sh = key - s.key0 if semis is None else semis
        fine = self.fine_weight if not drum and not getattr(s, "steady", False) else 0.0
        k = (f"{sample_tag(s)}:{self.name(tag)}:{key}:{round(sh, 3)}:{int(drum)}:{self.seconds}:w{self.mod_weight:g}"
             f"{self._live}{self._vib}" + (f"f{fine:g}" if fine else ""))

        def compute() -> float:
            y = play_sample(s, sh, self.seconds)
            ref, got = self._ref(tag, key), loudness_pattern(y)
            d = distance(ref, got, drum, mod_weight=self.mod_weight, live_weight=self.live_weight)
            if fine:
                d += fine * fine_distance(ref, got)
            if self.vib_weight and not drum:
                d += self.vib_weight * vib_distance(self._ref_vib(tag, key),
                                                    pitch_vibrato(y, DOC_RATE, key_hz(key)))
            return db(d)
        return self.cache.get(k, compute)

    @property
    def _live(self) -> str:
        return f"l{self.live_weight:g}" if self.live_weight else ""

    @property
    def _vib(self) -> str:
        return f"v{self.vib_weight:g}e" if self.vib_weight else ""      # c: with the dead zone; e: tracks sounding frames only

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


class NoteError:
    """docsim.NoteError's interface on this measure, for the pitch plan (`pitch_measure =
    "docsim2"`): err(k, rate, n) is the score (dB) of key n played from the ideal sample of
    key k at `rate` (Munt's render of k stored whole at that rate, 8 bits), over the whole
    held note, against Munt's render of n. `render(key)` gives a render at `sr` from its
    onset; results are cached on disk under `name` (ntpmod.doc_key)."""

    def __init__(self, name, render: Callable[[int], np.ndarray], sr: int, cache: Path | None = None,
                 mod_weight: float = 1.0, live_weight: float = 0.0):
        self.name, self.render, self.sr, self._docsim = name, render, sr, docsim
        self.mod_weight, self.live_weight = mod_weight, live_weight
        self.cache = Cache(cache, VERSION)
        self._ref = functools.lru_cache(maxsize=None)(lambda n: loudness_pattern(to_doc_rate(self.render(n), self.sr)))
        self._sample = functools.lru_cache(maxsize=64)(lambda k, r: docsim.stored(self.render(k), r))

    def __call__(self, k: int, rate: float, n: int) -> float:
        def compute() -> float:
            r = round(rate)
            s = self._sample(k, r)
            seconds = len(self.render(k)) / self.sr
            y = self._docsim.doc_play(s, r, n - k, seconds)
            return db(distance(self._ref(n), loudness_pattern(y), mod_weight=self.mod_weight,
                               live_weight=self.live_weight))
        live = f"l{self.live_weight:g}" if self.live_weight else ""
        return self.cache.get(f"pitch:{self.name}:{k}:{round(rate)}:{n}:w{self.mod_weight:g}{live}", compute)

    def save(self) -> None:
        self.cache.save()


def drum_db(ref: np.ndarray, sr: int, sample: np.ndarray, rate: float, semis: float, seconds: float) -> float:
    """docsim.drum_error_db's interface on this measure (whole-hit mean, forward masking): the DOC playing
    `sample` (values -1..1, rounded to 8 bits here, stored at `rate`) `semis` above its key,
    against `ref` (Munt's render of the played key, from its onset), over `seconds` + 20 ms."""
    n = int((seconds + 0.02) * DOC_RATE)
    q = quantise8(sample)
    got = docsim.doc_play(q, rate, semis, n / DOC_RATE)
    got = np.pad(got, (0, max(0, n - len(got))))
    want = to_doc_rate(ref[:int(n / DOC_RATE * sr) + 1], sr)[:n]
    return db(distance(loudness_pattern(want, forward_s=DRUM_FORWARD_S),
                       loudness_pattern(got, forward_s=DRUM_FORWARD_S), drum=True))


def a_weight(f: np.ndarray) -> np.ndarray:
    """IEC 61672 A-weighting (amplitude)."""
    f2 = np.asarray(f, dtype=np.float64) ** 2
    return (12194.0 ** 2 * f2 ** 2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
                                      * (f2 + 12194.0 ** 2))


def inharmonic_db(y: np.ndarray, fs: float, f0: float, t0: float = 0.3, t1: float = 1.8) -> float:
    """Share of a held note's energy (A-weighted, 20 Hz - 5 kHz, t0..t1 s) that lies between
    the note's harmonics and its sub-octave's, in dB: drop-sample images and a sample's
    beating carried to keys where the MT-32 has none sound as a buzz (MI153's pipe organ:
    docs/similarity-measures.md)."""
    seg = np.asarray(y[int(t0 * fs):int(t1 * fs)], dtype=np.float64)
    if len(seg) < 1024:
        return float("nan")
    nfft = 1 << int(np.ceil(np.log2(len(seg) * 4)))
    P = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), nfft)) ** 2
    f = np.fft.rfftfreq(nfft, 1 / fs)
    P *= a_weight(np.maximum(f, 1.0)) ** 2
    band = (f > 20) & (f < 5000)
    h = f / (f0 / 2)
    near = np.abs(h - np.round(h)) * (f0 / 2) < np.maximum(6.0, 0.015 * f0)
    tot = P[band].sum() or 1.0
    return float(10 * np.log10(P[band & ~near].sum() / tot + 1e-12))
