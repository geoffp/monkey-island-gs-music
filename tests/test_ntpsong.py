"""Arranging a song onto NinjaTrackerPlus tracks (ntpsong): grid, timing, levels, track
allocation and steals, envelopes, the track plan."""

from pathlib import Path

import numpy as np
import pytest

from migs import audio
from migs import ntpsamples as smp
from migs import ntpsong as sg
from ntp_fixtures import fake_sample, midi_song


def test_grid_detection_on_jittered_16ths():
    rng = np.random.default_rng(1)
    onsets = [(37.0 * i + 5 + rng.uniform(-2, 2)) * sg.MI1_TICK for i in range(200)]
    unit, phase = sg.detect_grid(onsets)
    assert unit == pytest.approx(37.0, abs=0.3) and phase == pytest.approx(5, abs=1.5)


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
    grid, patterns, rep = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
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


def test_channel_volume_and_swell_set_levels(tmp_path):
    import mido
    ev = [mido.Message("program_change", channel=1, program=9), mido.Message("program_change", channel=2, program=9),
          mido.Message("control_change", channel=1, control=7, value=100),
          mido.Message("control_change", channel=2, control=7, value=64)]
    for ch in (1, 2):
        ev += [mido.Message("note_on", channel=ch, note=60, velocity=100, time=0),
               mido.Message("note_off", channel=ch, note=60, time=240)]
    p = midi_song(tmp_path, ev)
    notes, _, _ = sg.read_notes(p)
    assert sorted(n.volume for n in notes) == [64, 100]
    cc7 = (np.array([0.0, 64, 100, 127]), np.array([0.0, 0.35, 1.0, 1.8]))
    s = fake_sample()
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
    p = midi_song(tmp_path, ev)
    s = fake_sample()
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
    p = midi_song(tmp_path, ev)
    s = fake_sample()
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
    p = midi_song(tmp_path, ev)
    lo = smp.ModSample("lo", np.zeros(512), loop_start=0, loop_len=512, base_note=30, key0=60,
                      env_t=np.array([0.0, 1.0]), env_g=np.array([1.0, 0.5]), rel_g=np.array([1.0, 0.0]))
    assert lo.ntp_blocks() == [512]
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 2, "unit_ticks": 30, "phase_ticks": 0}
    _, _, rep = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
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
    p = midi_song(tmp_path, ev)
    kick = fake_sample(kind="drum")
    kick.loop_len, kick.seconds = 0, 0.1
    drums = {k: (1, 20, 1.0) for k in (36, 38, 42)}
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 6, "unit_ticks": 30, "phase_ticks": 0}
    _, _, first = sg.convert_song(p, [fake_sample(), kick], {9: 0}, drums, cfg, dict(cfg))
    assert first.dropped == 2 and first.melodic_cap is None
    _, _, rep = sg.plan_tracks(p, [fake_sample(), kick], {9: 0}, drums, cfg, dict(cfg))
    assert rep.dropped == 0 and rep.melodic_cap == 3 and rep.track_kinds == ["melodic"] * 3
    _, _, off = sg.plan_tracks(p, [fake_sample(), kick], {9: 0}, drums, cfg, {**cfg, "track_plan": False})
    assert off.dropped == 2


def test_melodic_one_shots_may_use_one_oscillator_tracks(tmp_path):
    import mido
    ev = [mido.Message("program_change", channel=1, program=9),
          mido.Message("note_on", channel=1, note=60, velocity=100),
          mido.Message("note_off", channel=1, note=60, time=120)]
    p = midi_song(tmp_path, ev)
    shot = fake_sample()
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
    p = midi_song(tmp_path, ev)
    kick = fake_sample(kind="drum")
    kick.loop_len, kick.seconds = 0, 0.1
    drums = {k: (1, 20, 1.0) for k in (36, 38)}
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "unit_ticks": 30, "phase_ticks": 0}
    _, _, first = sg.convert_song(p, [fake_sample(), kick], {9: 0}, drums, cfg, dict(cfg))
    assert first.tracks_used == 4
    _, _, rep = sg.plan_tracks(p, [fake_sample(), kick], {9: 0}, drums, cfg, dict(cfg))
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
        return midi_song(tmp_path, ev, name)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "unit_ticks": 30, "phase_ticks": 0}
    for flam, snapped in ((False, 1), (True, 0)):
        p = song(f"{flam}.mid", flam)
        _, _, off = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
        _, _, on = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, {**cfg, "delay_snap_db": 4.0})
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
    s = fake_sample()
    s.env_t = np.arange(0, 3, 0.01)
    s.env_g = 1 + 0.8 * np.sin(2 * np.pi * 7 * s.env_t)          # 7 Hz tremolo around 1
    rows = [sg.env_at(s, r * 0.154, 0.154) for r in range(1, 18)]  # MI153's 154 ms rows
    assert max(rows) - min(rows) < 0.3                             # point sampling: ~1.6


def test_attack_rows_play_at_unit_gain():
    s = fake_sample()
    s.env_t, s.env_g = np.array([0.12, 1.0]), np.array([3.0, 1.0])   # loop is quieter than the attack's end
    assert sg.env_at(s, 0.05, 0.01, t_loop=0.12) == 1.0               # inside the attack: raw audio
    assert sg.env_at(s, 0.5, 0.01, t_loop=0.12) > 1.5                 # in the loop: the envelope
    assert sg.env_at(s, 0.2, 0.01, t_loop=0.24) == 1.0                # an octave down: attack lasts 2x


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
    _, _, rep = sg.convert_song(p, [fake_sample(), kick], {9: 0}, {36: (1, 20, 1.0)}, cfg, dict(cfg))
    kinds = rep.track_kinds
    assert kinds == sorted(kinds, key=lambda k: k != "melodic")
    assert sg.ntp_oscillators([2 if k == "melodic" else 1 for k in kinds]) is not None


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
    grid, _, rep = sg.convert_song(p, [fake_sample(), rest], {9: 0}, {}, cfg, dict(cfg))
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
    p = midi_song(tmp_path, ev)
    kick = smp.ModSample("kick", np.zeros(2038), kind="drum", seconds=0.2)
    snare = smp.ModSample("snare", np.zeros(2038), kind="drum", seconds=0.2)
    base = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    drums = {36: (1, 20, 1.0), 38: (2, 22, 1.0)}
    _, _, never = sg.convert_song(p, [fake_sample(), kick, snare], {9: 0}, drums, base, {**base, "drum_borrow": "never"})
    _, _, last = sg.convert_song(p, [fake_sample(), kick, snare], {9: 0}, drums, base, {**base, "drum_borrow": "last"})
    assert never.dropped > 0
    assert last.dropped == 0 and last.oscillators <= 31


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
