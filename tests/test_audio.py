# SPDX-License-Identifier: GPL-3.0-or-later
"""Analysis of rendered notes: pitch period, averaged cycle, relative pitch (audio)."""

import numpy as np
import pytest

from migs.audio import one_cycle, pitch_offset, refine_period
from migs.munt import SR


def test_period_and_cycle_extraction_on_a_pure_tone():
    f = 261.63
    x = np.sin(2 * np.pi * f * np.arange(SR) / SR)
    p = refine_period(x, 1000, f)
    assert p == pytest.approx(SR / f, rel=1e-3)
    cyc = one_cycle(x, 1000, p)
    assert len(cyc) == 256
    ref = np.sin(2 * np.pi * np.arange(256) / 256 + 2 * np.pi * f * 1000 / SR)
    assert np.corrcoef(cyc, ref)[0, 1] > 0.999


def test_pitch_offset_sign_lower_is_negative():
    t = np.arange(int(0.2 * SR)) / SR
    tone = lambda hz: np.sin(2 * np.pi * hz * t) * np.exp(-t * 10) + 0.5 * np.sin(4 * np.pi * hz * t) * np.exp(-t * 10)
    assert pitch_offset(tone(400), tone(400 * 2 ** (-5 / 12))) == -5
    assert pitch_offset(tone(400), tone(400 * 2 ** (3 / 12))) == 3
