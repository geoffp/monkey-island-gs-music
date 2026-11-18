# SPDX-License-Identifier: GPL-3.0-or-later
"""MOD volumes for the real DOC's volume curve (NTP build option `doc_volume = "measured"`).

The NTP build sets each note's MOD volume (0–64) as if the DOC were linear, which every emulator
is. The real chip isn't: measured on a IIgs (config/doc-volume.toml, from the recording in
measurements/docsweep/; docs/es5503.md), quiet
volumes come out 10–30 dB below linear and MOD 1–6 (DOC 3–23) are all but silent. `volume_map`
gives, for each volume the build wants, the MOD volume whose measured level is closest to it
(roughly m -> 7 + m * 57/64), so a real IIgs plays the levels the MT-32 did. MOD 0 stays 0.
"""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

CURVE = Path("config/doc-volume.toml")   # written by `just doc-curve` from measurements/docsweep/


def load_curve(path: Path = CURVE) -> list[float]:
    """Levels in dB of MOD volumes 1..64 (index 0 = volume 1), relative to volume 64."""
    if not path.is_file():
        raise SystemExit(f"{path}: no DOC volume curve (just doc-curve writes it from measurements/docsweep/)")
    levels = tomllib.loads(path.read_text())["level_db"]
    if len(levels) != 64:
        raise SystemExit(f"{path}: level_db needs 64 values (MOD volumes 1..64), not {len(levels)}")
    return [float(v) for v in levels]


DIP_TOLERANCE_DB = 0.5   # measurement noise near full volume (steps there are 0.1-0.2 dB)


def volume_map(levels: list[float]) -> list[int]:
    """map[m] = the MOD volume to write for a wanted linear volume m (0..64).

    The curve's foot is not monotonic (a leak floor and a null around DOC 20), and a louder
    setting must never sound quieter. So a volume is a candidate only if it measures no more than
    DIP_TOLERANCE_DB below a lower volume (the null is 4 dB below the floor; noise is ~0.1 dB).
    Each wanted level, 20·log10(m/64), takes the candidate nearest in dB, and the written volume
    never goes down as the wanted one goes up."""
    candidates, top = [], -math.inf
    for k, lv in enumerate(levels, start=1):
        if lv >= top - DIP_TOLERANCE_DB:
            candidates.append((k, lv))
        top = max(top, lv)
    out = [0]
    for m in range(1, 65):
        want = 20 * math.log10(m / 64)
        k = min(candidates, key=lambda c: (abs(c[1] - want), -c[0]))[0]
        out.append(max(k, out[-1]))
    return out


def measured_map(path: Path = CURVE) -> list[int]:
    return volume_map(load_curve(path))


def written_levels(path: Path | None = CURVE) -> list[float]:
    """Amplitude (re volume 64) of each volume 0..64 as written to the MOD: the measured curve,
    or linear (path None). Volume slides (Axy) step the written volume, so they are planned on
    these (ntpsong, `release_slides`)."""
    if path is None:
        return [v / 64 for v in range(65)]
    return [0.0] + [10 ** (lv / 20) for lv in load_curve(path)]
