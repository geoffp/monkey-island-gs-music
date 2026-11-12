# SPDX-License-Identifier: GPL-3.0-or-later
"""Arranging a song onto NinjaTrackerPlus tracks (ntpsong): grid, timing, levels, track
allocation and steals, envelopes, the track plan."""


import numpy as np
import pytest

from migs import ntpsamples as smp
from migs import ntpsong as sg
from ntp_fixtures import fake_sample, midi_song


def test_grid_detection_on_jittered_16ths():
    rng = np.random.default_rng(1)
    onsets = [(37.0 * i + 5 + rng.uniform(-2, 2)) * sg.RO_TIMEBASE.tick_s for i in range(200)]
    unit, phase = sg.detect_grid(onsets)
    assert unit == pytest.approx(37.0, abs=0.3) and phase == pytest.approx(5, abs=1.5)


def test_grid_is_in_the_songs_own_ticks(tmp_path):
    """A song's time base comes from its MIDI file, not SCUMM RO's 120 PPQN: the jittered grid
    of test_grid_detection_on_jittered_16ths at 480 PPQN (100 BPM) is found at 4x the unit."""
    import mido
    mf = mido.MidiFile(ticks_per_beat=480)
    t = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=600_000, time=0)])
    rng = np.random.default_rng(1)
    at = [int(148 * i + 20 + rng.integers(-8, 9)) for i in range(200)]   # 37 ticks at 120 PPQN
    tick = 0
    for a in at:
        t += [mido.Message("note_on", channel=1, note=60, velocity=100, time=a - tick),
              mido.Message("note_off", channel=1, note=60, time=40)]
        tick = a + 40
    mf.tracks.append(t)
    mf.save(p := tmp_path / "480.mid")
    tb = sg.read_timebase(p)
    assert tb == sg.Timebase(480, 600_000 / 1e6 / 480)
    notes, _, _ = sg.read_notes(p)
    unit, _ = sg.detect_grid([n.t for n in notes], tb)
    assert unit == pytest.approx(148, abs=1.2)                     # 4 x 37
    assert sg.RO_TIMEBASE == sg.Timebase(120, 0.5 / 120)            # what MI1's songs use


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
    # the row step limit would make the rows lag the swell; levels are the targets
    # (this note's swell peaks on its release row: "start" plays that row at the peak itself)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "env_max_rise_db": 0,
           "release_rows": "start"}
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


def test_envelope_rows_rise_at_most_env_max_rise_db(tmp_path):
    """The DOC applies a volume at once: a beating contour (French Horn: 29 26 20 23 33 34 25)
    stepped up to 3.6 dB a row. Sustain rows rise at most env_max_rise_db (at least one step);
    falls follow the contour, so real decays keep their speed."""
    import mido
    ev = [mido.Message("program_change", channel=1, program=9),
          mido.Message("note_on", channel=1, note=60, velocity=100, time=0),
          mido.Message("note_off", channel=1, note=60, time=480)]
    p = midi_song(tmp_path, ev)
    s = fake_sample()
    t = np.linspace(0.1, 2.0, 191)
    s.env_t, s.env_g = t, 10 ** (6 * np.sin(2 * np.pi * t / 0.6) / 20)      # +-6 dB beating, 0.6 s

    def held_volumes(limit):
        cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "env_max_rise_db": limit}
        grid, _, rep = sg.convert_song(p, [s], {9: 0}, {}, cfg, dict(cfg))
        cells = next(c for c in grid.cells if any(v[0] for v in c.values()))
        r_on = min(r for r, c in cells.items() if c[0])
        r_off = r_on + round(2.0 / (rep.row_ms / 1000))
        return [c[3] for r, c in sorted(cells.items()) if c[2] == 0xC and r_on < r <= r_off + 1]

    def rises(v):
        return [(20 * np.log10(b / a), b - a) for a, b in zip(v[1:], v[2:]) if a and b]  # after the first row

    assert max(d for d, _ in rises(held_volumes(0))) > 2.5   # unlimited: the beating steps
    v = held_volumes(1.5)
    assert all(d <= 1.5 + 1e-9 or step == 1 for d, step in rises(v))
    assert min(d for d, _ in rises(v)) < -1.5                # falls are not limited
    assert max(v) == max(held_volumes(0))                    # at 29 ms rows a 0.6 s swing is still followed:
    #                                                          the limit removes jumps, not slow movement


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


def test_delayed_notes_near_a_row_snap_to_it_within_delay_snap_ms(tmp_path):
    """delay_snap_ms: a delayed note whose onset is within that many ms of its nearest row goes
    onto the row with its velocity in-cell, even when the default volume is close (under
    delay_snap_db). A note at exactly the default volume gains nothing and keeps its EDx."""
    import mido
    # 62.5 ms rows = 15 MIDI ticks, speed 3: a tracker tick is 5 MIDI ticks (20.8 ms)
    on_row = [(0, 100), (60, 100), (120, 100)]          # set the default volume to velocity 100's
    delayed = [(185, 90), (247, 90), (305, 100)]        # 20.8 ms, 29.2 ms (still 1 tick), 20.8 ms at the default
    ev, now = [mido.Message("program_change", channel=1, program=9)], 0
    for at, vel in on_row + delayed:
        ev += [mido.Message("note_on", channel=1, note=60, velocity=vel, time=at - now),
               mido.Message("note_off", channel=1, note=60, time=20)]
        now = at + 20
    p = midi_song(tmp_path, ev)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "unit_ticks": 30, "phase_ticks": 0,
           "delay_snap_db": 4.0}
    reps = {ms: sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, {**cfg, "delay_snap_ms": ms})[2]
            for ms in (0, 25, 35)}
    assert [reps[ms].snap_near for ms in (0, 25, 35)] == [0, 1, 2]
    assert [reps[ms].vel_default for ms in (0, 25, 35)] == [3, 2, 1]
    assert reps[0].vel_snapped == 0 and reps[35].vel_snapped == 2
    assert sorted(round(x, 1) for x in reps[0].default_row_ms) == [20.8, 20.8, 29.2]
    assert reps[35].timing_err_ms > reps[25].timing_err_ms > reps[0].timing_err_ms


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
    note (EDx). So a looped note left running silently after C00 sounds again until the delayed
    note starts: every release ends with the silent rest on that track. The player writes that
    volume to both oscillators only if the track's current instrument is 2-oscillator (else the
    new loop starts at a stale volume), so the rest is looped, and a melodic track whose first
    note is delayed gets the rest on the row before it."""
    import mido
    mf = mido.MidiFile(ticks_per_beat=120)
    t = mido.MidiTrack([mido.Message("program_change", channel=1, program=9, time=0)])
    tick = 0
    for i in range(24):                                           # jittered, detached notes
        at = 60 * i + (11, 0, 23, 5)[i % 4]                        # the first note delayed
        t.append(mido.Message("note_on", channel=1, note=60 + i % 5, velocity=90, time=at - tick))
        t.append(mido.Message("note_off", channel=1, note=60 + i % 5, velocity=0, time=20))
        tick = at + 20
    mf.tracks.append(t)
    p = tmp_path / "1.mid"
    mf.save(p)
    rest = smp.rest_sample()
    assert rest.loop_start and rest.loop_len and len(rest.ntp_blocks()) == 2   # header + loop: 2 oscillators
    assert not rest.data.any()
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, _, rep = sg.convert_song(p, [fake_sample(), rest], {9: 0}, {}, cfg, dict(cfg))
    assert rep.halted > 0
    delayed = 0
    for cells in grid.cells:
        state = None                                              # "loop" running, "rest" = halted
        for row in sorted(cells):
            note, inst, eff, par = cells[row]
            if note and inst == 2:
                assert eff != 0xC or par == 0                     # silent: C00 or no volume at all
                state = "rest"
            elif note and inst == 1:
                if eff == 0xE:
                    delayed += 1
                    assert state == "rest"                        # never a live loop, never the first
                state = "loop"
    assert delayed > 1


def test_one_oscillator_tracks_never_get_the_looped_rest(tmp_path):
    """The rest is a 2-oscillator instrument: on a 1-oscillator (one-shot) track it would make
    NTP's converter give the track a pair. Cut one-shots there end with C00 alone."""
    import mido
    ev = [mido.Message("program_change", channel=1, program=9)]
    for i in range(8):
        ev += [mido.Message("note_on", channel=1, note=60, velocity=90, time=0 if i == 0 else 100),
               mido.Message("note_off", channel=1, note=60, time=20)]
    p = midi_song(tmp_path, ev)
    shot = smp.ModSample("x", np.zeros(8000), kind="melodic", base_note=30, key0=60, seconds=1.0,
                         env_t=np.array([0.1, 1.0]), env_g=np.array([1.0, 0.5]), rel_g=np.array([1.0, 0.5, 0.0]))
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31, "melodic_one_shots": "any"}
    grid, _, rep = sg.convert_song(p, [shot, smp.rest_sample()], {9: 0}, {}, cfg, dict(cfg))
    assert set(rep.track_kinds) == {"drum"}
    cells = [c for track in grid.cells for c in track.values()]
    assert any(c[2:] == [0xC, 0] for c in cells)                  # cut: released to C00
    assert not any(c[1] == 2 for c in cells)


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


def test_a_song_that_changes_tempo_is_arranged_in_beats_and_its_mod_follows_the_tempo(tmp_path):
    """iMUSE songs change tempo (MI2: 4, Fate of Atlantis: 17). read_notes gives times at the
    first tempo (beats), so the grid stays on the beat; the MOD's tempo commands follow the
    map, and the song plays in time: here 16 beats at 120 BPM, then 16 at 150 BPM."""
    import mido
    tr = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500_000, time=0),
                         mido.Message("program_change", channel=1, program=9, time=0)])
    tick = 0
    for i in range(64):                                   # 8ths
        a = 60 * i
        if a == 16 * 120:
            tr.append(mido.MetaMessage("set_tempo", tempo=400_000, time=a - tick))
            tick = a
        tr += [mido.Message("note_on", channel=1, note=60 + i % 5, velocity=90, time=a - tick),
               mido.Message("note_off", channel=1, note=60 + i % 5, time=30)]
        tick = a + 30
    mf = mido.MidiFile(ticks_per_beat=120)
    mf.tracks.append(tr)
    mf.save(p := tmp_path / "t.mid")
    notes, _, _ = sg.read_notes(p)
    assert notes[32].t == pytest.approx(8.0)              # beat 16, at the first tempo
    assert [tuple(map(round, m, (6, 6))) for m in sg.read_tempo_map(p)] == [(8.0, 1.25)]
    cfg = {"rows_per_unit": 1, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, patterns, rep = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
    assert rep.tempo_map == 1 and rep.timing_err_ms < 1.0
    played = _play(grid, patterns, rows_out=4000)
    at = {r: t for t, r in played}
    note_rows = sorted(r for c in grid.cells for r, cell in c.items() if cell[0])
    real = lambda b: b * 0.5 if b <= 16 else 8.0 + (b - 16) * 0.4      # beat -> seconds
    lead = at[note_rows[0]]
    for i, r in enumerate(note_rows):
        assert at[r] - lead == pytest.approx(real(i / 2), abs=0.004)


def test_a_struck_key_sounds_once_as_on_the_mt32(tmp_path):
    """MT-32 single-assign (Munt Part::playPoly, stopNote; MI153's organ): striking a sounding
    key again ends the old note there, the next note-off ends the new one, and the stray
    note-off after it is ignored. Struck twice at the same tick, only the second is heard."""
    import mido
    on = lambda k, dt=0: mido.Message("note_on", channel=1, note=k, velocity=90, time=dt)
    off = lambda k, dt=0: mido.Message("note_off", channel=1, note=k, time=dt)
    p = midi_song(tmp_path, [on(60), on(60, 240), off(60, 240), off(60, 240),       # held, restruck at 1 s
                             on(64), on(64), off(64, 60), off(64, 60)])             # twice at 3 s
    notes, _, _ = sg.read_notes(p)
    assert [(n.key, n.t, n.off) for n in notes] == [(60, 0.0, 1.0), (60, 1.0, 2.0), (64, 3.0, 3.25)]


def _ghosts(grid, samples) -> int:
    """Delayed notes (EDx) with an instrument on a track whose last volume is above 0 and isn't
    the rest: NTP raises that old note to the new default volume at tick 0 (docs "Ghosts")."""
    n = 0
    for cells in grid.cells:
        level, rest = 0, True
        for row in sorted(cells):
            note, inst, eff, par = cells[row]
            if note and inst and eff == 0xE and par & 0xF and level > 0 and not rest:
                n += 1
            if note and inst:
                rest = samples[inst - 1].kind == "rest"
                level = samples[inst - 1].default_volume
            if eff == 0xC:
                level = par
    return n


def _ghost_song(tmp_path, b_at):
    """One 2-oscillator track: note A (ch 1) releases over 1 s from tick 240; note B (ch 2)
    starts at `b_at`, off the row grid (rows of 15 ticks, ticks of 5), so it is delayed."""
    import mido
    ev = [mido.Message("program_change", channel=ch, program=9) for ch in (1, 2)]
    ev += [mido.Message("note_on", channel=1, note=60, velocity=110),
           mido.Message("note_off", channel=1, note=60, time=240),
           mido.Message("note_on", channel=2, note=64, velocity=60, time=b_at - 240),
           mido.Message("note_off", channel=2, note=64, time=120)]
    p = midi_song(tmp_path, ev)
    s = fake_sample()
    s.rel_g = np.linspace(1.0, 0.0, 101)                               # 1 s release ramp
    return p, [s, smp.rest_sample()]


def test_halt_before_delayed_leaves_nothing_sounding_for_a_delayed_note(tmp_path):
    """A delayed note that takes a still-releasing track: NTP would raise the release to the new
    note's default volume for d ticks (MI160 on the IIGS: +24 to +35 dB for 50-67 ms). With
    halt_before_delayed the track is halted (rest + C00) on the row before."""
    p, samples = _ghost_song(tmp_path, 307)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 2, "unit_ticks": 30, "phase_ticks": 0,
           "halt_before_delayed": False}
    grid, _, rep = sg.convert_song(p, samples, {9: 0}, {}, cfg, dict(cfg))
    assert rep.delayed == 1 and _ghosts(grid, samples) == 1                   # the option off
    on = {k: v for k, v in cfg.items() if k != "halt_before_delayed"}           # on by default
    grid, _, rep = sg.convert_song(p, samples, {9: 0}, {}, on, dict(on))
    assert _ghosts(grid, samples) == 0 and rep.ghost_halts == 1 and rep.ghost_moves == 0 and rep.delayed == 1
    cells = grid.cells[0]
    r = next(row for row, c in cells.items() if c[2] == 0xE)
    assert cells[r - 1][1] == 2 and cells[r - 1][2:] == [0xC, 0]                # rest + C00 on the row before


def test_halt_before_delayed_moves_the_note_when_the_row_before_starts_one(tmp_path):
    """No free row to halt on (a note starts there): the delayed note goes onto a row instead."""
    import mido
    ev = [mido.Message("program_change", channel=ch, program=9) for ch in (1, 2)]
    ev += [mido.Message("note_on", channel=1, note=60, velocity=110, time=0),
           mido.Message("note_off", channel=1, note=60, time=285),             # A: rows 0-19, then releases
           mido.Message("note_on", channel=1, note=62, velocity=110, time=0),  # row 19: A2 on the same track
           mido.Message("note_off", channel=1, note=62, time=5),
           mido.Message("note_on", channel=2, note=64, velocity=60, time=17),  # tick 307: row 20, delayed
           mido.Message("note_off", channel=2, note=64, time=120)]
    p = midi_song(tmp_path, ev)
    s = fake_sample()
    s.rel_g = np.linspace(1.0, 0.0, 101)
    samples = [s, smp.rest_sample()]
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 2, "unit_ticks": 30, "phase_ticks": 0,
           "halt_before_delayed": True}
    grid, _, rep = sg.convert_song(p, samples, {9: 0}, {}, cfg, dict(cfg))
    assert rep.ghost_moves == 1 and rep.ghost_halts == 0 and _ghosts(grid, samples) == 0


# --- song loops (the game's FF: play again from the start) ------------------------------

def _loop_song(tmp_path, loop_end, held=False, late=None, first=0):
    """Notes on a 30-tick grid; markers as the extractor writes them for an RO track ending in
    FF. `held`: the last note sounds to the end (never released). `late`: a note at that tick."""
    import mido
    ev = [mido.MetaMessage("marker", text="loopStart", time=0),
          mido.Message("program_change", channel=1, program=9, time=0)]
    at = [(first + 60 * i, 60 + i % 5) for i in range(16 - first // 60)]
    if late is not None:
        at.append((late, 72))
    tick = 0
    for i, (a, k) in enumerate(at):
        ev.append(mido.Message("note_on", channel=1, note=k, velocity=90, time=a - tick))
        tick = a
        if not (held and i == len(at) - 1):
            ev.append(mido.Message("note_off", channel=1, note=k, velocity=0, time=20))
            tick += 20
    ev += [mido.MetaMessage("marker", text="loopEnd", time=loop_end - tick)]
    return midi_song(tmp_path, ev)


def _play(grid, patterns, rows_out=2000):
    """(time s, grid row) of every row a MOD player plays, in order: Fxx (speed < 32, else
    tempo) applies from its own row, Bxx/Dxx after it."""
    cells = lambda r: [c.get(r, sg.CELL_EMPTY) for c in grid.cells]
    speed, tempo, pos, row, t, out = 6, 125, 0, 0, 0.0, []
    while len(out) < rows_out and pos < patterns:
        r = pos * sg.ROWS + row
        jump = None
        for c in cells(r):
            if c[2] == sg.FX_SPEED:
                speed, tempo = (c[3], tempo) if c[3] < 32 else (speed, c[3])
            elif c[2] == sg.FX_JUMP:
                jump = (c[3], 0)
            elif c[2] == sg.FX_BREAK:
                jump = (pos + 1, c[3])
        out.append((t, r))
        t += speed * 2.5 / tempo
        pos, row = jump if jump else ((pos, row + 1) if row + 1 < sg.ROWS else (pos + 1, 0))
    return out


def test_a_looping_song_repeats_exactly_as_long_as_the_game_loop(tmp_path):
    """The pass is rows 1..end; Bxx on its last row jumps back to row 1, and the lead-in row 0
    is alone in pattern 0 (D00), so it plays once. The fitted grid doesn't divide the loop
    (16 units of 60 ticks + 17), so the last row gets its own tempo, and row 1 restores the
    start tempo: every pass lasts the loop, to within a tempo step."""
    loop_ticks = 16 * 60 + 17
    p = _loop_song(tmp_path, loop_ticks)
    assert sg.read_loop(p) == pytest.approx((0.0, loop_ticks * sg.RO_TIMEBASE.tick_s))
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, patterns, rep = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
    assert rep.loop_rows > 0 and abs(rep.loop_err_ms) < 3 and rep.loop_fit_rows >= 1
    assert patterns == 1 + -(-rep.loop_rows // sg.ROWS)
    assert any(c.get(0, sg.CELL_EMPTY)[2:] == [sg.FX_BREAK, 0] for c in grid.cells)
    assert all(r == 0 or r >= sg.ROWS for c in grid.cells for r in c)          # pattern 0: row 0 only
    jumps = [(r, c[r][3]) for c in grid.cells for r in c if c[r][2] == sg.FX_JUMP]
    assert jumps == [(sg.ROWS + rep.loop_rows - 1, 1)]
    played = _play(grid, patterns)
    starts = [t for t, r in played if r == sg.ROWS]                            # pass row 1
    assert len(starts) >= 3
    for a, b in zip(starts, starts[1:]):
        assert b - a == pytest.approx(loop_ticks * sg.RO_TIMEBASE.tick_s, abs=0.003)


def test_a_song_that_plays_once_ends_after_its_last_row(tmp_path):
    import mido
    p = midi_song(tmp_path, [mido.Message("program_change", channel=1, program=9, time=0),
                             mido.Message("note_on", channel=1, note=60, velocity=90, time=0),
                             mido.Message("note_off", channel=1, note=60, velocity=0, time=240)])
    assert sg.read_loop(p) is None
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, _, rep = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
    assert rep.loop_rows == 0
    assert not any(c[r][2] == sg.FX_JUMP for c in grid.cells for r in c)
    breaks = [r for c in grid.cells for r in c if c[r][2] == sg.FX_BREAK]
    assert breaks == [rep.rows - 1] and rep.rows >= max(r for c in grid.cells for r in c) + 1


def test_releases_that_cross_the_loop_sound_into_the_next_pass(tmp_path):
    """The game's loop stops the notes still sounding (MidiParser jumpToTick), and the MT-32
    plays their release into the next pass. A note held to the loop's end releases there:
    its release cells move to the start of the pass, before the track's first note, and the
    release still ends silent (the rest), so nothing rings on into the next pass."""
    p = _loop_song(tmp_path, 16 * 60, held=True, first=120)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    samples = [fake_sample(), smp.rest_sample()]
    grid, patterns, rep = sg.convert_song(p, samples, {9: 0}, {}, cfg, dict(cfg))
    assert rep.loop_wrapped > 0 and rep.loop_cut == 0
    last = sg.ROWS + rep.loop_rows - 1
    assert all(r <= last for c in grid.cells for r in c)
    # the held note's track: its wrapped release comes before its first note, ending in the rest
    for c in grid.cells:
        first = min((r for r in c if c[r][0] and c[r][1] == 1), default=None)
        early = [c[r] for r in sorted(c) if 0 < r and first is not None and r < first
                 and (c[r][0] or c[r][2] == sg.FX_VOLUME)]
        if early:
            assert all(cell[0] == 0 or cell[1] == 2 for cell in early)            # releases, or the rest
            assert early[-1][1] == 2 and early[-1][2:] == [sg.FX_VOLUME, 0]
            break
    else:
        raise AssertionError("no release moved into the next pass")


def test_a_note_the_grid_puts_past_the_loop_end_plays_on_the_last_row(tmp_path):
    loop_ticks = 16 * 60
    p = _loop_song(tmp_path, loop_ticks, held=True, late=loop_ticks - 4)
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, _, rep = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
    assert rep.loop_late == 1 and rep.loop_cut == 0 and rep.dropped == 0
    last = sg.ROWS + rep.loop_rows - 1
    assert any(c.get(last, sg.CELL_EMPTY)[0] for c in grid.cells)


def _intro_song(tmp_path, start, loop_ticks):
    import mido
    ev, tick = [mido.Message("program_change", channel=1, program=9, time=0)], 0
    for i in range(24):
        a = 60 * i
        ev += [mido.Message("note_on", channel=1, note=60 + i % 5, velocity=90, time=a - tick),
               mido.Message("note_off", channel=1, note=60 + i % 5, velocity=0, time=20)]
        tick = a + 20
    marks = [mido.MetaMessage("marker", text="loopStart", time=start),
             mido.MetaMessage("marker", text="loopEnd", time=loop_ticks)]
    mf = mido.MidiFile(ticks_per_beat=120)
    mf.tracks += [mido.MidiTrack(marks), mido.MidiTrack(ev)]
    mf.save(p := tmp_path / "intro.mid")
    return p


def test_a_loop_after_an_intro_jumps_back_to_a_pattern_start(tmp_path):
    """iMUSE songs loop to a point inside the song. NTP can't jump to a row (B and D share one
    variable), so the intro gets patterns of its own (its last one ended by D00) and the pass
    starts on a fresh pattern, which the Bxx jumps back to. The intro plays once."""
    start, loop_ticks = 240 + 7, 16 * 60 + 17
    p = _intro_song(tmp_path, start, loop_ticks)
    tb = sg.RO_TIMEBASE.tick_s
    assert sg.read_loop(p) == pytest.approx((start * tb, (start + loop_ticks) * tb))
    cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
    grid, patterns, rep = sg.convert_song(p, [fake_sample()], {9: 0}, {}, cfg, dict(cfg))
    assert rep.loop_start_row > 1 and abs(rep.loop_err_ms) < 3
    jumps = [c[r][3] for c in grid.cells for r in c if c[r][2] == sg.FX_JUMP]
    intro_patterns = -(-(rep.loop_start_row - 1) // sg.ROWS)
    assert jumps == [1 + intro_patterns] and patterns == 1 + intro_patterns + -(-rep.loop_rows // sg.ROWS)
    played = _play(grid, patterns, rows_out=4000)
    assert sum(r == sg.ROWS for _, r in played) == 1                 # the intro plays once
    starts = [t for t, r in played if r == jumps[0] * sg.ROWS]
    assert len(starts) >= 3
    for a, b in zip(starts, starts[1:]):
        assert b - a == pytest.approx(loop_ticks * tb, abs=0.003)
    # the lead-in, then the intro: the pass starts at the loop's start, to within half a row
    assert starts[0] - played[1][0] == pytest.approx(start * tb, abs=0.0625 + 0.001)


def test_short_notes_halve_the_rows_until_they_fit():
    """row_fit = "short-notes" (trial): a note lasts at least a row, so rows halve while longer
    than the song's short melodic notes (10th percentile), down to 30 ms. MI2's 101: 221 ms grid
    unit (an 8th at 135 BPM), 35-50 ms organ notes."""
    N = lambda t, d, ch=1: sg.Note(t, t + d, 60, 90, ch)
    notes = [N(0.25 * i, 0.04) for i in range(40)] + [N(0.25 * i + 0.1, 0.3, 9) for i in range(40)]
    cfg = {"row_fit": "short-notes"}
    assert sg._fit_rows(notes, 0.221, 2, {}, {}) == 2                     # default: as configured
    assert sg._fit_rows(notes, 0.221, 2, cfg, {}) == 4                    # 55 ms; 28 ms would pass the floor
    assert sg._fit_rows(notes, 0.221, 2, {**cfg, "row_fit_min_ms": 20}, {}) == 8
    long_notes = [N(0.25 * i, 0.2) for i in range(40)]
    assert sg._fit_rows(long_notes, 0.221, 2, cfg, {}) == 2                # already short enough rows
    assert sg._fit_rows(notes, 0.221, 2, cfg, {"row_fit": "grid"}) == 2    # a song can opt out
    # as the build calls it: the song's config arrives with the defaults (rows_per_unit) merged in
    full = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "row_fit": "short-notes"}
    song = {**full, "unit_ticks": 0.221 / sg.RO_TIMEBASE.tick_s, "phase_ticks": 0}
    assert sg._timing(notes, full, song).row_s == pytest.approx(0.221 / 4)
    assert sg._timing(notes, full, {**song, "row_fit": "grid"}).row_s == pytest.approx(0.221 / 2)


def test_mean_release_rows_play_the_release_averaged_over_the_row(tmp_path):
    """A row holds one volume. release_rows = "start" (the rule before 2026-11-12) gives the first release row the
    release's gain at its first instant, so a release that falls 20 dB in 10 ms (Elec Org 2)
    holds full volume for a row; "mean" (the default) gives it the RMS gain over the row."""
    import mido
    p = midi_song(tmp_path, [mido.Message("program_change", channel=1, program=9),
                             *[m for i in range(4) for m in (
                                 mido.Message("note_on", channel=1, note=60, velocity=100, time=0 if i == 0 else 360),
                                 mido.Message("note_off", channel=1, note=60, time=120))]])

    def release_row(mode):
        s = fake_sample()
        s.rel_g = np.array([1.0, 0.05, 0.0])                     # per 10 ms: gone in 20 ms
        cfg = {"rows_per_unit": 2, "speed": 3, "quantize": 0.0, "oscillators": 31}
        if mode:
            cfg["release_rows"] = mode
        song = {**cfg, "unit_ticks": 60, "phase_ticks": 0}       # 125 ms rows: a note is 4 rows
        grid, _, rep = sg.convert_song(p, [s], {9: 0}, {}, cfg, song)
        assert rep.row_ms == pytest.approx(125)
        cells = next(c for c in grid.cells if any(v[0] for v in c.values()))
        on = min(r for r in cells if cells[r][0])
        off = min(r for r in cells if r >= on + 4 and cells[r][2] == 0xC)
        return cells[on][3], cells[off][3]
    onset, start = release_row("start")
    _, mean = release_row("mean")
    _, default = release_row(None)
    assert start > 0.6 * onset and 0 < mean < 0.3 * start and default == mean
