# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic tones with known answers for the loop tests (test_loopbuild, test_looplab)."""

import numpy as np

from migs import munt

SR = munt.SR


def tone(f0, secs, amps=(1.0, 0.5, 0.3), detune_hz=0.0, env=None):
    t = np.arange(int(secs * SR)) / SR
    y = np.zeros_like(t)
    for h, a in enumerate(amps, 1):
        g = env(t, h) if env else 1.0
        y += a * g * np.sin(2 * np.pi * h * f0 * t)
        if detune_hz:
            y += a * g * np.sin(2 * np.pi * h * (f0 + detune_hz) * t + 0.3)
    return y / np.max(np.abs(y))


def cycle(n, amps=(1.0, 0.5, 0.3)):
    t = np.arange(n) / n
    return sum(a * np.sin(2 * np.pi * h * t + 0.4 * h) for h, a in enumerate(amps, 1))


def with_sub_octave(f0, secs, level=0.4):
    """A note at f0 with a steady partial series an octave below (1/2, 3/2 x f0), as the MT-32's
    Pipe Org 2 has at every key."""
    t = np.arange(int(secs * SR)) / SR
    return tone(f0, secs) + level * (np.sin(2 * np.pi * f0 / 2 * t) + 0.7 * np.sin(2 * np.pi * 1.5 * f0 * t))
