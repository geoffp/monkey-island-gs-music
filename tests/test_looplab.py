# SPDX-License-Identifier: GPL-3.0-or-later
"""The loop lab's metrics and searches (looplab: seam step, seam flux, seam search, tuning, the
handover check on an aligned join), on synthetic tones with known answers. Loop construction
itself is tested in test_loopbuild."""

import numpy as np

from loop_fixtures import SR, cycle, tone
from migs import loopbuild as lb
from migs import looplab as ll
from migs import ntpsamples as smp


def test_seam_step_flags_a_discontinuity():
    c = np.round(cycle(64) * 100)
    loop = np.tile(c, 8)
    assert ll.seam_step(loop) <= 1.0
    bad = loop.copy()
    bad[-1] += 60
    assert ll.seam_step(bad) > 2


def test_seam_flux_is_zero_for_a_periodic_loop_and_positive_for_a_jump():
    loop = np.tile(cycle(32), 16)
    assert abs(ll.seam_flux(loop, 16)) < 0.05
    jump = loop.copy()
    jump[-64:] *= 0.5
    assert ll.seam_flux(jump, 16) > 1.0


def test_whole_cycle_tuning_divides_the_loop():
    tables = {0: [int(round(smp.PAL / (8363 * 2 ** ((n - 24) / 12)))) for n in range(smp.NOTES)]}
    err, k, rate, n0, ft = ll.tune(392.0, tables, 6000, 26000, 512, 67, 60, 72, whole_cycles=True)
    assert 512 % k == 0 and 6000 <= rate <= 26000


def test_searched_raw_loop_beats_a_fixed_start_on_a_detuned_tone():
    f0 = 200.0
    x = tone(f0, 2.0, detune_hz=2.5)
    period = SR / f0
    k = 10
    span = k * period
    fixed = 0.4 * SR
    starts = np.arange(0.2 * SR, 1.5 * SR, period / 4)
    best = min(ll.seam_score(x, s0, span, period) for s0 in starts)
    assert best < ll.seam_score(x, fixed, span, period)


def test_aligned_join_keeps_level_and_zero_start():
    f0 = 220.0
    x = np.concatenate([np.zeros(500), tone(f0, 2.0, detune_hz=0.0)])
    period = SR / f0
    k, L = 16, 512
    rate = L * f0 / k
    loop = lb.loop_power(x, 600 + 4000, 600 + 20000, period, k, L)
    attack, lp = lb.join_aligned(x, 500, period, rate, k, 1016, loop)
    assert lp[0] == 0.0
    qa, ql = lb.to_bytes(attack, lp)
    lvl, jump = ll.handover(qa, ql, k)
    assert abs(lvl) < 0.5 and jump < 1.0
    assert smp.loop_buzz_db(ql, k) == -200.0
