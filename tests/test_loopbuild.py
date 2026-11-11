# SPDX-License-Identifier: GPL-3.0-or-later
"""Loop construction (loopbuild: harmonic tracks, attack detection, E'+ loops and joins, the
sub-octave series) and the loop rules ntpsamples gates with, on synthetic tones with known
answers."""

import numpy as np

from loop_fixtures import SR, cycle, tone, with_sub_octave
from migs import loopbuild as lb
from migs import ntpsamples as smp


# --- the buzz metric the build gates loops with ------------------------------

def test_buzz_metric_zero_for_tiled_cycle_and_sees_1pct_cycle_difference():
    c = cycle(32)
    exact = np.tile(c, 16)
    assert smp.loop_buzz_db(exact, 16) == -200.0
    wobbly = exact.copy()
    wobbly[:32] *= 1.01
    assert smp.loop_buzz_db(wobbly, 16) > -60


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


# --- sub-octave series -------------------------------------------------------------

def test_sub_octave_measures_the_series_an_octave_below_the_note():
    f0 = 500.0
    plain, sub = tone(f0, 1.5), with_sub_octave(f0, 1.5)
    assert lb.sub_octave(plain, 0, len(plain), SR / f0) < 1e-4
    assert 0.05 < lb.sub_octave(sub, 0, len(sub), SR / f0) < 0.3
    cfg = {"sub_octave_min_share": 0.02}
    assert smp.loop_notes(sub, 0, len(sub), SR / f0, 0.4, cfg) == 2
    assert smp.loop_notes(plain, 0, len(plain), SR / f0, 0.4, cfg) == 1
    noise = np.random.default_rng(0).standard_normal(len(plain))       # inharmonic: no period at all
    assert smp.loop_notes(noise, 0, len(noise), SR / f0, 0.4, cfg) == 1


def test_a_two_period_loop_keeps_the_sub_octave_that_a_one_period_loop_drops():
    """The bug: Pipe Organ 88's 512 B loop held 32 single note periods, so its 659 Hz series
    was gone and the attack (as recorded) sounded an octave below the loop."""
    f0 = 500.0
    x = with_sub_octave(f0, 1.5)
    P = SR / f0
    one = lb.loop_power(x, 0.3 * SR, 1.2 * SR, P, 16, 512)
    two = lb.loop_power(x, 0.3 * SR, 1.2 * SR, 2 * P, 8, 512, notes=2)
    share = lambda loop: lb.sub_octave(np.tile(loop, 40), 0, 40 * 512, 32.0)   # 32 samples per note period
    assert share(one) < 1e-3
    assert abs(share(two) - lb.sub_octave(x, 0.3 * SR, 1.2 * SR, P)) < 0.02
