# SPDX-License-Identifier: GPL-3.0-or-later
"""MOD volumes for the real DOC's measured volume curve (docvolume)."""

import math

from migs import docvolume


def test_linear_curve_maps_to_itself():
    levels = [20 * math.log10(m / 64) for m in range(1, 65)]
    assert docvolume.volume_map(levels) == list(range(65))


def test_map_skips_the_non_monotonic_foot_and_keeps_full_and_silent():
    # a floor at -48 dB for 1-4, a null at 5-6, then linear in (m - 6): the shape measured
    levels = [-48.0] * 4 + [-52.0] * 2 + [20 * math.log10((m - 6) / 58) for m in range(7, 65)]
    vmap = docvolume.volume_map(levels)
    assert vmap[0] == 0 and vmap[64] == 64
    assert all(v not in (5, 6) for v in vmap)              # never the null
    assert vmap == sorted(vmap)                            # louder wanted, never quieter written
    assert vmap[1] >= 7 and vmap[32] > 32


def test_the_measured_curve_loads():
    vmap = docvolume.measured_map()
    assert len(vmap) == 65 and vmap[0] == 0 and vmap[64] == 64
    assert 6 <= vmap[1] - 1 <= 8                           # the quiet end gains about 7
