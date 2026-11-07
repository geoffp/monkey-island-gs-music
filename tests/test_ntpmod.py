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


def test_loop_is_exactly_periodic_and_the_attack_flows_into_it():
    sr = nm.mb.SR
    t = np.arange(sr) / sr
    # a chorused tone: two slightly detuned partials, so raw cycles drift (what made loops buzz)
    x = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 221.3 * t + 1) + 0.3 * np.sin(2 * np.pi * 660 * t)
    period, k, loop_bytes, attack_bytes = sr / 220, 8, 512, 2040
    rate = loop_bytes * 220 / k
    attack, loop, _ = nm.attack_and_loop(x, 100, period, rate, k, attack_bytes, loop_bytes)
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


def test_drum_rate_follows_bandwidth():
    sr = nm.mb.SR
    rng = np.random.default_rng(0)
    n = int(0.05 * sr)
    hat = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.01 * sr))
    hat = np.diff(hat, prepend=0)                               # high-passed noise burst
    kick = np.sin(2 * np.pi * 60 * np.arange(sr) / sr) * np.exp(-np.arange(sr) / (0.3 * sr))
    assert nm.drum_rate(hat, 1016, 6000, 26000) > 20000
    assert nm.drum_rate(kick, 2040, 6000, 26000) == 6000
    long_noise = np.diff(rng.standard_normal(sr), prepend=0)   # can't fit: trade, but at most half
    assert 13000 <= nm.drum_rate(long_noise, 2040, 6000, 26000) < 26000


def _song(tmp_path, events, name="s.mid"):
    import mido
    mf = mido.MidiFile(ticks_per_beat=120)
    mf.tracks.append(mido.MidiTrack(events))
    p = tmp_path / name
    mf.save(p)
    return p


def test_channel_volume_and_swell_set_levels(tmp_path):
    import mido
    ev = [mido.Message("program_change", channel=1, program=9), mido.Message("program_change", channel=2, program=9),
          mido.Message("control_change", channel=1, control=7, value=100),
          mido.Message("control_change", channel=2, control=7, value=64)]
    for ch in (1, 2):
        ev += [mido.Message("note_on", channel=ch, note=60, velocity=100, time=0),
               mido.Message("note_off", channel=ch, note=60, time=240)]
    p = _song(tmp_path, ev)
    notes, _, _ = nm.read_notes(p)
    assert sorted(n.volume for n in notes) == [64, 100]
    cc7 = (np.array([0.0, 64, 100, 127]), np.array([0.0, 0.35, 1.0, 1.8]))
    s = _fake()
    s.env_g = np.array([1.0, 2.0])                 # swells to twice its attack level
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, _, _ = nm.convert_song(p, [s], {9: 0}, {}, cfg, dict(cfg), cc7)
    vols = []
    for cells in grid.cells:
        on = [c for c in cells.values() if c[0]]
        if on:
            seq = [c[3] for _, c in sorted(cells.items()) if c[2] == 0xC]
            vols.append((seq[0], max(seq)))
    (a0, a_max), (b0, b_max) = sorted(vols, reverse=True)
    assert a_max == 64 and a0 == pytest.approx(32, abs=2)     # loudest moment (the swell) = 64
    assert b0 == pytest.approx(a0 * 0.35, abs=1.5)              # CC7 64 vs 100 per the curve


def test_tempo_commands_stay_on_the_ideal_grid():
    ideal, speed, rows, tol = 97.56, 3, 2000, 0.002
    ch = nm.tempo_commands(ideal, speed, rows, lambda r: True, tol)
    assert ch[0][0] == 0 and {t for _, t in ch} <= {97, 98}
    tempo, actual, worst = dict(ch), 0.0, 0.0
    cur = ch[0][1]
    for r in range(rows):
        cur = tempo.get(r, cur)
        worst = max(worst, abs(actual - r * 2.5 * speed / ideal))
        actual += 2.5 * speed / cur
    assert worst < tol + 2.5 * speed / 97                 # within tolerance + one row's error
    assert worst < 0.01                                   # vs ~0.35 s of drift with a fixed tempo
    assert len(ch) < rows / 5                             # hysteresis keeps changes sparse
    assert nm.tempo_commands(98.0, 3, 100, lambda r: True, tol) == [(0, 98)]


def test_envelope_rows_average_tremolo_instead_of_aliasing_it():
    s = _fake()
    s.env_t = np.arange(0, 3, 0.01)
    s.env_g = 1 + 0.8 * np.sin(2 * np.pi * 7 * s.env_t)          # 7 Hz tremolo around 1
    rows = [nm.env_at(s, r * 0.154, 0.154) for r in range(1, 18)]  # MI153's 154 ms rows
    assert max(rows) - min(rows) < 0.3                             # point sampling: ~1.6


def test_ntp_only_sounds_split_out_of_bank_merges():
    spec = {"melodic": [{"slot": 2, "name": "Elec Organ", "program": 9, "covers": [9, 13], "key": 60},
                        {"slot": 3, "name": "Solo", "program": 20, "covers": [20], "key": 60}], "drum": []}
    conf = {"melodic": [{"slot": 101, "name": "Pipe Organ", "program": 13, "covers": [13], "key": 60},
                        {"slot": 102, "name": "Solo2", "program": 20, "covers": [20], "key": 60}]}
    out = nm.ntp_spec(spec, conf)
    assert {m["name"]: m["covers"] for m in out["melodic"]} == {"Elec Organ": [9], "Pipe Organ": [13], "Solo2": [20]}
    assert spec["melodic"][0]["covers"] == [9, 13]                  # shared bank spec untouched
