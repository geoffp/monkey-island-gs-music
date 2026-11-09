"""Choosing sampled keys, zones and rates (pitchplan) and the DOC playback measure (docsim)."""

import numpy as np
import pytest

from migs import docsim
from migs import ntpsamples as smp
from migs import pitchplan as pp
from migs.munt import SR


def tone(key: int, seconds: float = 1.0, harmonics: int = 40) -> np.ndarray:
    """A bright harmonic tone (1/h amplitudes up to 7 kHz, within a 16 kHz sample), the same
    timbre at every key."""
    f0, t = docsim.key_hz(key), np.arange(int(seconds * SR)) / SR
    hs = [h for h in range(1, harmonics + 1) if h * f0 < 7000]
    return sum(np.sin(2 * np.pi * h * f0 * t) / h for h in hs) * 0.3


def test_a_weighting_is_unity_at_1_khz_and_cuts_the_bass():
    assert docsim.a_weight(np.array([1000.0]))[0] == pytest.approx(1.0, abs=0.01)
    assert docsim.a_weight(np.array([100.0]))[0] < 0.02                       # about -19 dB


def test_playing_far_below_the_sampled_key_costs_and_sampling_near_it_helps():
    err = docsim.NoteError(0, tone)
    assert err(60, 16000, 40) > err(60, 16000, 60) + 6                        # 20 semitones down: dark, aliased
    assert err(42, 16000, 40) < err(60, 16000, 40) - 6                        # a sample near the note


def test_a_higher_rate_moves_drop_sample_images_out_of_the_band():
    """No interpolation: a 16 kHz sample's 7 kHz content leaves an image at 16 - 7 = 9 kHz."""
    err = docsim.NoteError(0, tone)
    assert err(60, 26320, 60) < err(60, 16000, 60) - 10


def test_note_errors_are_cached_on_disk(tmp_path):
    cache = tmp_path / "docsim.json"
    err = docsim.NoteError(0, tone, cache)
    v = err(60, 16000, 48)
    err.save()

    def no_render(key):
        raise AssertionError("render called despite the cache")
    assert docsim.NoteError(0, no_render, cache)(60, 16000, 48) == v


def test_loop_rates_keep_whole_cycles_in_the_loop():
    # A4 = 440 Hz: 512 B hold 16 cycles at 14080 Hz (32 samples each); 8 or 32 cycles are outside
    assert pp.loop_rates(69, 512, 9000, 22000) == [pytest.approx(14080)]
    assert pp.loop_rates(69, 512, 5000, 30000) == pytest.approx([28160, 14080, 7040])


def _distance_plan(weights, max_zones, span=(0, 127)):
    cands = [pp.Candidate(k, 16000.0, *span) for k in range(min(weights), max(weights) + 1)]
    return pp.plan(weights, cands, lambda k, r, n: abs(n - k), max_zones)


def test_one_zone_samples_at_the_weighted_median_key():
    (one,) = _distance_plan({40: 1.0, 50: 3.0, 52: 1.0}, 1)
    assert one.zones[0].key == 50 and (one.zones[0].lo, one.zones[0].hi) == (40, 52)
    assert one.error_db == pytest.approx((10 + 0 + 2) / 5)


def test_zones_split_between_clusters_and_cover_every_key():
    one, two = _distance_plan({36: 1.0, 38: 1.0, 60: 1.0, 62: 1.0}, 2)
    assert [(z.lo, z.hi) for z in two.zones] == [(36, 38), (60, 62)]
    assert two.error_db < one.error_db
    assert pp.zone_ranges(two.zones) == [(0, 49), (50, 127)]             # halfway between 38 and 60
    assert pp.gain_db(one, two) > 0


def test_candidates_must_reach_every_key_of_their_zone():
    """A sample plays only keys inside the period table from its base note: a zone it can't
    reach takes another candidate (or another split)."""
    w = {30: 1.0, 31: 1.0, 70: 1.0}
    cands = [pp.Candidate(30, 16000.0, 0, 60), pp.Candidate(70, 16000.0, 40, 127)]
    plans = pp.plan(w, cands, lambda k, r, n: abs(n - k), 2)
    assert [p.count for p in plans] == [2]                                   # no single sample reaches 30-70
    assert [z.key for z in plans[0].zones] == [30, 70]


def test_steadiness_follows_the_level_trend_not_a_wobble():
    t = np.arange(int(2.6 * SR)) / SR
    wobble = 10 ** (10 * np.sin(2 * np.pi * t / 0.5) / 20)                 # +-10 dB rotary-like wobble
    decay = 10 ** (-12 * t / 20)                                           # -12 dB/s
    a, off = 0, int(2.5 * SR)
    assert smp.is_steady(wobble, a, off, 0.4)
    assert not smp.is_steady(decay, a, off, 0.4)
