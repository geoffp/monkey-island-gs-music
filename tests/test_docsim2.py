"""docsim 2.0 (src/migs/docsim2.py): the properties the bake-off relied on
(docs/similarity-measures.md), on synthetic signals."""
import numpy as np
import pytest

from migs import docsim2 as d2
from migs.docram import DOC_RATE
from migs.ntpsamples import ModSample

FS = DOC_RATE


def tone(f0=220.0, seconds=2.5, harmonics=8, am=0.0, am_hz=5.0, am_phase=0.0, tilt=1.0):
    t = np.arange(int(seconds * FS)) / FS
    y = sum(tilt ** h * np.sin(2 * np.pi * f0 * (h + 1) * t) / (h + 1) for h in range(harmonics))
    return y * (1 + am * np.sin(2 * np.pi * am_hz * t + am_phase))


def pattern(y):
    return d2.loudness_pattern(y, FS)


def test_identical_notes_score_zero():
    p = pattern(tone())
    assert d2.seg_distance(p, p) == 0
    assert d2.mod_distance(p, p) == 0


def test_the_measure_is_level_free():
    a, b = tone(), tone(tilt=0.7)
    assert d2.distance(pattern(a), pattern(b)) == pytest.approx(d2.distance(pattern(a), pattern(0.1 * b)), rel=1e-4)


def test_movement_is_compared_phase_free():
    """Two renders of a wobbling sound differ in the wobble's phase, not in quality."""
    ref = pattern(tone(am=0.5))
    shifted = d2.mod_distance(ref, pattern(tone(am=0.5, am_phase=2.0)))
    static = d2.mod_distance(ref, pattern(tone()))
    assert shifted < 0.05 * static
    # segment means: the 0.12 s attack segment is shorter than a 5 Hz cycle, so a little phase
    # remains there, far below a real change of timbre
    timbre = d2.seg_distance(ref, pattern(tone(am=0.5, tilt=0.7)))
    assert d2.seg_distance(ref, pattern(tone(am=0.5, am_phase=2.0))) < 0.25 * timbre


def test_modulation_lands_in_its_band_and_scales_with_depth():
    depth = {}
    for am in (0.2, 0.5):
        dd, w = d2.modulation(pattern(tone(am=am, am_hz=5.0)))
        depth[am] = dd[np.argmax(w)]
    assert np.argmax(depth[0.5]) == 2                    # 4-8 Hz
    assert depth[0.5][2] > 2 * depth[0.2][2]


def test_more_distortion_scores_worse():
    rng = np.random.default_rng(0)
    ref = tone()
    p = pattern(ref)
    noisy = [d2.distance(p, pattern(ref + a * rng.standard_normal(len(ref)))) for a in (0.01, 0.05, 0.2)]
    assert noisy[0] < noisy[1] < noisy[2]
    duller = [d2.distance(p, pattern(tone(harmonics=h))) for h in (6, 4, 2)]
    assert duller[0] < duller[1] < duller[2]


def test_drums_skip_the_modulation_term():
    """A decay is not movement: a drum scores the mean over the whole hit only."""
    t = np.arange(int(0.6 * FS)) / FS
    rng = np.random.default_rng(1)
    hit = rng.standard_normal(len(t)) * np.exp(-t / 0.08)
    faster = rng.standard_normal(len(t)) * np.exp(-t / 0.06)
    a, b = pattern(hit), pattern(faster)
    assert d2.distance(a, b, drum=True) == d2.mean_distance(a, b)
    assert d2.distance(a, b) > d2.distance(a, b, drum=True)


def test_a_one_frame_drum_is_still_measured():
    """A 37 ms bongo is one analysis frame: it must not score as identical to anything."""
    rng = np.random.default_rng(2)
    t = np.arange(int(0.04 * FS)) / FS
    hit = rng.standard_normal(len(t)) * np.exp(-t / 0.01)
    dull = np.convolve(hit, np.ones(8) / 8, "same")
    a, b = pattern(hit), pattern(dull)
    assert a.shape[1] == 1 and d2.distance(a, b, drum=True) > 0.05


def test_forward_masking_hides_noise_in_a_hits_tail():
    """Quiet noise just after a loud hit counts less with forward masking (the ear's)."""
    rng = np.random.default_rng(4)
    t = np.arange(int(0.3 * FS)) / FS
    hit = np.sin(2 * np.pi * 200 * t) * np.exp(-t / 0.03)
    noisy = hit + 0.003 * rng.standard_normal(len(t))
    plain = d2.mean_distance(pattern(hit), pattern(noisy))
    masked = d2.mean_distance(d2.loudness_pattern(hit, FS, d2.DRUM_FORWARD_S),
                              d2.loudness_pattern(noisy, FS, d2.DRUM_FORWARD_S))
    assert masked < 0.8 * plain


def test_db_halving_the_distance_gains_6_db():
    assert d2.db(0.1) - d2.db(0.2) == pytest.approx(-6.02, abs=0.01)


def _looped(f0=200.0, rate=16000.0, cycles=4):
    period = rate / f0
    n = int(round(period * cycles))
    t = np.arange(512 + n) / rate
    data = 0.8 * np.sin(2 * np.pi * f0 * t)
    s = ModSample("t", data, loop_start=512, loop_len=n, key0=60, rate=rate)
    s.env_t, s.env_g = np.array([0.0, 3.0]), np.array([1.0, 1.0])
    return s


def test_a_looped_sample_plays_through_attack_and_loop():
    s = _looped()
    y = d2.play_sample(s, 0.0, 1.0)
    assert len(y) == int(DOC_RATE)
    spec = np.abs(np.fft.rfft(y[int(0.2 * DOC_RATE):]))
    f = np.fft.rfftfreq(len(y) - int(0.2 * DOC_RATE), 1 / DOC_RATE)
    assert abs(f[np.argmax(spec)] - 200.0) < 2.0       # still the note after the attack
    up = d2.play_sample(s, 12.0, 1.0)
    spec = np.abs(np.fft.rfft(up[int(0.2 * DOC_RATE):]))
    assert abs(f[np.argmax(spec)] - 400.0) < 3.0


def test_a_one_shot_plays_once_then_silence():
    s = ModSample("shot", np.ones(1000) * 0.5, rate=10000.0, key0=60)
    y = d2.play_sample(s, 0.0, 0.5)
    assert np.all(y[:int(0.09 * DOC_RATE)] > 0)
    assert np.all(y[int(0.11 * DOC_RATE):] == 0)


def test_sample_tag_follows_what_playback_depends_on():
    a, b = _looped(), _looped()
    assert d2.sample_tag(a) == d2.sample_tag(b)
    b.loop_len -= 2
    assert d2.sample_tag(a) != d2.sample_tag(b)
    c = _looped()
    c.env_g = np.array([1.0, 0.5])
    assert d2.sample_tag(a) != d2.sample_tag(c)


def test_the_scorer_caches_and_weights_its_keys(tmp_path):
    calls = []

    def render(tag, key):
        calls.append(key)
        f0 = 440 * 2 ** ((key - 69) / 12)
        return tone(f0, seconds=3.0) * 0.3

    sc = d2.Scorer(lambda tag, key: np.interp(np.arange(int(3.0 * 32000)) / 32000,
                                              np.arange(int(3.0 * FS)) / FS, render(tag, key)),
                   32000, seconds=3.0, cache=tmp_path / "c.json")
    s = _looped(f0=440 * 2 ** ((55 - 69) / 12), rate=16000.0)
    s.key0 = 55
    one = sc.note_db(s, 1, 55)
    assert sc.note_db(s, 1, 55) == one and calls.count(55) == 1
    both = sc.sample_db(s, 1, {55: 3.0, 57: 1.0})
    assert both == pytest.approx((3 * one + sc.note_db(s, 1, 57)) / 4)
    sc.cache.save()
    again = d2.Scorer(lambda tag, key: 1 / 0, 32000, cache=tmp_path / "c.json")
    assert again.note_db(s, 1, 55) == one                 # from disk: no render needed


def test_pick_keys_keeps_the_weight_and_spreads_over_the_range():
    w = {k: 1.0 for k in range(40, 80)}
    p = d2.pick_keys(w, 5)
    assert len(p) == 5 and sum(p.values()) == pytest.approx(40.0)
    assert min(p) < 46 and max(p) > 73
    assert d2.pick_keys({60: 2.0, 62: 1.0}, 5) == {60: 2.0, 62: 1.0}


def _bright(key, seconds=2.0, sr=32000):
    """A bright synthetic sound (harmonics to 11 kHz) at `sr`: played well below its sampled
    key from a 16.7 kHz sample, it loses its top end."""
    f0 = 440 * 2 ** ((key - 69) / 12)
    t = np.arange(int(seconds * sr)) / sr
    return 0.3 * sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, 80) if f0 * h < 11000)


def test_pitch_error_grows_with_transposition_and_is_cached(tmp_path):
    calls = []

    def render(key):
        calls.append(key)
        return _bright(key)
    err = d2.NoteError("bright", render, 32000, tmp_path / "c.json")
    at_own, near, far = err(72, 16744.0, 72), err(72, 16744.0, 66), err(72, 16744.0, 60)
    assert at_own < near < far and at_own + 3 < far
    n_calls = len(calls)
    assert err(72, 16744.0, 60) == far and len(calls) == n_calls
    err.save()
    again = d2.NoteError("bright", lambda k: 1 / 0, 32000, tmp_path / "c.json")
    assert again(72, 16744.0, 60) == far


def test_drum_score_prefers_the_higher_rate():
    rng = np.random.default_rng(3)
    sr = 32000
    t = np.arange(int(0.4 * sr)) / sr
    hit = rng.standard_normal(len(t)) * np.exp(-t / 0.05)
    from scipy.signal import resample
    lo = resample(hit, int(len(hit) * 6000 / sr))
    hi = resample(hit, int(len(hit) * 22000 / sr))
    s_lo = d2.drum_db(hit, sr, lo / np.max(np.abs(lo)), 6000.0, 0.0, 0.4)
    s_hi = d2.drum_db(hit, sr, hi / np.max(np.abs(hi)), 22000.0, 0.0, 0.4)
    assert s_hi < s_lo


def test_inharmonic_energy_finds_partials_between_the_harmonics():
    f0 = 220.0
    t = np.arange(int(2.0 * FS)) / FS
    clean = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, 10))
    sub = clean + 0.3 * np.sin(2 * np.pi * f0 / 2 * t)                 # a sub-octave is harmonic here
    buzz = clean + 0.1 * np.sin(2 * np.pi * f0 * 2.37 * t)             # an image between harmonics
    c, s, b = (d2.inharmonic_db(y, FS, f0) for y in (clean, sub, buzz))
    assert c < -40 and s < -40
    assert b > c + 20


def test_the_live_term_hears_a_static_copy_as_worse_than_an_extra_pulse():
    """MI153's Pipe Organ: Munt shimmers (2-8 Hz); a 512 B loop is static, a 16 KB loop keeps
    the shimmer and adds a slow pulse. Depth is heard as a ratio, so the static copy is the
    worse one, whatever the per-band linear term says."""
    t = np.arange(int(3.0 * FS)) / FS
    base = tone(seconds=3.0)
    slow = 1 + 0.03 * np.sin(2 * np.pi * 1.2 * t)                 # Munt moves a little at 0.5-2 Hz too
    ref = pattern(base * slow * (1 + 0.15 * np.sin(2 * np.pi * 5 * t)))
    static = pattern(base)
    pulsed = pattern(base * (1 + 0.15 * np.sin(2 * np.pi * 5 * t + 1.0)) * (1 + 0.1 * np.sin(2 * np.pi * 1.2 * t)))
    assert d2.live_distance(ref, static) > 2 * d2.live_distance(ref, pulsed)
    assert d2.live_distance(ref, ref) == 0


def test_live_weight_zero_is_the_old_measure():
    a, b = pattern(tone(am=0.3)), pattern(tone(tilt=0.7))
    assert d2.distance(a, b, mod_weight=3.0) == d2.distance(a, b, mod_weight=3.0, live_weight=0.0)
    assert d2.distance(a, b, live_weight=0.2) > d2.distance(a, b)


def test_the_scorer_keys_its_cache_by_the_live_weight(tmp_path):
    f0 = 440 * 2 ** ((55 - 69) / 12)
    ref = tone(f0, seconds=3.0, am=0.3) * 0.3
    s = _looped(f0=f0, rate=16000.0)
    s.key0 = 55
    a = d2.Scorer(lambda tag, key: ref, FS, 3.0, tmp_path / "c.json")
    b = d2.Scorer(lambda tag, key: ref, FS, 3.0, tmp_path / "c.json", live_weight=0.5)
    assert b.note_db(s, 1, 55) > a.note_db(s, 1, 55)
