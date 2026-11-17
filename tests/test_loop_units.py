import numpy as np

from migs.ntpsamples import ModSample, loop_units, plan_loops


def zone(name, raw_ok, steady=True, loop=512):
    s = ModSample(name, np.zeros(2040 + loop), loop_start=2040, loop_len=loop, kind="melodic")
    s.steady, s.raw_ok, s.max_loop = steady, raw_ok, 32768
    return s


def test_alone_each_sample_is_a_unit_of_its_raw_sizes():
    samples = [zone("A 52", [1024, 8192]), zone("A 73", [8192])]
    units = loop_units(samples, {0, 1}, 16384)
    assert units == [([0], [{0: 1024}, {0: 8192}]), ([1], [{1: 8192}])]


def test_a_sound_is_one_unit_raw_in_every_zone():
    samples = [zone("A 52", [16384]), zone("A 64", [8192, 16384]), zone("B", [8192])]
    units = loop_units(samples, {0, 1, 2}, 16384, {0: "A", 1: "A", 2: "B"})
    assert units == [([0, 1], [{0: 16384, 1: 8192}, {0: 16384, 1: 16384}]), ([2], [{2: 8192}])]


def test_a_sound_with_a_zone_that_cannot_go_raw_stays_short():
    samples = [zone("A 52", [8192]), zone("A 73", [])]
    assert loop_units(samples, {0, 1}, 16384, {0: "A", 1: "A"}) == []


def test_planning_by_sound_leaves_no_sound_half_raw():
    # 150 pages: the two A zones (2 x 8192 B = 64 pages) fit, the two B ones (2 x 64 pages) don't:
    # B stays short in both zones, where one at a time the first would have been bought
    samples = [zone("A 52", [8192]), zone("A 73", [8192]), zone("B 52", [16384]), zone("B 73", [16384])]
    usage = {0: 50.0, 1: 5.0, 2: 40.0, 3: 40.0}
    by_sound = plan_loops(samples, usage, 150, 16384, {0: "A", 1: "A", 2: "B", 3: "B"})
    assert by_sound[0] == by_sound[1] == 8192 and by_sound[2] == by_sound[3] == 512
    alone = plan_loops(samples, usage, 150, 16384)
    assert alone[2] == 16384 and alone[3] == 512      # one at a time: a half-raw sound
