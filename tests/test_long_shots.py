"""Long one-shots (`long_shots`): the vibrato tracker on notes that end, the fine term, the
flattened sample and its decay, and the planner's one-shot sizes."""
import numpy as np

from migs import docsim2 as d2
from migs import ntpsamples as smp
from migs.docram import DOC_RATE
from migs.munt import SR


def tone(f0, seconds, fs, decay_s=None):
    t = np.arange(int(seconds * fs)) / fs
    y = np.sin(2 * np.pi * f0 * t) + 0.3 * np.sin(4 * np.pi * f0 * t)
    return y * np.exp(-t / decay_s) if decay_s else y


def test_a_note_that_ends_has_no_vibrato():
    # 1.5 s of a steady tone, then silence: the silent frames once read as ~1100 cents
    y = np.concatenate([tone(440.0, 1.5, DOC_RATE), np.zeros(int(1.5 * DOC_RATE))])
    assert d2.pitch_vibrato(y, DOC_RATE, 440.0) < d2.VIB_DEAD_CENTS      # within the tracker's own wobble


def test_a_note_that_decays_into_noise_has_no_vibrato():
    rng = np.random.default_rng(1)
    y = tone(440.0, 3.0, DOC_RATE, decay_s=0.15)
    y = np.round(y * 127) / 127 + rng.normal(0, 0.3 / 127, len(y))     # 8-bit floor and noise
    assert d2.pitch_vibrato(y, DOC_RATE, 440.0) < 5.0


def glide(f_from, f_to, seconds, fs):
    t = np.arange(int(seconds * fs)) / fs
    f = f_from * (f_to / f_from) ** (t / seconds)
    return np.sin(2 * np.pi * np.cumsum(f) / fs)


def test_fine_term_hears_a_slide():
    fs = DOC_RATE
    base = tone(1000.0, 2.4, fs)
    ref = base + glide(3600.0, 2500.0, 2.4, fs)
    frozen = base + np.sin(2 * np.pi * 3000.0 * np.arange(len(base)) / fs)
    p, q = d2.loudness_pattern(ref), d2.loudness_pattern(frozen)
    assert d2.fine_distance(p, p) == 0.0
    assert d2.fine_distance(p, q) > 0.1


def test_flattened_divides_the_decay_out_and_gives_it_back():
    x = tone(440.0, 2.0, SR, decay_s=0.4)
    seg, t, g = smp.flattened(x, 0.25)
    assert t[0] == 0.25 and g[0] > 0.95 and g[-1] < 0.05       # the decay, from flat_s
    env = smp.audio.rms_envelope(seg)
    i0, i1 = int(0.4 * SR), int(1.8 * SR)
    assert env[i1] / env[i0] > 0.7                              # stored near flat
    # put back by the gain, it is the source again
    back = seg * np.where(np.arange(len(seg)) / SR < t[0], 1.0, np.interp(np.arange(len(seg)) / SR, t, g))
    assert np.max(np.abs(back - x)[int(0.3 * SR):int(1.5 * SR)]) < 0.05


def test_play_sample_applies_a_flattened_shots_decay():
    s = smp.ModSample("s", np.full(8000, 0.5), kind="melodic", env_t=np.array([0.1, 0.5]),
                      env_g=np.array([1.0, 0.1]))
    s.rate, s.shot_env = DOC_RATE, True
    y = d2.play_sample(s, 0.0, 0.25)
    assert abs(y[int(0.05 * DOC_RATE)] - 0.5) < 0.01
    assert y[int(0.2 * DOC_RATE)] < 0.45                        # on its way down
    s.shot_env = False
    assert d2.play_sample(s, 0.0, 0.25)[int(0.2 * DOC_RATE)] > 0.49


def _sample(name, loop_len, att=2048, shots=()):
    s = smp.ModSample(name, np.zeros(att + loop_len), loop_start=att, loop_len=loop_len, kind="melodic")
    s.steady = False
    s.raw_ok = tuple(smp.SHOT | z for z in shots)
    return s


def test_planner_prices_a_one_shot_against_attack_and_loop():
    s = _sample("Ice", 512, shots=(16384,))
    gain = {512: 0.0, smp.SHOT | 16384: -6.0}
    sizes, lines, _ = smp.plan_loops_valued([s], {0: 1.0}, 200, 16384, lambda i, z: gain[z])
    assert sizes[0] == smp.SHOT | 16384
    assert any("shot 16384" in ln and "54 pages" in ln for ln in lines)     # 64 - (8 + 2)


def test_planner_one_shot_may_displace_a_sounds_loops():
    # the organ's loop (more per page) is bought first and leaves too little room; the
    # one-shot is worth more in all, so with singles_give_way it takes the loop's pages
    ice = _sample("Ice", 512, shots=(16384,))
    org = _sample("Org", 512)
    org.steady, org.raw_ok, org.max_loop = True, (8192,), 8192
    score = {(0, 512): 0.0, (0, smp.SHOT | 16384): -6.0, (1, 512): 0.0, (1, 8192): -4.0}
    usage = {0: 1.0, 1: 1.0}
    last = 20 + 54 - 1 + 4          # ice 8+2, org 8+2 pages: room for one of the two purchases
    sizes, lines, _ = smp.plan_loops_valued([ice, org], usage, last, 16384, lambda i, z: score[(i, z)],
                                            singles_give_way=True)
    assert sizes[0] == smp.SHOT | 16384 and sizes[1] == 512
    assert any("takes the pages of Org" in ln for ln in lines)
    sizes, _, _ = smp.plan_loops_valued([ice, org], usage, last, 16384, lambda i, z: score[(i, z)])
    assert sizes == {0: 512, 1: 8192}                           # by default the loop keeps its pages

