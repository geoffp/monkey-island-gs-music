"""migs.docrender: our MODs played as the DOC plays them."""
import numpy as np

from migs import docrender
from migs.docram import DOC_RATE
from migs.modfile import mod_file
from migs.ntpsamples import PAL, ModSample
from migs.ntpsong import FX_EXTENDED, FX_JUMP, FX_SPEED, FX_VOLUME, NOTE_DELAY, Grid

# a minimal period table: every finetune the same, ProTracker's octave 2 C = 428 at note 25
TABLES = {ft: [int(round(428 * 2 ** ((25 - i) / 12))) for i in range(1, 73)] for ft in range(-8, 8)}


def song(cells, samples, patterns=1, tracks=1):
    g = Grid(tracks)
    for (t, r), c in cells.items():
        g.get(t, r)[:] = c
    return docrender.read_mod(mod_file("t", samples, g, patterns, TABLES))


def square(n=512):
    s = ModSample("sq", np.where(np.arange(n) % 64 < 32, 0.5, -0.5), loop_start=0, loop_len=n, kind="melodic")
    s.default_volume = 64
    return s


LINEAR = docrender.volume_gains(None)


def render_doc(mod, tables, gains, tail_s=1.0, max_s=600.0):
    """The whole song's DOC sides (n x 2) from doc_blocks."""
    out = list(docrender.doc_blocks(mod, tables, gains, tail_s, max_s))
    return np.concatenate(out) if out else np.zeros((0, 2))


def to_output(doc, rate=48000, block=1 << 15):
    """DOC-rate sides -> `rate`, a block at a time through Output."""
    o = docrender.Output(rate, doc.shape[1], block)
    parts = [o.feed(doc[at:at + block]) for at in range(0, doc.shape[0], block)] + [o.finish()]
    return np.concatenate(parts)


def test_a_note_plays_at_its_rate_and_volume_for_its_rows():
    # speed 6, tempo 125: 20 ms ticks, 0.12 s rows; two rows of note 25 (period 428) at volume 32
    m = song({(0, 0): [25, 1, FX_SPEED, 6], (0, 1): [0, 0, FX_VOLUME, 32]}, [square()])
    y = render_doc(m, TABLES, LINEAR, tail_s=0.0)
    left = y[:, 1 - docrender.STEREO[0]]
    row = int(0.12 * DOC_RATE)
    assert abs(len(y) - 64 * row) <= 64                   # one pass of the pattern
    assert abs(np.max(np.abs(left[:row])) - 0.5 * 127 / 128) < 0.01     # volume 64
    assert abs(np.max(np.abs(left[row:2 * row])) - 0.25 * 127 / 128) < 0.01  # Cxx 32, at once
    # 64-byte square cycles at PAL / 428 bytes per second
    crossings = np.count_nonzero(np.diff(np.sign(left[:row])) > 0)
    assert abs(crossings - PAL / 428 / 64 * 0.12) <= 1


def test_the_measured_curve_is_applied_to_volumes():
    gains = docrender.volume_gains("config/doc-volume.toml")
    assert gains[0] == 0.0 and abs(gains[64] - 1.0) < 1e-9
    assert 20 * np.log10(gains[8]) < -30                  # quiet codes are far quieter than linear (-18 dB)


def test_a_delayed_note_starts_on_its_tick():
    m = song({(0, 0): [25, 1, FX_EXTENDED, NOTE_DELAY | 3]}, [square()])
    y = render_doc(m, TABLES, LINEAR, tail_s=0.0)
    first = np.nonzero(np.abs(y).sum(1))[0][0]
    assert abs(first - 3 * 0.02 * DOC_RATE) < 3


def test_a_looping_song_plays_one_pass():
    # B00 on the last row of pattern 1 jumps back to pattern 1: patterns 0, 1, then stop
    m = song({(0, 0): [25, 1, FX_SPEED, 6], (0, 127): [0, 0, FX_JUMP, 1]}, [square()], patterns=2)
    y = render_doc(m, TABLES, LINEAR, tail_s=0.0)
    assert abs(len(y) - 128 * int(0.12 * DOC_RATE)) <= 128


def test_tracks_pan_hard_as_ntp_does():
    m = song({(0, 0): [25, 1, -1, 0], (1, 0): [25, 1, -1, 0]}, [square()], tracks=2)
    y = render_doc(m, TABLES, LINEAR, tail_s=0.0)
    # tracks 0 and 1 are on opposite sides (1 0 0 1 ...)
    assert np.max(np.abs(y[:, 0])) > 0.4 and np.max(np.abs(y[:, 1])) > 0.4


def test_output_keeps_the_images_a_desktop_player_smooths_away():
    # a 6 kHz partial played at the DOC rate has an image at 26.32 - 6 = 20.32 kHz: the hold and
    # the 17.7 kHz filter soften it, but it is there (an interpolating player leaves next to none)
    t = np.arange(DOC_RATE) / DOC_RATE
    doc = np.repeat(np.round(np.sin(2 * np.pi * 6000 * t) * 127)[:, None] / 128, 2, axis=1)
    y = to_output(doc, 48000)[:, 0]
    X = np.abs(np.fft.rfft(y)) ** 2
    f = np.fft.rfftfreq(len(y), 1 / 48000)
    image = X[(f > 20000) & (f < 20700)].sum() / X[(f > 5900) & (f < 6100)].sum()
    assert 1e-3 < image < 0.5


def test_to_output_by_blocks_is_resample_poly_of_the_whole():
    # the block-at-a-time output stage gives the samples the whole-song one did, at any length
    import math
    from scipy.signal import butter, resample_poly, sosfilt
    fs = DOC_RATE * docrender.HOLD
    g = math.gcd(fs, 48000)
    for n in (1, 7, 1000, 2500):
        doc = np.random.default_rng(n).standard_normal((n, 2))
        whole = resample_poly(sosfilt(butter(2, docrender.FILTER_HZ, fs=fs, output="sos"),
                                      np.repeat(doc, docrender.HOLD, axis=0), axis=0),
                              48000 // g, fs // g, axis=0)
        assert np.array_equal(to_output(doc, 48000, block=700), whole)


def test_render_wav_streams_what_the_whole_song_gives():
    # a block at a time, the song is never held as floats: the same 16-bit samples, scaled or not
    m = song({(0, 0): [25, 1, FX_SPEED, 6], (0, 40): [37, 1, FX_VOLUME, 48]}, [square()])
    whole = to_output(render_doc(m, TABLES, LINEAR), 48000) * 3.0
    pcm, peak = docrender.render_wav(m, TABLES, LINEAR, 48000, 3.0)
    assert abs(peak - np.max(np.abs(whole))) < 1e-12
    assert np.array_equal(pcm, docrender._pcm(whole))
    scaled, _ = docrender.render_wav(m, TABLES, LINEAR, 48000, 3.0, scale=peak)
    assert np.array_equal(scaled, docrender._pcm(whole / peak))


def test_a_volume_slide_falls_on_each_tick_after_the_first():
    # A04 at speed 6: 32 on tick 0, then 28, 24, ... 12; the next row (no effect) holds 12
    from migs.ntpsong import FX_SLIDE
    m = song({(0, 0): [25, 1, FX_SPEED, 6], (0, 1): [0, 0, FX_VOLUME, 32], (0, 2): [0, 0, FX_SLIDE, 4]}, [square()])
    y = render_doc(m, TABLES, LINEAR, tail_s=0.0)[:, 1 - docrender.STEREO[0]]
    tick = int(0.02 * DOC_RATE)
    for k, want in enumerate([32, 28, 24, 20, 16, 12, 12]):
        a = 2 * 6 * tick + k * tick
        assert abs(np.max(np.abs(y[a + 50:a + tick - 50])) - 0.5 * 127 / 128 * want / 64) < 0.01, k
