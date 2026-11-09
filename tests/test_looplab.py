# SPDX-License-Identifier: GPL-3.0-or-later
"""Loop construction (loopbuild: harmonic tracks, attack detection, E'+ loops and joins) and
the loop lab's metrics (looplab), on synthetic tones with known answers."""

import numpy as np

from migs import loopbuild as lb
from migs import looplab as ll
from migs import munt
from migs import ntpsamples as smp

SR = munt.SR


def tone(f0, secs, amps=(1.0, 0.5, 0.3), detune_hz=0.0, env=None):
    t = np.arange(int(secs * SR)) / SR
    y = np.zeros_like(t)
    for h, a in enumerate(amps, 1):
        g = env(t, h) if env else 1.0
        y += a * g * np.sin(2 * np.pi * h * f0 * t)
        if detune_hz:
            y += a * g * np.sin(2 * np.pi * h * (f0 + detune_hz) * t + 0.3)
    return y / np.max(np.abs(y))


def cycle(n, amps=(1.0, 0.5, 0.3)):
    t = np.arange(n) / n
    return sum(a * np.sin(2 * np.pi * h * t + 0.4 * h) for h, a in enumerate(amps, 1))


# --- metrics ---------------------------------------------------------------

def test_buzz_metric_zero_for_tiled_cycle_and_sees_1pct_cycle_difference():
    c = cycle(32)
    exact = np.tile(c, 16)
    assert smp.loop_buzz_db(exact, 16) == -200.0
    wobbly = exact.copy()
    wobbly[:32] *= 1.01
    assert smp.loop_buzz_db(wobbly, 16) > -60


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


# --- exact periodicity -------------------------------------------------------

def test_whole_cycles_survive_8bit_rounding_but_fractional_ones_buzz():
    fine = cycle(1024, amps=(1.0, 0.6, 0.4, 0.3, 0.2))
    whole = lb.cycle_at_rate(fine, 16, 512)                      # 32 samples per cycle
    frac = lb.cycle_at_rate(fine, 15, 512)                       # 34.13 samples per cycle
    q = lambda y: np.round(y / np.max(np.abs(y)) * 127)
    assert smp.loop_buzz_db(q(whole), 16) == -200.0
    assert smp.loop_buzz_db(q(frac), 15) > -60


def test_exact_zero_start_keeps_every_cycle_identical():
    loop = lb.cycle_at_rate(cycle(1024, amps=(1.0, 0.8, 0.6, 0.5)), 16, 512)
    c = int(np.nonzero((np.roll(loop, 1) < 0) & (loop >= 0))[0][0])
    rough = smp.zero_start(loop, c)                                # linear crossing, forced 0
    exact = lb.zero_start_exact(loop, c)
    assert exact[0] == 0.0
    assert np.max(np.abs(exact[::32])) < 1e-9                     # every cycle starts at 0
    assert smp.loop_buzz_db(exact, 16) < -200 or smp.loop_buzz_db(exact, 16) == -200.0
    assert smp.loop_buzz_db(rough, 16) > smp.loop_buzz_db(exact, 16) + 40


def test_whole_cycle_tuning_divides_the_loop():
    tables = {0: [int(round(smp.PAL / (8363 * 2 ** ((n - 24) / 12)))) for n in range(smp.NOTES)]}
    err, k, rate, n0, ft = ll.tune(392.0, tables, 6000, 26000, 512, 67, 60, 72, whole_cycles=True)
    assert 512 % k == 0 and 6000 <= rate <= 26000


# --- attack detection ----------------------------------------------------------

def tracks_for(y, f0):
    return lb.harmonic_tracks(y, 0, SR / f0)


def test_steady_beating_is_steady_from_the_start():
    y = tone(220, 6.0, detune_hz=4.0, env=lambda t, h: np.minimum(t / 0.02, 1.0))
    st = lb.settle(tracks_for(y, 220))
    assert st.steady
    assert st.attack_s < 0.1
    assert st.mod_s is not None and abs(st.mod_s - 0.25) < 0.03


def test_slow_swell_in_one_harmonic_delays_the_attack_end():
    # harmonic 2 swells over ~1 s; the others are there at once
    env = lambda t, h: np.minimum(t / 0.02, 1.0) * ((1 - np.exp(-t / 0.3)) if h == 2 else 1.0)
    st = lb.settle(tracks_for(tone(220, 6.0, env=env), 220))
    assert st.steady
    assert 0.5 < st.attack_s < 1.5


def test_exponential_decay_is_classed_as_decaying_with_its_rate():
    env = lambda t, h: np.minimum(t / 0.005, 1.0) * 10 ** (-10 * t / 20)   # -10 dB/s
    st = lb.settle(tracks_for(tone(220, 4.0, env=env), 220))
    assert not st.steady
    assert abs(st.decay_db_s + 10) < 1.5
    assert st.attack_s < 0.1


# --- joining ---------------------------------------------------------------------

def test_aligned_join_keeps_level_and_zero_start():
    f0 = 220.0
    x = np.concatenate([np.zeros(500), tone(f0, 2.0, detune_hz=0.0)])
    period = SR / f0
    k, L = 16, 512
    rate = L * f0 / k
    loop = lb.loop_power(x, 600 + 4000, 600 + 20000, period, k, L)
    attack, lp = lb.join_aligned(x, 500, period, rate, k, 1016, loop)
    assert lp[0] == 0.0
    qa, ql = ll.to_bytes(attack, lp)
    lvl, jump = ll.handover(qa, ql, k)
    assert abs(lvl) < 0.5 and jump < 1.0
    assert smp.loop_buzz_db(ql, k) == -200.0



def test_aligned_join_starts_the_loop_at_its_gentlest_rising_crossing():
    """The DOC's swap to the loop oscillator slips by a fraction of a sample; at a steep
    crossing that is a click every note. A fundamental with a strong, phase-shifted 3rd
    harmonic has three rising crossings per cycle at very different slopes."""
    f0, t = 220.0, np.arange(int(2.0 * SR)) / SR
    x = np.concatenate([np.zeros(500), 0.3 * np.sin(2 * np.pi * f0 * t) + 0.5 * np.sin(6 * np.pi * f0 * t + 2.0)])
    period, k, L = SR / f0, 16, 512
    loop = lb.loop_power(x, 600 + 4000, 600 + 20000, period, k, L)
    _, lp = lb.join_aligned(x, 500, period, L * f0 / k, k, 1016, loop)
    c = L // k                                                         # samples per cycle
    rising = [lp[i] - lp[i - 1] for i in range(1, c + 1) if lp[i - 1] < 0 <= lp[i]]
    assert len(rising) >= 2 and lp[1] - lp[-1] <= 2 * min(rising) + 1e-9

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
