"""NinjaTrackerPlus samples (ntpsamples): tuning, loops and their metrics, one-shots, drums,
lengths, DOC block rules and loop planning."""

import numpy as np
import pytest

from migs import audio, munt
from migs import ntpsamples as smp
from ntp_fixtures import MAKE_TABLES, decaying_tone, synthetic_tables, wobbly_loop


@pytest.mark.skipif(not MAKE_TABLES.exists(), reason="run `just fetch-ntp`")
def test_period_tables_have_16_finetunes_of_72_notes():
    t = smp.period_tables(MAKE_TABLES)
    assert sorted(t) == list(range(-8, 8))
    assert all(len(v) == 72 for v in t.values())
    assert t[0][:2] == [1712, 1616] and t[0][24] == 428          # C-2 = 428 on finetune 0
    assert t[1][0] > t[0][0] > t[-1][0] or t[-1][0] > t[0][0]     # finetunes shift periods


def test_ntp_doc_blocks_follow_the_converters_rules():
    shot = smp.ModSample("s", np.zeros(1014))                      # + 8 stopper bytes -> 1 KB
    assert shot.ntp_blocks() == [1024]
    ideal = smp.ModSample("i", np.zeros(1024))                     # ideal size: no stoppers
    assert ideal.ntp_blocks() == [1024]
    looped = smp.ModSample("l", np.zeros(2552), loop_start=2040, loop_len=512)
    assert looped.ntp_blocks() == [2048, 512]                    # header 2040+8, loop ideal


def test_loop_is_exactly_periodic_and_the_attack_flows_into_it():
    sr = munt.SR
    t = np.arange(sr) / sr
    # a chorused tone: two slightly detuned partials, so raw cycles drift (what made loops buzz)
    x = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 221.3 * t + 1) + 0.3 * np.sin(2 * np.pi * 660 * t)
    period, k, loop_bytes, attack_bytes = sr / 220, 8, 512, 2040
    rate = loop_bytes * 220 / k
    attack, loop, _, _ = smp.attack_and_loop(x, 100, period, rate, k, attack_bytes, loop_bytes)
    assert len(attack) == attack_bytes and len(loop) == loop_bytes
    P = np.abs(np.fft.rfft(loop)) ** 2
    P[0] = 0
    assert P[k::k].sum() / P.sum() > 0.999                    # only harmonics of the k-cycle loop
    step = np.median(np.abs(np.diff(loop)))
    assert abs(loop[0] - loop[-1]) < 3 * step                 # loop seam
    assert abs(loop[0] - attack[-1]) < 3 * step               # attack -> loop seam
    # loop starts at a rising zero crossing with loop[0] == 0, so the NTP converter's
    # "rotate to the sample nearest zero" is a no-op and the header stays 2040 B
    assert loop[0] == 0.0 and loop[-1] < 0 and loop[1] >= 0
    assert abs(loop[0] - (loop[-1] + loop[1]) / 2) < 0.5 * step  # no step forced at the start


def test_drum_rate_follows_bandwidth():
    sr = munt.SR
    rng = np.random.default_rng(0)
    n = int(0.05 * sr)
    hat = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.01 * sr))
    hat = np.diff(hat, prepend=0)                               # high-passed noise burst
    kick = np.sin(2 * np.pi * 60 * np.arange(sr) / sr) * np.exp(-np.arange(sr) / (0.3 * sr))
    assert smp.drum_rate(hat, 1016, 6000, 26000) > 20000
    assert smp.drum_rate(kick, 2040, 6000, 26000) == 6000
    long_noise = np.diff(rng.standard_normal(sr), prepend=0)   # can't fit: trade, but at most half
    assert 13000 <= smp.drum_rate(long_noise, 2040, 6000, 26000) < 26000


def test_raw_loop_keeps_chorus_and_has_a_continuous_seam():
    sr = munt.SR
    t = np.arange(3 * sr) / sr
    x = np.sin(2 * np.pi * 220 * t) + 0.6 * np.sin(2 * np.pi * 221.5 * t)   # 1.5 Hz beating
    period, k, out = sr / 220, 110, 8192                                       # 0.5 s of cycles
    loop = smp.raw_loop(x, 0.5 * sr, period, k, out)
    env = np.abs(loop)
    blocks = env[: out // 8 * 8].reshape(8, -1).max(axis=1)
    assert blocks.max() / blocks.min() > 1.3                       # beating kept, not averaged away
    step = np.median(np.abs(np.diff(loop)))
    assert abs(loop[0] - loop[-1]) < 4 * step                      # end flows into the start


def test_loop_planner_spends_free_doc_ram_on_steady_sounds_only():
    def mk(name, steady, max_loop=16384):
        s = smp.ModSample(name, np.zeros(2552), loop_start=2040, loop_len=512, kind="melodic")
        s.steady, s.max_loop = steady, max_loop
        s.raw_ok = (1024, 2048, 4096, 8192, 16384) if steady else ()
        return s
    samples = [mk("organ", True), mk("strings", True, 4096), mk("marimba", False),
               smp.ModSample("kick", np.zeros(2038), kind="drum")]
    usage = {0: 30.0, 1: 10.0, 2: 50.0}
    sizes = smp.plan_loops(samples, usage, 239, 16384)
    assert sizes[2] == 512                                          # decaying: stays short
    assert sizes[1] == 4096                                         # capped by its recording
    assert sizes[0] == 16384                                        # most sustain: biggest loop
    tight = smp.plan_loops(samples, usage, 40, 16384)               # 41 pages: little room
    blocks = [2048, tight[0], 2048, tight[1], 2048, 512, 2048]
    assert sum(blocks) <= 41 * 256 and tight[0] >= tight[1]


def test_steadiness_separates_sustained_from_decaying():
    sr = munt.SR
    t = np.arange(int(2.6 * sr)) / sr
    a, off = int(0.1 * sr), int(2.1 * sr)
    organ = audio.rms_envelope(np.sin(2 * np.pi * 220 * t))
    bell = audio.rms_envelope(np.sin(2 * np.pi * 220 * t) * np.exp(-t / 0.4))
    assert smp.is_steady(organ, a, off, 0.4) and not smp.is_steady(bell, a, off, 0.4)


def test_smooth_matches_the_direct_moving_average():
    rng = np.random.default_rng(3)
    for length, n in ((1000, 7), (1000, 8), (5000, 801)):
        x = rng.standard_normal(length)
        k = np.ones(n)
        ref = np.convolve(x, k, "same") / np.convolve(np.ones(length), k, "same")
        assert np.allclose(smp.smooth(x, n), ref, atol=1e-12)


def test_vectorized_tuning_search_matches_the_scan():
    import math
    tables = {ft: [int(1712 * 2 ** (-(n + ft / 8) / 12)) for n in range(72)] for ft in (0, 1, -1, 2, -2)}
    def scan(freq, L, key0, lo_k, hi_k):
        best = None
        for k in range(max(1, math.ceil(L * freq / 22000)), math.floor(L * freq / 9000) + 1):
            rate = L * freq / k
            for ft, per in tables.items():
                for n in range(72):
                    if n + lo_k - key0 < 0 or n + hi_k - key0 >= 72:
                        continue
                    err = 1200 * math.log2((smp.PAL / per[n]) / rate)
                    if best is None or abs(err) < abs(best[0]):
                        best = (err, k, rate, n, ft)
        return best
    for freq, L, key0, lo_k, hi_k in ((261.6, 512, 60, 48, 84), (65.4, 16384, 36, 30, 47), (1046.5, 2048, 84, 70, 96)):
        a, b = scan(freq, L, key0, lo_k, hi_k), smp.best_tuning(freq, tables, 9000, 22000, L, key0, lo_k, hi_k)
        assert a[1:] == b[1:] and abs(a[0] - b[0]) < 1e-9


def test_floor_time_finds_the_8bit_floor():
    x = np.concatenate([np.zeros(100), decaying_tone(660, 2.0, 150.0)])     # 150 dB/s: -38.5 dB at ~0.26 s
    t = smp.floor_time(x, 100, len(x))
    assert 0.2 < t < 0.3
    slow = np.concatenate([np.zeros(100), decaying_tone(660, 2.0, 8.0)])     # bass-like: never gets there
    assert smp.floor_time(slow, 100, len(slow)) > 1.9


def test_drum_end_cuts_junk_after_the_decay():
    # a snare-like hit: a clean 0.1 s decay to ~-20 dB that stops abruptly, then 0.4 s of junk
    # hovering around the 8-bit floor (1.5 LSB = -38.5 dB) with bursts just above it
    sr = munt.SR
    rng = np.random.default_rng(1)
    n = int(0.1 * sr)
    hit = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.04 * sr))
    junk = rng.standard_normal(int(0.4 * sr)) * (1.2 / 127) * np.max(np.abs(hit))   # 1.2 LSB RMS
    junk[int(0.2 * sr):int(0.22 * sr)] *= 1.6                     # a 1.9 LSB burst: above the floor
    x = np.concatenate([np.zeros(100), hit, junk])
    assert smp.floor_time(x, 100, len(x)) > 0.3                    # last crossing: keeps the junk
    assert smp.drum_end(x, 100, len(x)) < 0.12                     # ends with the decay
    # a smooth decay through the floor ends at the floor, as floor_time does
    y = np.concatenate([np.zeros(100), decaying_tone(660, 2.0, 150.0)])
    assert abs(smp.drum_end(y, 100, len(y)) - smp.floor_time(y, 100, len(y))) < 0.01


def test_one_shot_when_the_whole_sound_fits_the_attack_and_loop_budget():
    tables = synthetic_tables()
    # 0.21 s at ~9.6 kHz = 2 KB: fits the 10 pages of a 2040 B attack + 512 B loop
    shot = smp.one_shot_tuning(0.21, 9644.0, 10, tables, 76, 72, 84, 9000)
    assert shot is not None
    rate, nbytes, note, ft = shot
    assert nbytes <= 2048 - smp.STOPPER and rate <= 9644.0 and rate >= 9644.0 * 0.9
    assert abs(smp.PAL / tables[ft][note] - rate) < 1e-6                  # an exact table rate
    # a 1.6 s decay would need far more than the budget at any acceptable rate
    assert smp.one_shot_tuning(1.6, 16744.0, 10, tables, 48, 40, 60, 9000) is None


def test_tremolo_metric_sees_a_level_drift_across_the_loop():
    assert smp.loop_tremolo_db(np.tile(np.sin(2 * np.pi * np.arange(60) / 60), 17), 17) < 0.01
    assert smp.loop_tremolo_db(wobbly_loop(), 17) > 2.0


def test_raw_loop_gate():
    cfg = {"raw_loop_slow_hz": 2.0, "raw_loop_max_tremolo_db": 1.0, "raw_loop_max_buzz_db": -50.0}
    lp = wobbly_loop()                                    # 1020 points
    assert not smp.raw_loop_ok(lp, 17, 15700.0, cfg)        # repeats at ~15 Hz: an added tremolo
    assert smp.raw_loop_ok(lp, 17, 1500.0, cfg)             # repeats at ~1.5 Hz: the sound's own wobble
    clean = np.tile(np.sin(2 * np.pi * np.arange(60) / 60), 17)
    assert smp.raw_loop_ok(clean, 17, 15700.0, cfg)         # fast but exactly periodic


def test_loop_problems_flags_a_tremolo_loop_in_a_sample_set():
    cfg = {"raw_loop_slow_hz": 2.0, "raw_loop_max_tremolo_db": 1.0, "raw_loop_max_buzz_db": -50.0}
    lp = wobbly_loop()
    s = smp.ModSample("Elec Organ", np.concatenate([np.zeros(1016), lp / np.max(np.abs(lp))]),
                     loop_start=1016, loop_len=len(lp))
    s.cycles, s.rate = 17, 15700.0
    assert smp.loop_problems([s], cfg) and "tremolo" in smp.loop_problems([s], cfg)[0]
    s.rate = 1500.0
    assert smp.loop_problems([s], cfg) == []


def test_loop_planner_skips_raw_sizes_that_fail_the_gate():
    s = smp.ModSample("organ", np.zeros(2552), loop_start=2040, loop_len=512, kind="melodic")
    s.steady, s.max_loop, s.raw_ok = True, 16384, (8192,)       # 1024-4096 would add a tremolo
    sizes = smp.plan_loops([s], {0: 10.0}, 239, 16384)
    assert sizes[0] == 8192
    small = smp.plan_loops([s], {0: 10.0}, 20, 16384)           # no room for 8192: stays averaged
    assert small[0] == 512


def test_loop_construction_defaults_to_eprime_and_rejects_unknown_names():
    assert smp.loop_construction({}) == "eprime"
    assert smp.loop_construction({"loop_construction": "averaged"}) == "averaged"
    with pytest.raises(ValueError, match="loop_construction"):
        smp.loop_construction({"loop_construction": "eprim"})


def test_sustain_contour_does_not_droop_into_the_release():
    # a steady note held 2 s, then a fast release: smoothing over the release pulled the last
    # 0.4 s of the contour down, and envelopes hold their last value for longer notes
    sr = munt.SR
    env = np.concatenate([np.ones(2 * sr), np.exp(-np.arange(sr) / (0.05 * sr))])
    c = smp.sustain_contour(env, 2 * sr, 0.8)
    assert 20 * np.log10(c[int(1.95 * sr)]) > -0.1
    old = np.exp(smp.smooth(np.log(np.maximum(env, 1e-6)), int(0.8 * sr)))
    assert 20 * np.log10(old[int(1.95 * sr)]) < -10                 # the old droop
