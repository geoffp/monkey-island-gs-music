"""Tuning over the keys a sample plays: whole-number periods, and the build's tuning report."""
from pathlib import Path

import numpy as np
import pytest

from migs import ntpmod
from migs import ntpsamples as smp

MAKE_TABLES = Path("vendor/ntp/src/make_tables.php")
pytestmark = pytest.mark.skipif(not MAKE_TABLES.exists(), reason="run `just fetch-ntp`")
TABLES = smp.period_tables(MAKE_TABLES) if MAKE_TABLES.exists() else {}


def test_the_period_tables_step_unevenly_at_the_top():
    # why a sample can't play far above its key in tune: ft 0's top octave is 28..53
    cents = np.diff(1200 * np.log2(smp.PAL / np.array(TABLES[0], dtype=float)))
    assert np.all(np.abs(cents[:24] - 100) < 5)                 # long periods: even semitones
    assert np.max(np.abs(cents[60:] - 100)) > 30                # periods 28-57: up to a third of a step


def test_note_cents_is_what_ntp_plays():
    s = smp.ModSample("x", np.zeros(8), finetune=0, base_note=36, key0=60)
    s.rate = smp.PAL / TABLES[0][36]
    assert abs(ntpmod.note_cents(s, 36, 60, TABLES)) < 1e-9
    assert abs(ntpmod.note_cents(s, 48, 72, TABLES)) < 2        # an octave up, periods ~214 -> 107
    assert abs(ntpmod.note_cents(s, 71, 95, TABLES)) > 10       # period 28: coarse


def test_tuning_report_names_the_sounds_off_by_more_than_10_cents():
    s = smp.ModSample("Bass", np.zeros(8), finetune=0, base_note=36, key0=60)
    s.rate = smp.PAL / TABLES[0][36] * 2 ** (30 / 1200)        # 30 cents flat everywhere
    ok = smp.ModSample("Organ", np.zeros(8), finetune=0, base_note=36, key0=60)
    ok.rate = smp.PAL / TABLES[0][36]
    line = ntpmod.tuning_report([s, ok], {(0, 36, 60): 3, (1, 36, 60): 5}, TABLES)
    assert line.startswith("tuning 3 of 8 notes over 10 cents (3 over 20): Bass 30")
    assert ntpmod.tuning_report([ok], {(0, 36, 60): 5}, TABLES) == ""
