# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the NinjaTrackerPlus tests (not collected: no test_ prefix)."""

from pathlib import Path

import mido
import numpy as np

from migs import ntpsamples as smp

MAKE_TABLES = Path(__file__).resolve().parents[1] / "vendor" / "ntp" / "src" / "make_tables.php"


def fake_sample(kind="melodic", **kw):
    s = smp.ModSample("x", np.zeros(2552), loop_start=2040, loop_len=512, kind=kind, base_note=30, key0=60,
                     env_t=np.array([0.1, 1.0]), env_g=np.array([1.0, 0.5]), rel_g=np.array([1.0, 0.5, 0.0]), **kw)
    return s


def midi_song(tmp_path, events, name="s.mid"):
    mf = mido.MidiFile(ticks_per_beat=120)
    mf.tracks.append(mido.MidiTrack(events))
    p = tmp_path / name
    mf.save(p)
    return p


# --- one-shot detection -------------------------------------------------------

def decaying_tone(f0, secs, db_per_s, sr=32000):
    t = np.arange(int(secs * sr)) / sr
    return np.sin(2 * np.pi * f0 * t) * 10 ** (-db_per_s * t / 20)


def synthetic_tables():
    return {ft: [int(round(smp.PAL / (8363 * 2 ** ((n - 24 + ft / 8) / 12)))) for n in range(smp.NOTES)]
            for ft in range(-8, 8)}


# --- raw-loop tremolo / buzz gate ------------------------------------------------

def wobbly_loop(k=17, spc=60, depth_db=6.0, wobble_cycles=0.5):
    """k cycles whose level drifts through `wobble_cycles` of a slow wobble (a raw slice of a
    rotary organ shorter than its wobble period)."""
    t = np.arange(k * spc) / spc
    g = 10 ** (depth_db / 20 * 0.5 * np.sin(2 * np.pi * wobble_cycles * t / k))
    return np.sin(2 * np.pi * t) * g
