"""A sound's own periodic movement (docsim2.movement), the per-sound verdict, the loop span
that holds whole cycles of it (ntpsamples.sync_span) and the planner's set purchases."""
import numpy as np

from migs import ntpsamples as smp
from migs.docsim2 import Movement, movement, sound_kind
from migs.munt import SR
from ntp_fixtures import looped_melodic


def vibrato(f0, cents, rate, seconds=4.0, fs=SR):
    t = np.arange(int(seconds * fs)) / fs
    phase = 2 * np.pi * np.cumsum(f0 * 2 ** (cents * np.sin(2 * np.pi * rate * t) / 1200)) / fs
    return np.sin(phase) + 0.3 * np.sin(2 * phase)


def beating(f0, beat, seconds=4.0, fs=SR):
    t = np.arange(int(seconds * fs)) / fs
    return np.sin(2 * np.pi * f0 * t) + 0.5 * np.sin(2 * np.pi * (f0 + beat) * t)


def test_a_vibrato_is_found_with_its_rate_and_depth():
    mv = movement(vibrato(330.0, 28.0, 6.16), SR, 330.0)
    assert mv.fm_periodic and not mv.am_periodic
    assert abs(mv.hz - 6.16) < 0.03 and abs(mv.fm_cents - 28.0) < 2.0


def test_a_shallow_vibrato_is_regular_but_not_movement():
    mv = movement(vibrato(330.0, 5.0, 4.75), SR, 330.0)
    assert mv.fm_regular and abs(mv.fm_hz - 4.75) < 0.05 and abs(mv.fm_cents - 5.0) < 1.0     # read to a cent
    assert mv.hz == 0.0


def test_beating_partials_are_a_level_swing():
    mv = movement(beating(262.0, 2.0), SR, 262.0)
    assert mv.am_periodic and not mv.fm_periodic
    assert abs(mv.hz - 2.0) < 0.05


def test_a_steady_note_and_a_wandering_one_have_no_period():
    t = np.arange(4 * SR) / SR
    assert movement(np.sin(2 * np.pi * 330.0 * t), SR, 330.0).hz == 0.0
    rng = np.random.default_rng(1)
    walk = np.cumsum(rng.standard_normal(len(t))) * 0.02                 # cents: a random drift
    walk = 30 * (walk - walk.mean()) / np.abs(walk).max()
    y = np.sin(2 * np.pi * np.cumsum(330.0 * 2 ** (walk / 1200)) / SR)
    assert movement(y, SR, 330.0).hz == 0.0


def test_a_note_too_low_to_follow_has_none():
    assert movement(vibrato(55.0, 28.0, 6.0), SR, 55.0) == Movement()


def test_one_verdict_per_sound_not_per_key():
    """Sax reads 11.3, 12.0 and 9.6 cents at three keys: one answer for all its zones."""
    sax = [Movement(5.18, c, True, 5.1, 0.1, False) for c in (11.3, 12.0, 9.6)]
    assert sound_kind(sax) == "fm"
    panpipes = [Movement(4.75, 7.0, True, 4.75, 0.1, False)] * 3
    assert sound_kind(panpipes) == ""
    strings = [Movement(1.5, 7.0, False, 1.5, 8.4, True), Movement(2.3, 7.0, False, 2.27, 8.1, True),
               Movement(5.0, 7.0, False, 3.4, 8.0, False)]
    assert sound_kind(strings) == "am"                                  # regular at most keys
    assert sound_kind([Movement(6.0, 30.0, True), Movement(), Movement()]) == ""
    assert sound_kind([]) == ""


def test_sync_span_finds_one_vibrato_cycle_in_whole_periods():
    """The span is measured, not k x a period read at one instant: that period is up to 1.6 %
    off a 28-cent vibrato's mean, half a period over a cycle."""
    f0, rate = 330.0, 6.16
    x = vibrato(f0, 28.0, rate, 2.0)
    off = SR / (f0 * 2 ** (20 / 1200))                                   # a period read at the swing's top
    spans = smp.sync_span(x, 0.6 * SR, SR / rate, off)
    corr, length, k = spans[0]
    assert corr > 0.99
    assert abs(length - SR / rate) < 0.6 * SR / f0                       # the cycle is 53.6 periods: 53 or 54
    assert abs(length * f0 / SR - round(length * f0 / SR)) < 0.05        # a whole number of mean periods
    assert k == round(length * f0 / SR)


def test_sync_span_needs_room_in_the_recording():
    x = vibrato(330.0, 28.0, 6.16, 0.5)
    assert smp.sync_span(x, 0.45 * SR, SR / 6.16, SR / 330.0) == []


def _zone(name, move_ok):
    return looped_melodic(name, steady=True, max_loop=16384, raw_ok=move_ok, move_ok=move_ok, move_hz=6.16)


def test_a_moving_sounds_zones_get_their_loops_together():
    lo, hi = _zone("whistle 57", (2048, 4096)), _zone("whistle 76", (2048, 4096))
    scores = {0: {512: 14.0, 2048: -6.0, 4096: -8.0}, 1: {512: 14.0, 2048: -3.0, 4096: -3.5}}
    sizes, lines, _ = smp.plan_loops_valued([lo, hi], {0: 30.0, 1: 1.0}, 239, 16384, lambda i, z: scores[i][z],
                                            sound={0: 109, 1: 109})
    assert sizes[0] >= 2048 and sizes[1] >= 2048
    assert sum("loop set" in l for l in lines) == 2


def test_a_set_that_does_not_fit_leaves_every_zone_short():
    lo, hi = _zone("whistle 57", (2048,)), _zone("whistle 76", (16384,))
    scores = {0: {512: 14.0, 2048: -6.0}, 1: {512: 14.0, 16384: -3.0}}
    last = (2 * 2048 + 2 * 512 + 2048) // 256 - 1                        # room for the low zone's loop alone
    args = ([lo, hi], {0: 30.0, 1: 1.0}, last, 16384, lambda i, z: scores[i][z])
    sizes, lines, _ = smp.plan_loops_valued(*args, sound={0: 109, 1: 109})
    assert sizes == {0: 512, 1: 512}
    assert any("blocked" in l for l in lines)
    assert smp.plan_loops_valued(*args)[0] == {0: 2048, 1: 512}          # zones priced singly: one moves, one doesn't


def test_a_tight_set_falls_back_to_each_zones_smallest_gain():
    lo, hi = _zone("whistle 57", (2048, 8192)), _zone("whistle 76", (2048, 8192))
    scores = {0: {512: 14.0, 2048: 0.0, 8192: -100.0}, 1: {512: 14.0, 2048: 0.0, 8192: -100.0}}   # 8 KB is the better value
    last = (2 * 2048 + 2 * 2048) // 256 - 1
    sizes, _, _ = smp.plan_loops_valued([lo, hi], {0: 10.0, 1: 10.0}, last, 16384, lambda i, z: scores[i][z],
                                        sound={0: 109, 1: 109})
    assert sizes == {0: 2048, 1: 2048}


def test_a_zone_that_cannot_keep_the_movement_is_left_out_of_the_set():
    low, mid, hi = _zone("organ 37", ()), _zone("organ 56", (8192,)), _zone("organ 68", (8192,))
    low.raw_ok, low.move_hz = (16384,), 0.0                              # too low to follow: an ordinary raw loop
    scores = {0: {512: -10.0, 16384: -10.5}, 1: {512: -10.0, 8192: -20.0}, 2: {512: -10.0, 8192: -11.0}}
    last = (3 * 2048 + 512 + 2 * 8192) // 256 - 1                        # the two that can, not the low one's 16 KB
    sizes, lines, _ = smp.plan_loops_valued([low, mid, hi], {0: 10.0, 1: 10.0, 2: 10.0}, last, 16384,
                                            lambda i, z: scores[i][z], sound={0: 12, 1: 12, 2: 12})
    assert sizes == {0: 512, 1: 8192, 2: 8192}
    assert any("organ 37" in l and "by itself" in l for l in lines)


def test_loops_holding_the_movement_pass_the_loop_check():
    s = _zone("whistle", (2048,))
    t = np.arange(2040 + 2048)
    s.data = np.sin(2 * np.pi * t / 37.0) * (1 + 0.5 * np.sin(2 * np.pi * t / 2048))     # a 9 dB swing a pass
    s.loop_len, s.cycles, s.rate = 2048, 55, 12600.0
    cfg = {"raw_loop_slow_hz": 2.0, "raw_loop_max_tremolo_db": 1.0, "raw_loop_max_buzz_db": -50.0}
    assert smp.loop_problems([s], cfg) == []
    s.move_ok = ()
    assert len(smp.loop_problems([s], cfg)) == 1


def test_a_blocked_set_displaces_loops_worth_less_in_all():
    """Value per page alone buys the small dense loop first and leaves the big set, worth more
    in all, blocked with pages to spare."""
    solo = _zone("strings", ())
    solo.raw_ok, solo.move_hz = (4096,), 0.0
    a, b = _zone("organ 56", (8192,)), _zone("organ 68", (8192,))
    scores = {0: {512: -10.0, 4096: -16.0}, 1: {512: -10.0, 8192: -20.0}, 2: {512: -10.0, 8192: -20.0}}
    last = (3 * 2048 + 512 + 2 * 8192) // 256 - 1                        # the organ's set, or the strings' loop
    sizes, lines, _ = smp.plan_loops_valued([solo, a, b], {0: 10.0, 1: 10.0, 2: 10.0}, last, 16384,
                                            lambda i, z: scores[i][z], sound={0: 50, 1: 12, 2: 12})
    assert sizes == {0: 512, 1: 8192, 2: 8192}
    assert any("takes the pages of strings" in l for l in lines)
