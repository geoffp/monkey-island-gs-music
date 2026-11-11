# SPDX-License-Identifier: GPL-3.0-or-later
"""Analysis of rendered notes: pitch period, averaged cycle, relative pitch (audio)."""

import platform

import numpy as np
import pytest

from migs.audio import interp_at, one_cycle, pitch_offset, refine_period, rms_envelope, smooth
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


def test_interp_at_is_np_interp_on_the_sample_grid():
    """The fast form gives np.interp's values, held at the ends: exactly on x86_64 (MOD bytes
    depend on them), to the last bit elsewhere (arm64 builds of numpy fuse its multiply-add,
    rounding once instead of twice)."""
    rng = np.random.default_rng(1)
    exact = platform.machine().lower() in ("x86_64", "amd64")
    for n in (2, 3, 1000):
        x = rng.standard_normal(n)
        pos = np.concatenate([rng.uniform(-3, n + 3, 5000), np.arange(n, dtype=float), [n - 1, 0.0]])
        got, want = interp_at(x, pos), np.interp(pos, np.arange(n), x)
        if exact:
            assert np.array_equal(got, want)
        else:
            np.testing.assert_allclose(got, want, rtol=0, atol=4 * np.finfo(float).eps * np.abs(x).max())


@pytest.mark.parametrize("n", [1, 160, 320, 321])
def test_rms_envelope_matches_the_convolution(n):
    """Same window as np.convolve(x*x, ones(n)/n, "same"): zeros beyond the ends, odd and even n
    (the build uses 5 and 10 ms). The cumulative sum's rounding error is relative to the whole
    signal's energy, so it is bounded against the peak, far below an 8-bit step."""
    x = np.random.default_rng(2).standard_normal(4000) * np.exp(-np.arange(4000) / 800)
    ref = np.sqrt(np.convolve(x * x, np.ones(n) / n, mode="same"))
    assert np.allclose(rms_envelope(x, (n + 0.5) / SR), ref, rtol=1e-9, atol=1e-9 * ref.max())


def test_smooth_matches_the_edge_corrected_convolution():
    x = np.random.default_rng(3).standard_normal(500)
    for n in (2, 7, 8):
        ref = np.convolve(x, np.ones(n), "same") / np.convolve(np.ones_like(x), np.ones(n), "same")
        assert np.allclose(smooth(x, n), ref)
