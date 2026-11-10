# SPDX-License-Identifier: GPL-3.0-or-later
"""MIDI Synth/DOC tuning model, checked against the GSSquared measurements."""

import pytest

from migs.doc_tuning import (bytes_per_second, decode, encode, rate_for, segment_rate,
                             single_cycle_tuning, tuning_for)


@pytest.mark.parametrize("size, measured", [(1, 225879), (2, 451758), (3, 112940), (5, 112940), (7, 112940)])
def test_matches_measured_byte_rates_at_a4_octave4(size, measured):
    # docs/iigs-midisynth.md, experiment 2
    assert bytes_per_second(69, 0.0, size) == pytest.approx(measured, rel=1e-4)


def test_measured_tunings_experiment1():
    # 256 B at octave 3: one pass = 220.5 Hz at A4; +7 semi +32 fine = x1.5428
    assert bytes_per_second(69, decode(3, 0, 0), 0) / 256 == pytest.approx(220.485, rel=1e-3)
    assert bytes_per_second(69, decode(3, 7, 32), 0) / bytes_per_second(69, decode(3, 0, 0), 0) == \
        pytest.approx(1694 / 1098, rel=1e-3)


def test_single_cycle_waves_are_in_tune_at_octave_4():
    t = single_cycle_tuning()
    assert abs(t) < 0.004                          # octave 4, a few cents flat of exactly 0
    assert abs(decode(*encode(t)) - t) <= 1 / (12 * 64)
    assert bytes_per_second(57, single_cycle_tuning(), 0) / 256 == pytest.approx(220.0, rel=1e-9)


def test_tuning_round_trip_plays_the_sample_at_its_recorded_rate():
    t = tuning_for(16000, 45, 3)                  # 16 kHz sample, natural pitch at key 45
    o, s, f = encode(t)
    assert bytes_per_second(45, decode(o, s, f), 3) == pytest.approx(16000, rel=2e-3)   # within 1/64 semi
    with pytest.raises(ValueError):
        encode(tuning_for(400, 100, 7))           # far too slow: below octave 0


def test_envelope_rates():
    assert rate_for(0.0) == 31 and rate_for(13.0) == 0 and rate_for(0.43) == 16
    assert segment_rate(127, 127, 1.0) == 31
    assert segment_rate(127, 0, 0.43) == 16 and segment_rate(127, 63, 0.215) == 16
