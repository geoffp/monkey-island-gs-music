# SPDX-License-Identifier: GPL-3.0-or-later
"""Choose where to sample each melodic sound: its sampled key(s), key zones and sample rate.

A sound plays every key from one sample (or one per zone). Each played key n then costs
docsim's error of the zone's sample (sampled key k, rate r) playing n. The plan minimises the
duration-weighted mean of that error over the notes the songs play (one song for per-song
sets, all songs for the shared set):

- candidates: every key from the lowest to the highest played, each at the rates its loop
  allows. An E'+ 512 B loop holds a whole number of cycles that divides 512, so the rate is
  the key's frequency times a power of two (`loop_rates`); raw loops tune within the window
  later, around the same rate;
- zones: contiguous runs of played keys; for each count the best split is found exactly
  (dynamic programming over prefix sums);
- a candidate is valid for a zone only if the zone's keys stay inside NTP's 72-note period
  table once the rate fixes the sample's base note (`span_ok`).

Measurements: docs/ninjatrackerplus.md "Sampled pitch and rate".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np

from .docsim import key_hz


@dataclass(frozen=True)
class Candidate:
    key: int          # sampled key
    rate: float       # Hz
    lo_ok: int        # lowest key this sample can play within the period table
    hi_ok: int        # highest


@dataclass(frozen=True)
class Zone:
    lo: int           # lowest played key in the zone
    hi: int           # highest
    key: int          # sampled key
    rate: float
    error_db: float   # duration-weighted mean over the zone's notes


@dataclass(frozen=True)
class Plan:
    zones: tuple[Zone, ...]
    error_db: float   # duration-weighted mean over all notes

    @property
    def count(self) -> int:
        return len(self.zones)


def loop_rates(key: int, loop_bytes: int, min_rate: float, max_rate: float) -> list[float]:
    """Rates at which `loop_bytes` hold a whole number of the key's cycles dividing it (E'+:
    every cycle the same whole number of samples), highest first."""
    f = key_hz(key)
    ks = [k for k in range(1, loop_bytes + 1) if loop_bytes % k == 0]
    return sorted((loop_bytes * f / k for k in ks if min_rate <= loop_bytes * f / k <= max_rate), reverse=True)


def candidates(lo: int, hi: int, rates_for: Callable[[int], list[float]],
               base_note: Callable[[int, float], int | None], notes: int = 72) -> list[Candidate]:
    """Every key lo..hi at each of its rates. `base_note(key, rate)` gives the period-table note
    that plays the sample at its own pitch (None: no tuning); the sample can then play keys
    key - base .. key + notes - 1 - base."""
    out = []
    for k in range(lo, hi + 1):
        for r in rates_for(k):
            n0 = base_note(k, r)
            if n0 is not None:
                out.append(Candidate(k, r, k - n0, k + notes - 1 - n0))
    return out


def plan(weights: Mapping[int, float], cands: list[Candidate], error: Callable[[int, float, int], float],
         max_zones: int = 1) -> list[Plan]:
    """The best plan for 1..max_zones zones (fewer if there are fewer played keys).
    `weights`: played key -> weight (note seconds); `error(k, rate, n)`: dB."""
    keys = sorted(k for k, w in weights.items() if w > 0)
    if not keys or not cands:
        return []
    w = np.array([weights[k] for k in keys], dtype=np.float64)
    e = np.array([[error(c.key, c.rate, n) for n in keys] for c in cands])         # (C, m)
    pre = np.concatenate([np.zeros((len(cands), 1)), np.cumsum(e * w, axis=1)], axis=1)
    wpre = np.concatenate([[0.0], np.cumsum(w)])
    lo_ok = np.array([c.lo_ok for c in cands])
    hi_ok = np.array([c.hi_ok for c in cands])
    m = len(keys)
    # best[i][j]: (weighted error sum, candidate) of keys[i..j]
    best = {}
    for i in range(m):
        for j in range(i, m):
            ok = (lo_ok <= keys[i]) & (hi_ok >= keys[j])
            if not ok.any():
                continue
            s = np.where(ok, pre[:, j + 1] - pre[:, i], np.inf)
            c = int(np.argmin(s))
            best[i, j] = (float(s[c]), c)
    plans = []
    # dp[z][j]: (cost, splits) for keys[0..j] in z zones
    dp = {0: {-1: (0.0, ())}}
    for z in range(1, min(max_zones, m) + 1):
        dp[z] = {}
        for j in range(m):
            opts = [(dp[z - 1][i][0] + best[i + 1, j][0], dp[z - 1][i][1] + ((i + 1, j),))
                    for i in dp[z - 1] if i < j and (i + 1, j) in best]
            if opts:
                dp[z][j] = min(opts, key=lambda o: o[0])
        if m - 1 not in dp[z]:
            continue
        cost, spans = dp[z][m - 1]
        zones = []
        for i, j in spans:
            s, c = best[i, j]
            zones.append(Zone(keys[i], keys[j], cands[c].key, cands[c].rate, s / (wpre[j + 1] - wpre[i])))
        plans.append(Plan(tuple(zones), cost / wpre[m]))
    return plans


def zone_ranges(zones: tuple[Zone, ...], lo: int = 0, hi: int = 127) -> list[tuple[int, int]]:
    """Key ranges covering lo..hi: each zone reaches halfway to the next one's played keys."""
    out, start = [], lo
    for z, nxt in zip(zones, zones[1:]):
        cut = (z.hi + nxt.lo) // 2
        out.append((start, cut))
        start = cut + 1
    out.append((start, hi))
    return out


def describe(p: Plan) -> str:
    """One line: each zone's played keys, sampled key, rate and error."""
    return " | ".join(f"{z.lo}-{z.hi}: key {z.key} @ {z.rate:.0f} Hz {z.error_db:+.1f} dB" for z in p.zones)


def gain_db(a: Plan, b: Plan) -> float:
    """How much better b is than a (dB, positive = better)."""
    return a.error_db - b.error_db

