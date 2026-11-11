# SPDX-License-Identifier: GPL-3.0-or-later
"""The MI1 MIDI Synth bank builder's Munt-free parts: envelope fitting and the slot map."""

import tomllib

import numpy as np

from migs import midisynth_tuning as dt
from migs.mt32bank import MIN_RELEASE_RATE, SR, fit_envelope, map_toml


def _contour(levels_db, seconds):
    """A piecewise-linear (in dB) loudness envelope through (t, dB) points, at SR."""
    t = np.arange(int(seconds * SR)) / SR
    return 10 ** (np.interp(t, *zip(*levels_db)) / 20)


def test_a_held_sound_keeps_its_sustain_and_releases_promptly():
    env = _contour([(0, -60), (0.02, 0), (0.3, -3), (2.0, -3), (2.05, -40), (3.0, -80)], 3.0)
    e = fit_envelope(env, 0, int(2.0 * SR))
    attack_level, sustain = e[0], e[6]
    assert attack_level == 127 and 80 <= sustain <= 100          # -3 dB of linear 127 is 90
    assert e[9] >= MIN_RELEASE_RATE                              # release 1 rate
    assert e[8] == 0                                             # release 1 goes to silence


def test_a_decaying_sound_reaches_zero_by_itself_so_its_voice_frees():
    env = _contour([(0, -60), (0.01, 0), (0.6, -30), (2.0, -70)], 2.5)
    e = fit_envelope(env, 0, int(2.0 * SR))
    assert e[6] == 0                                             # sustain 0: the voice ends at its decay
    assert dt.ENVELOPE_RATE_SECONDS[e[7]] < 13                   # and gets there (decay 3 isn't the slowest)


def test_map_toml_is_what_mid2seq_reads():
    m = tomllib.loads(map_toml("config/mt32sounds.toml", {9: 2, 13: 2}, {36: [0, 40], 38: [0, 42]}))
    assert m["programs"] == {"9": 2, "13": 2} and m["drums"]["38"] == [0, 42]
