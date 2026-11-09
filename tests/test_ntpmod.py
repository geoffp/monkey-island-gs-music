"""ProTracker MOD / NinjaTrackerPlus building blocks: samples (ntpsamples), arrangement
(ntpsong), the MOD writer (modfile) and the build's configuration (ntpmod)."""

import struct
from pathlib import Path

import numpy as np
import pytest

from migs import audio, modfile, munt
from migs import ntpmod as nm
from migs import ntpsamples as smp
from migs import ntpsong as sg

MAKE_TABLES = Path(__file__).resolve().parents[1] / "vendor" / "ntp" / "src" / "make_tables.php"


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


def test_grid_detection_on_jittered_16ths():
    rng = np.random.default_rng(1)
    onsets = [(37.0 * i + 5 + rng.uniform(-2, 2)) * sg.MI1_TICK for i in range(200)]
    unit, phase = sg.detect_grid(onsets)
    assert unit == pytest.approx(37.0, abs=0.3) and phase == pytest.approx(5, abs=1.5)


def test_mod_bytes_layout():
    s = smp.ModSample("Sine", np.sin(np.linspace(0, 6.28, 100)), loop_start=40, loop_len=60, finetune=-3)
    s.default_volume = 40
    g = sg.Grid(12)
    c = g.get(3, 5)
    c[:] = [25, 1, 0xE, 0xD2]            # note index 24 (C-2), sample 1, ED2
    tables = {0: [1712 - i for i in range(72)]}
    tables[0][24] = 428
    mod = modfile.mod_file("Test", [s], g, 1, tables)
    assert mod[:4] == b"Test" and mod[1080:1084] == b"12CH"
    name, words, ft, vol, ls, ll = struct.unpack(">22sHBBHH", mod[20:50])
    assert (words, ft, vol, ls, ll) == (50, 0x0D, 40, 20, 30)   # lengths/loops in words; ft -3 = 0xD
    assert mod[950] == 1 and mod[952] == 0                      # song length 1, order [0]
    o = 1084 + (5 * 12 + 3) * 4
    w0, w1 = struct.unpack(">HH", mod[o:o + 4])
    assert w0 & 0xFFF == 428 and (w0 >> 12) == 0 and (w1 >> 12) == 1 and w1 & 0xFFF == 0xED2
    assert len(mod) == 1084 + 64 * 12 * 4 + 100
    assert modfile.mod_file("T", [s], sg.Grid(9), 1, tables)[1080:1084] == b"9CHN"


def _fake(kind="melodic", **kw):
    s = smp.ModSample("x", np.zeros(2552), loop_start=2040, loop_len=512, kind=kind, base_note=30, key0=60,
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
    grid, patterns, rep = sg.convert_song(p, [_fake()], {9: 0}, {}, cfg, dict(cfg))
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
    notes, _, _ = sg.read_notes(p)
    assert sorted(n.volume for n in notes) == [64, 100]
    cc7 = (np.array([0.0, 64, 100, 127]), np.array([0.0, 0.35, 1.0, 1.8]))
    s = _fake()
    s.env_g = np.array([1.0, 2.0])                 # swells to twice its attack level
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, _, _ = sg.convert_song(p, [s], {9: 0}, {}, cfg, dict(cfg), cc7)
    vols = []
    for cells in grid.cells:
        on = [c for c in cells.values() if c[0]]
        if on:
            seq = [c[3] for _, c in sorted(cells.items()) if c[2] == 0xC]
            vols.append((seq[0], max(seq)))
    (a0, a_max), (b0, b_max) = sorted(vols, reverse=True)
    assert a_max == 64 and a0 == pytest.approx(32, abs=2)     # loudest moment (the swell) = 64
    assert b0 == pytest.approx(a0 * 0.35, abs=1.5)              # CC7 64 vs 100 per the curve


def test_steals_take_the_quietest_release(tmp_path):
    import mido
    # two tracks only; a loud note releases first, a quiet one later; a third note needs a track
    ev = [mido.Message("program_change", channel=ch, program=9) for ch in (1, 2, 3)]
    ev += [mido.Message("note_on", channel=1, note=60, velocity=127),
           mido.Message("note_on", channel=2, note=64, velocity=30),
           mido.Message("note_off", channel=1, note=60, time=240),     # 1.0 s
           mido.Message("note_off", channel=2, note=64, time=60),      # 1.25 s
           mido.Message("note_on", channel=3, note=67, velocity=100, time=60),
           mido.Message("note_off", channel=3, note=67, time=120)]
    p = _song(tmp_path, ev)
    s = _fake()
    s.rel_g = np.linspace(1.0, 0.0, 101)                               # 1 s release ramp
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 4,
           "unit_ticks": 30, "phase_ticks": 0}
    grid, _, rep = sg.convert_song(p, [s], {9: 0}, {}, cfg, dict(cfg))
    assert rep.tracks_used == 2 and rep.dropped == 0 and rep.stolen == 1
    played = [{c[0] for c in cells.values() if c[0] and c[1]} for cells in grid.cells]
    quiet, third = s.base_note + 4 + 1, s.base_note + 7 + 1
    assert any({quiet, third} <= notes for notes in played)               # the quiet tail was cut
    assert 0 < rep.stolen_levels[0] < 20                                   # not the loud one (~30)


def test_steals_spare_small_parts(tmp_path):
    import mido
    # a loud 3-note chord (channel 1) and a quiet bass note (channel 2) release together; a new
    # note (channel 3) finds the 4-track budget full
    ev = [mido.Message("program_change", channel=ch, program=9) for ch in (1, 2, 3)]
    ev += [mido.Message("note_on", channel=1, note=k, velocity=127) for k in (60, 64, 67)]
    ev += [mido.Message("note_on", channel=2, note=40, velocity=30)]
    ev += [mido.Message("note_off", channel=1, note=k, time=240 if k == 60 else 0) for k in (60, 64, 67)]
    ev += [mido.Message("note_off", channel=2, note=40),
           mido.Message("note_on", channel=3, note=72, velocity=100, time=120),
           mido.Message("note_off", channel=3, note=72, time=120)]
    p = _song(tmp_path, ev)
    s = _fake()
    s.rel_g = np.linspace(1.0, 0.0, 101)
    bass, new = s.base_note - 20 + 1, s.base_note + 12 + 1
    for protect, victim_is_bass in ((0, True), (2, False)):
        cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 8,
               "unit_ticks": 30, "phase_ticks": 0, "steal_protect": protect}
        grid, _, rep = sg.convert_song(p, [s], {9: 0}, {}, cfg, dict(cfg))
        assert rep.tracks_used == 4 and rep.stolen == 1
        played = [{c[0] for c in cells.values() if c[0] and c[1]} for cells in grid.cells]
        assert any({bass, new} <= notes for notes in played) == victim_is_bass
        assert rep.stolen_parts == [1 if victim_is_bass else 3]


def test_loop_only_sounds_take_one_oscillator(tmp_path):
    import mido
    # two overlapping notes, 2 oscillators: attack + loop fits one note, loop-only fits both
    ev = [mido.Message("program_change", channel=1, program=9),
          mido.Message("note_on", channel=1, note=60, velocity=100),
          mido.Message("note_on", channel=1, note=64, velocity=100, time=60),
          mido.Message("note_off", channel=1, note=60, time=120),
          mido.Message("note_off", channel=1, note=64, time=0)]
    p = _song(tmp_path, ev)
    lo = smp.ModSample("lo", np.zeros(512), loop_start=0, loop_len=512, base_note=30, key0=60,
                      env_t=np.array([0.0, 1.0]), env_g=np.array([1.0, 0.5]), rel_g=np.array([1.0, 0.0]))
    assert lo.ntp_blocks() == [512]
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 2, "unit_ticks": 30, "phase_ticks": 0}
    _, _, rep = sg.convert_song(p, [_fake()], {9: 0}, {}, cfg, dict(cfg))
    assert rep.dropped == 1
    _, _, rep = sg.convert_song(p, [lo], {9: 0}, {}, cfg, dict(cfg))
    assert rep.dropped == 0 and rep.track_kinds == ["drum", "drum"] and rep.oscillators == 2


def test_track_plan_keeps_early_one_oscillator_notes_from_fixing_the_split(tmp_path):
    import mido
    # 3 drum hits open 3 1-oscillator tracks first; a later 3-note chord then finds room for
    # one 2-oscillator track (budget 6). Planned: 3 melodic tracks, which the drums open and borrow.
    ev = [mido.Message("program_change", channel=1, program=9)]
    ev += [mido.Message("note_on", channel=9, note=k, velocity=100) for k in (36, 38, 42)]
    ev += [mido.Message("note_on", channel=1, note=k, velocity=100, time=240 if k == 60 else 0) for k in (60, 64, 67)]
    ev += [mido.Message("note_off", channel=1, note=k, time=120 if k == 60 else 0) for k in (60, 64, 67)]
    p = _song(tmp_path, ev)
    kick = _fake(kind="drum")
    kick.loop_len, kick.seconds = 0, 0.1
    drums = {k: (1, 20, 1.0) for k in (36, 38, 42)}
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 6, "unit_ticks": 30, "phase_ticks": 0}
    _, _, first = sg.convert_song(p, [_fake(), kick], {9: 0}, drums, cfg, dict(cfg))
    assert first.dropped == 2 and first.melodic_cap is None
    _, _, rep = sg.plan_tracks(p, [_fake(), kick], {9: 0}, drums, cfg, dict(cfg))
    assert rep.dropped == 0 and rep.melodic_cap == 3 and rep.track_kinds == ["melodic"] * 3
    _, _, off = sg.plan_tracks(p, [_fake(), kick], {9: 0}, drums, cfg, {**cfg, "track_plan": False})
    assert off.dropped == 2


def test_melodic_one_shots_may_use_one_oscillator_tracks(tmp_path):
    import mido
    ev = [mido.Message("program_change", channel=1, program=9),
          mido.Message("note_on", channel=1, note=60, velocity=100),
          mido.Message("note_off", channel=1, note=60, time=120)]
    p = _song(tmp_path, ev)
    shot = _fake()
    shot.loop_start, shot.loop_len, shot.seconds = 0, 0, 0.3
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "unit_ticks": 30, "phase_ticks": 0}
    _, _, rep = sg.convert_song(p, [shot], {9: 0}, {}, cfg, dict(cfg))
    assert rep.track_kinds[0] == "drum" and rep.oscillators == 1
    _, _, rep = sg.convert_song(p, [shot], {9: 0}, {}, cfg, {**cfg, "melodic_one_shots": "melodic"})
    assert rep.track_kinds[0] == "melodic"


def test_track_plan_prefers_fewer_tracks_at_equal_loss(tmp_path):
    import mido
    # a 2-note chord, then (after it has fully released) 2 drum hits: opening drum tracks makes
    # 4 tracks, reusing the idle melodic tracks ("first") keeps 2, with nothing lost either way
    ev = [mido.Message("program_change", channel=1, program=9)]
    ev += [mido.Message("note_on", channel=1, note=k, velocity=100) for k in (60, 64)]
    ev += [mido.Message("note_off", channel=1, note=k, time=120 if k == 60 else 0) for k in (60, 64)]
    ev += [mido.Message("note_on", channel=9, note=k, velocity=100, time=480 if k == 36 else 0) for k in (36, 38)]
    p = _song(tmp_path, ev)
    kick = _fake(kind="drum")
    kick.loop_len, kick.seconds = 0, 0.1
    drums = {k: (1, 20, 1.0) for k in (36, 38)}
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "unit_ticks": 30, "phase_ticks": 0}
    _, _, first = sg.convert_song(p, [_fake(), kick], {9: 0}, drums, cfg, dict(cfg))
    assert first.tracks_used == 4
    _, _, rep = sg.plan_tracks(p, [_fake(), kick], {9: 0}, drums, cfg, dict(cfg))
    assert rep.tracks_used == 2 and rep.borrow == "first" and sg.allocation_cost(rep) == 0


def test_delayed_notes_far_from_the_default_volume_snap_to_a_row(tmp_path):
    import mido
    # a quiet note 1 tick after a row (speed 3, 62.5 ms rows): EDx would start it at the
    # default volume (the song's median), far too loud; snapping moves it back one tick, Cxx in-cell.
    # The same note one tick after a same-part partner on the row (a flam) must stay delayed.
    tick = 0.0625 / 3
    def song(name, flam):
        ev = [mido.Message("program_change", channel=1, program=9),        # a loud note on a row sets
              mido.Message("note_on", channel=1, note=67, velocity=127),  # the default volume between
              mido.Message("note_off", channel=1, note=67, time=60)]      # the two
        if flam:                                                          # a partner on the row:
            ev.append(mido.Message("note_on", channel=1, note=64, velocity=100, time=60))  # moving back collapses
        ev.append(mido.Message("note_on", channel=1, note=60, velocity=20,
                               time=round((0.5 + tick) * 240) - (120 if flam else 60)))
        ev += [mido.Message("note_off", channel=1, note=60, time=120),
               mido.Message("note_off", channel=1, note=64, time=0)]
        return _song(tmp_path, ev, name)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "unit_ticks": 30, "phase_ticks": 0}
    for flam, snapped in ((False, 1), (True, 0)):
        p = song(f"{flam}.mid", flam)
        _, _, off = sg.convert_song(p, [_fake()], {9: 0}, {}, cfg, dict(cfg))
        _, _, on = sg.convert_song(p, [_fake()], {9: 0}, {}, cfg, {**cfg, "delay_snap_db": 4.0})
        assert on.vel_snapped == snapped
        assert on.vel_default == off.vel_default - snapped
        assert on.timing_err_ms >= off.timing_err_ms


def test_tempo_commands_stay_on_the_ideal_grid():
    ideal, speed, rows, tol = 97.56, 3, 2000, 0.002
    ch = sg.tempo_commands(ideal, speed, rows, lambda r: True, tol)
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
    assert sg.tempo_commands(98.0, 3, 100, lambda r: True, tol) == [(0, 98)]


def test_envelope_rows_average_tremolo_instead_of_aliasing_it():
    s = _fake()
    s.env_t = np.arange(0, 3, 0.01)
    s.env_g = 1 + 0.8 * np.sin(2 * np.pi * 7 * s.env_t)          # 7 Hz tremolo around 1
    rows = [sg.env_at(s, r * 0.154, 0.154) for r in range(1, 18)]  # MI153's 154 ms rows
    assert max(rows) - min(rows) < 0.3                             # point sampling: ~1.6


def test_ntp_only_sounds_split_out_of_bank_merges():
    spec = {"melodic": [{"slot": 2, "name": "Elec Organ", "program": 9, "covers": [9, 13], "key": 60},
                        {"slot": 3, "name": "Solo", "program": 20, "covers": [20], "key": 60}], "drum": []}
    conf = {"melodic": [{"slot": 101, "name": "Pipe Organ", "program": 13, "covers": [13], "key": 60},
                        {"slot": 102, "name": "Solo2", "program": 20, "covers": [20], "key": 60}]}
    out = nm.ntp_spec(spec, conf)
    assert {m["name"]: m["covers"] for m in out["melodic"]} == {"Elec Organ": [9], "Pipe Organ": [13], "Solo2": [20]}
    assert spec["melodic"][0]["covers"] == [9, 13]                  # shared bank spec untouched


def test_key_split_sounds_pick_their_register():
    spec = {"melodic": [{"slot": 101, "name": "Hi", "program": 13, "covers": [13], "key": 60, "keys": [52, 127]},
                        {"slot": 102, "name": "Lo", "program": 13, "covers": [13], "key": 41, "keys": [0, 51]},
                        {"slot": 1, "name": "Bass", "program": 65, "covers": [65], "key": 36}], "drum": []}
    assert nm.melodic_entry(spec, 13, 40)["name"] == "Lo" and nm.melodic_entry(spec, 13, 52)["name"] == "Hi"
    assert nm.melodic_entry(spec, 65, 90)["name"] == "Bass" and nm.melodic_entry(spec, 7, 60) is None
    programs = {13: [(52, 127, 0), (0, 51, 1)], 65: 2}
    assert [sg.sample_for(programs, 13, k) for k in (36, 51, 52, 90)] == [1, 1, 0, 0]
    assert sg.sample_for(programs, 65, 30) == 2 and sg.sample_for(programs, 9, 60) is None


def test_attack_rows_play_at_unit_gain():
    s = _fake()
    s.env_t, s.env_g = np.array([0.12, 1.0]), np.array([3.0, 1.0])   # loop is quieter than the attack's end
    assert sg.env_at(s, 0.05, 0.01, t_loop=0.12) == 1.0               # inside the attack: raw audio
    assert sg.env_at(s, 0.5, 0.01, t_loop=0.12) > 1.5                 # in the loop: the envelope
    assert sg.env_at(s, 0.2, 0.01, t_loop=0.24) == 1.0                # an octave down: attack lasts 2x


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


def test_key_split_falls_back_to_the_nearest_range_and_modes_filter_sounds():
    spec = {"melodic": [{"slot": 2, "name": "Elec Organ", "program": 9, "covers": [9, 13], "key": 60}], "drum": []}
    conf = {"melodic": [{"slot": 101, "name": "Hi", "program": 13, "covers": [13], "key": 60, "keys": [52, 127]},
                        {"slot": 102, "name": "Lo", "program": 13, "covers": [13], "key": 44, "keys": [40, 51]},
                        {"slot": 103, "name": "Lo2", "program": 13, "covers": [13], "key": 37, "keys": [0, 39],
                         "modes": ["per-song"]}]}
    shared = nm.ntp_spec(spec, conf, "shared")
    assert [m["name"] for m in shared["melodic"]] == ["Elec Organ", "Hi", "Lo"]
    assert nm.melodic_entry(shared, 13, 36)["name"] == "Lo"            # nearest range
    assert nm.melodic_entry(nm.ntp_spec(spec, conf, "per-song"), 13, 36)["name"] == "Lo2"
    assert sg.sample_for({13: [(52, 127, 0), (40, 51, 1)]}, 13, 30) == 1


def test_track_order_lets_the_ntp_converter_assign_every_oscillator():
    # the converter's first-fit: interleaving strands odd oscillators even within 31
    interleaved = [1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]          # 31 oscillators
    assert sg.ntp_oscillators(interleaved) is None
    assert sg.ntp_oscillators(sorted(interleaved, reverse=True)) is not None
    # convert_song emits melodic (2-osc) tracks first, so any song within budget fits
    import mido
    mf = mido.MidiFile(ticks_per_beat=120)
    t = mido.MidiTrack([mido.Message("program_change", channel=1, program=9)])
    for i in range(12):
        t += [mido.Message("note_on", channel=9, note=36, velocity=100, time=0 if i else 0),
              mido.Message("note_on", channel=1, note=60 + i, velocity=90, time=5),
              mido.Message("note_off", channel=9, note=36, time=5)]
    t += [mido.Message("note_off", channel=1, note=60 + i, time=1 if i == 0 else 0) for i in range(12)]
    mf.tracks.append(t)
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "x.mid"
    mf.save(p)
    kick = smp.ModSample("kick", np.zeros(1014), kind="drum", seconds=0.5)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    _, _, rep = sg.convert_song(p, [_fake(), kick], {9: 0}, {36: (1, 20, 1.0)}, cfg, dict(cfg))
    kinds = rep.track_kinds
    assert kinds == sorted(kinds, key=lambda k: k != "melodic")
    assert sg.ntp_oscillators([2 if k == "melodic" else 1 for k in kinds]) is not None


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


# --- one-shot detection -------------------------------------------------------

def _decay(f0, secs, db_per_s, sr=32000):
    t = np.arange(int(secs * sr)) / sr
    return np.sin(2 * np.pi * f0 * t) * 10 ** (-db_per_s * t / 20)


def test_floor_time_finds_the_8bit_floor():
    x = np.concatenate([np.zeros(100), _decay(660, 2.0, 150.0)])     # 150 dB/s: -38.5 dB at ~0.26 s
    t = smp.floor_time(x, 100, len(x))
    assert 0.2 < t < 0.3
    slow = np.concatenate([np.zeros(100), _decay(660, 2.0, 8.0)])     # bass-like: never gets there
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
    y = np.concatenate([np.zeros(100), _decay(660, 2.0, 150.0)])
    assert abs(smp.drum_end(y, 100, len(y)) - smp.floor_time(y, 100, len(y))) < 0.01


def _tables():
    return {ft: [int(round(smp.PAL / (8363 * 2 ** ((n - 24 + ft / 8) / 12)))) for n in range(smp.NOTES)]
            for ft in range(-8, 8)}


def test_one_shot_when_the_whole_sound_fits_the_attack_and_loop_budget():
    tables = _tables()
    # 0.21 s at ~9.6 kHz = 2 KB: fits the 10 pages of a 2040 B attack + 512 B loop
    shot = smp.one_shot_tuning(0.21, 9644.0, 10, tables, 76, 72, 84, 9000)
    assert shot is not None
    rate, nbytes, note, ft = shot
    assert nbytes <= 2048 - smp.STOPPER and rate <= 9644.0 and rate >= 9644.0 * 0.9
    assert abs(smp.PAL / tables[ft][note] - rate) < 1e-6                  # an exact table rate
    # a 1.6 s decay would need far more than the budget at any acceptable rate
    assert smp.one_shot_tuning(1.6, 16744.0, 10, tables, 48, 40, 60, 9000) is None


# --- raw-loop tremolo / buzz gate ------------------------------------------------

def _wobbly_loop(k=17, spc=60, depth_db=6.0, wobble_cycles=0.5):
    """k cycles whose level drifts through `wobble_cycles` of a slow wobble (a raw slice of a
    rotary organ shorter than its wobble period)."""
    t = np.arange(k * spc) / spc
    g = 10 ** (depth_db / 20 * 0.5 * np.sin(2 * np.pi * wobble_cycles * t / k))
    return np.sin(2 * np.pi * t) * g


def test_tremolo_metric_sees_a_level_drift_across_the_loop():
    assert smp.loop_tremolo_db(np.tile(np.sin(2 * np.pi * np.arange(60) / 60), 17), 17) < 0.01
    assert smp.loop_tremolo_db(_wobbly_loop(), 17) > 2.0


def test_raw_loop_gate():
    cfg = {"raw_loop_slow_hz": 2.0, "raw_loop_max_tremolo_db": 1.0, "raw_loop_max_buzz_db": -50.0}
    lp = _wobbly_loop()                                    # 1020 points
    assert not smp.raw_loop_ok(lp, 17, 15700.0, cfg)        # repeats at ~15 Hz: an added tremolo
    assert smp.raw_loop_ok(lp, 17, 1500.0, cfg)             # repeats at ~1.5 Hz: the sound's own wobble
    clean = np.tile(np.sin(2 * np.pi * np.arange(60) / 60), 17)
    assert smp.raw_loop_ok(clean, 17, 15700.0, cfg)         # fast but exactly periodic


def test_loop_problems_flags_a_tremolo_loop_in_a_sample_set():
    cfg = {"raw_loop_slow_hz": 2.0, "raw_loop_max_tremolo_db": 1.0, "raw_loop_max_buzz_db": -50.0}
    lp = _wobbly_loop()
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


# --- halting released loops (NTP applies a delayed cell's instrument volume at tick 0) ----------

def test_released_loops_are_halted_before_any_delayed_note(tmp_path):
    """NinjaTrackerPlus sets a cell's instrument default volume at tick 0 and delays only the
    note (EDx), so a looped note left running silently after C00 sounds again until the delayed
    note starts. Every release must end with the silent rest one-shot on that track."""
    import mido
    mf = mido.MidiFile(ticks_per_beat=120)
    t = mido.MidiTrack([mido.Message("program_change", channel=1, program=9, time=0)])
    tick = 0
    for i in range(24):                                           # jittered, detached notes
        at = 60 * i + (0, 11, 23, 5)[i % 4]
        t.append(mido.Message("note_on", channel=1, note=60 + i % 5, velocity=90, time=at - tick))
        t.append(mido.Message("note_off", channel=1, note=60 + i % 5, velocity=0, time=20))
        tick = at + 20
    mf.tracks.append(t)
    p = tmp_path / "1.mid"
    mf.save(p)
    rest = smp.ModSample("rest", np.zeros(248), kind="rest", base_note=24, key0=60, default_volume=0)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, _, rep = sg.convert_song(p, [_fake(), rest], {9: 0}, {}, cfg, dict(cfg))
    assert rep.halted > 0
    delayed_after_release = 0
    for cells in grid.cells:
        state = None                                              # "loop" running, "rest" = halted
        for row in sorted(cells):
            note, inst, eff, par = cells[row]
            if note and inst == 2:
                assert (eff, par) == (0xC, 0)                     # the rest carries C00
                state = "rest"
            elif note and inst == 1:
                if eff == 0xE and state is not None:
                    delayed_after_release += 1
                    assert state == "rest"                        # never a live silent loop
                state = "loop"
    assert delayed_after_release > 0


def test_drums_borrow_idle_melodic_tracks_when_the_budget_is_full(tmp_path):
    """Track types are fixed: early melodic tracks used to leave too few drum tracks, and drums
    were dropped while melodic tracks sat idle (MI167: 82 drops at 28/31 oscillators)."""
    import mido
    ev = [mido.Message("program_change", channel=1, program=9)]
    for k in range(60, 75):                                         # 15 held notes: 30 oscillators
        ev.append(mido.Message("note_on", channel=1, note=k, velocity=90, time=0))
    for k in range(60, 75):
        ev.append(mido.Message("note_off", channel=1, note=k, time=240 if k == 60 else 0))
    for i in range(8):                                              # then two overlapping drums at once
        ev += [mido.Message("note_on", channel=9, note=36, velocity=100, time=240 if i == 0 else 60),
               mido.Message("note_on", channel=9, note=38, velocity=100, time=0),
               mido.Message("note_off", channel=9, note=36, time=10), mido.Message("note_off", channel=9, note=38, time=0)]
    p = _song(tmp_path, ev)
    kick = smp.ModSample("kick", np.zeros(2038), kind="drum", seconds=0.2)
    snare = smp.ModSample("snare", np.zeros(2038), kind="drum", seconds=0.2)
    base = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    drums = {36: (1, 20, 1.0), 38: (2, 22, 1.0)}
    _, _, never = sg.convert_song(p, [_fake(), kick, snare], {9: 0}, drums, base, {**base, "drum_borrow": "never"})
    _, _, last = sg.convert_song(p, [_fake(), kick, snare], {9: 0}, drums, base, {**base, "drum_borrow": "last"})
    assert never.dropped > 0
    assert last.dropped == 0 and last.oscillators <= 31


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


def test_read_notes_refuses_tempo_changes(tmp_path):
    import mido
    mf = mido.MidiFile(ticks_per_beat=120)
    mf.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500_000, time=0),
                                     mido.Message("note_on", channel=1, note=60, velocity=90, time=0),
                                     mido.MetaMessage("set_tempo", tempo=400_000, time=120),
                                     mido.Message("note_off", channel=1, note=60, time=0)]))
    p = tmp_path / "t.mid"
    mf.save(p)
    with pytest.raises(ValueError, match="tempo changes"):
        sg.read_notes(p)
