import numpy as np

from migs.docram import DOC_RATE
from migs.docsim2 import pitch_vibrato, vib_distance


def tone(f0, cents, rate, seconds=3.0, fs=DOC_RATE):
    t = np.arange(int(seconds * fs)) / fs
    dev = cents * np.sin(2 * np.pi * rate * t)                       # cents of pitch swing
    phase = 2 * np.pi * np.cumsum(f0 * 2 ** (dev / 1200)) / fs
    return np.sin(phase) + 0.3 * np.sin(2 * phase)


def test_vibrato_depth_is_recovered():
    for cents in (15.0, 27.0):
        got = pitch_vibrato(tone(659.0, cents, 5.5), DOC_RATE, 659.0)
        assert abs(got - cents) < 0.2 * cents, (cents, got)


def test_steady_note_is_under_the_floor():
    assert pitch_vibrato(tone(659.0, 0.0, 5.5), DOC_RATE, 659.0) < 3.0


def test_rates_outside_the_band_are_not_vibrato():
    assert pitch_vibrato(tone(659.0, 27.0, 0.8), DOC_RATE, 659.0) < 8.0      # a slow drift


def test_low_notes_are_not_tracked():
    assert pitch_vibrato(tone(60.0, 27.0, 5.5), DOC_RATE, 60.0) == 0.0


def test_lost_vibrato_costs_a_ratio():
    assert vib_distance(27.0, 0.0) > 0.6 and vib_distance(27.0, 24.0) < 0.1 and vib_distance(0.0, 0.0) == 0.0
    assert vib_distance(9.0, 0.0) == 0.0 and vib_distance(0.0, 9.0) == 0.0      # tracker noise on steady notes
