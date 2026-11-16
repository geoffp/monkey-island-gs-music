import numpy as np

from migs.ntpsamples import SR, loop_notes


def tone(freq, seconds=1.5):
    t = np.arange(int(seconds * SR)) / SR
    return np.sin(2 * np.pi * freq * t) + 0.1 * np.sin(2 * np.pi * 3 * freq * t)


F0 = 130.81
PERIOD = SR / F0


def test_note_pitch_sustain_is_one_period():
    assert loop_notes(tone(F0), 0, SR, PERIOD, 0.4, {}) == 1


def test_sustain_an_octave_down_is_two_periods():
    # Timpani: the sustain is one partial about half the note's pitch (63.5 Hz for key 48)
    assert loop_notes(tone(F0 / 2 * 0.97), 0, SR, PERIOD, 0.4, {}) == 2


def test_sustain_far_from_half_is_not():
    # Taiko: a low partial that doesn't follow the key (46.7 Hz for key 48)
    assert loop_notes(tone(46.7), 0, SR, PERIOD, 0.4, {}) == 1
