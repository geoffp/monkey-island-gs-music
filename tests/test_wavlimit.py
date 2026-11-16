import numpy as np

from migs.wavlimit import limit


def test_quiet_song_is_untouched():
    x = np.array([0.1, -0.3, 0.5], np.float32)
    y, g = limit(x, 0.7)
    assert g == 1.0 and list(y) == [round(v * 32768) for v in (0.1, -0.3, 0.5)]


def test_loud_song_peaks_at_ceiling_even_above_full_scale():
    x = np.array([0.2, -1.06, 0.9], np.float32)
    y, g = limit(x, 0.7)
    assert abs(int(np.abs(y).max()) - round(0.7 * 32768)) <= 1 and g < 1.0


def test_silence():
    y, g = limit(np.zeros(4, np.float32), 0.7)
    assert g == 1.0 and not y.any()
