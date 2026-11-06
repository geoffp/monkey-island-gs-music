"""ProTracker MOD / NinjaTrackerPlus building blocks."""

import struct
from pathlib import Path

import numpy as np
import pytest

from migs import ntpmod as nm

MAKE_TABLES = Path(__file__).resolve().parents[1] / "vendor" / "ntp" / "src" / "make_tables.php"


@pytest.mark.skipif(not MAKE_TABLES.exists(), reason="run `just fetch-ntp`")
def test_period_tables_have_16_finetunes_of_72_notes():
    t = nm.period_tables(MAKE_TABLES)
    assert sorted(t) == list(range(-8, 8))
    assert all(len(v) == 72 for v in t.values())
    assert t[0][:2] == [1712, 1616] and t[0][24] == 428          # C-2 = 428 on finetune 0
    assert t[1][0] > t[0][0] > t[-1][0] or t[-1][0] > t[0][0]     # finetunes shift periods


def test_ntp_doc_blocks_follow_the_converters_rules():
    shot = nm.ModSample("s", np.zeros(1014))                      # + 8 stopper bytes -> 1 KB
    assert shot.ntp_blocks() == [1024]
    ideal = nm.ModSample("i", np.zeros(1024))                     # ideal size: no stoppers
    assert ideal.ntp_blocks() == [1024]
    looped = nm.ModSample("l", np.zeros(2552), loop_start=2040, loop_len=512)
    assert looped.ntp_blocks() == [2048, 512]                    # header 2040+8, loop ideal


def test_grid_detection_on_jittered_16ths():
    rng = np.random.default_rng(1)
    onsets = [(37.0 * i + 5 + rng.uniform(-2, 2)) * nm.MI1_TICK for i in range(200)]
    unit, phase = nm.detect_grid(onsets)
    assert unit == pytest.approx(37.0, abs=0.3) and phase == pytest.approx(5, abs=1.5)


def test_mod_bytes_layout():
    s = nm.ModSample("Sine", np.sin(np.linspace(0, 6.28, 100)), loop_start=40, loop_len=60, finetune=-3)
    s.default_volume = 40
    g = nm.Grid(12)
    c = g.get(3, 5)
    c[:] = [25, 1, 0xE, 0xD2]            # note index 24 (C-2), sample 1, ED2
    tables = {0: [1712 - i for i in range(72)]}
    tables[0][24] = 428
    mod = nm.mod_file("Test", [s], g, 1, tables)
    assert mod[:4] == b"Test" and mod[1080:1084] == b"12CH"
    name, words, ft, vol, ls, ll = struct.unpack(">22sHBBHH", mod[20:50])
    assert (words, ft, vol, ls, ll) == (50, 0x0D, 40, 20, 30)   # lengths/loops in words; ft -3 = 0xD
    assert mod[950] == 1 and mod[952] == 0                      # song length 1, order [0]
    o = 1084 + (5 * 12 + 3) * 4
    w0, w1 = struct.unpack(">HH", mod[o:o + 4])
    assert w0 & 0xFFF == 428 and (w0 >> 12) == 0 and (w1 >> 12) == 1 and w1 & 0xFFF == 0xED2
    assert len(mod) == 1084 + 64 * 12 * 4 + 100
    assert nm.mod_file("T", [s], nm.Grid(9), 1, tables)[1080:1084] == b"9CHN"


def _fake(kind="melodic", **kw):
    s = nm.ModSample("x", np.zeros(2552), loop_start=2040, loop_len=512, kind=kind, base_note=30, key0=60,
                     env_t=np.array([0.1, 1.0]), env_g=np.array([1.0, 0.5]), rel_g=np.array([1.0, 0.5, 0.0]), **kw)
    return s


def test_conversion_invariants(tmp_path):
    import mido
    mf = mido.MidiFile(ticks_per_beat=120)
    t = mido.MidiTrack([mido.Message("program_change", channel=1, program=9, time=0)])
    tick = 0
    for i in range(32):                                          # jittered 16ths, chords of 3
        at = 30 * i + (i % 3)
        for j, k in enumerate((60, 64, 67)):
            t.append(mido.Message("note_on", channel=1, note=k, velocity=40 + 2 * i, time=at - tick))
            tick = at
        for k in (60, 64, 67):
            t.append(mido.Message("note_off", channel=1, note=k, velocity=0, time=0))
    mf.tracks.append(t)
    p = tmp_path / "1.mid"
    mf.save(p)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, patterns, rep = nm.convert_song(p, [_fake()], {9: 0}, {}, cfg, dict(cfg))
    assert rep.notes == 96 and rep.dropped == 0
    # 3-note chords 2 rows apart: tracks still releasing alternate with fresh ones -> 3 or 6
    assert rep.oscillators <= 31 and rep.tracks_used in (3, 6)
    for cells in grid.cells:                                       # a track never holds two notes
        rows = sorted(r for r, c in cells.items() if c[0])
        assert all(b - a >= 2 for a, b in zip(rows, rows[1:]))
    row0 = [grid.cells[tr].get(0) for tr in range(2)]
    assert [(c[2], c[3]) for c in row0] == [(0xF, rep.speed), (0xF, rep.tempo)]
    for cells in grid.cells:
        for row, (note, inst, eff, par) in cells.items():
            if eff == 0xE:
                assert par >> 4 == 0xD and (par & 0xF) < rep.speed      # delays stay inside the row
            if eff == 0xC:
                assert 0 <= par <= 64
    assert rep.timing_err_ms < rep.row_ms / rep.speed                # micro-timing kept
