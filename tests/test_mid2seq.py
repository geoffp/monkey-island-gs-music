# SPDX-License-Identifier: GPL-3.0-or-later
"""GM MIDI -> MIDI Synth sequence conversion rules."""

import mido
import pytest

from migs.mid2seq import BankMap, _gate_drums, _tempo_and_scale, convert


BANK = BankMap(name="T", file="T.BNK", rhythm_slot=0, fallback_slot=2,
               programs={75: 7, 32: 1}, drums={36: 36, 42: 80})


def song(tpq=120, tempo=500_000):
    mf = mido.MidiFile(type=1, ticks_per_beat=tpq)
    cond = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=tempo, time=0)])
    a = mido.MidiTrack([
        mido.Message("program_change", channel=1, program=75, time=0),
        mido.Message("control_change", channel=1, control=7, value=100, time=0),
        mido.Message("control_change", channel=1, control=10, value=20, time=0),   # pan: dropped
        mido.Message("note_on", channel=1, note=72, velocity=90, time=0),
        mido.Message("note_off", channel=1, note=72, velocity=0, time=120),
    ])
    b = mido.MidiTrack([
        mido.Message("program_change", channel=8, program=0, time=0),             # no notes
        mido.Message("note_on", channel=9, note=36, velocity=100, time=60),
        mido.Message("note_on", channel=9, note=76, velocity=100, time=0),        # unmapped drum
        mido.Message("note_on", channel=9, note=42, velocity=0, time=0),
        mido.Message("note_off", channel=9, note=36, velocity=0, time=60),
        mido.Message("note_off", channel=9, note=76, velocity=0, time=0),
    ])
    mf.tracks += [cond, a, b]
    return mf


def test_mi1_timing_is_kept_exact_with_tempo_75():
    assert _tempo_and_scale(120, 500_000) == (75, 1.0)
    assert _tempo_and_scale(96, 500_000) == (60, 1.0)
    tempo, scale = _tempo_and_scale(480, 500_000)      # no exact fit: rescale ticks
    assert tempo == 60 and scale == pytest.approx(96 / 480)


def test_channels_become_instrument_slots_and_unsupported_data_is_dropped():
    seq, rep = convert(song(), BANK)
    assert rep.channel_slots == {1: 7, 9: 0}             # Pan Flute -> slot 7; rhythm -> slot 0
    statuses = {i.status for i in seq.items}
    assert statuses <= {0x97, 0x87, 0xB7, 0x90, 0x80}    # no program change, no ch 9 track
    assert not any(i.status & 0xF0 == 0xB0 and i.data[0] == 10 for i in seq.items)
    assert rep.dropped["CC10"] == 1 and rep.dropped["unmapped drum key 76"] == 2
    assert seq.tempo == 75 and seq.channel_volume[7] == 100


def test_drum_keys_are_remapped_onto_kit_zones():
    seq, _ = convert(song(), BANK)
    offs = sorted(i.data[0] for i in seq.items if i.status == 0x80)
    assert offs == [36]          # gated hit; the orphan vel-0 "off" for 42 and key 76 are dropped
    ons = [(i.time, i.data[0]) for i in seq.items if i.status == 0x90]
    assert ons == [(60, 36)]


def test_items_are_time_ordered_with_one_track_per_source_channel():
    seq, _ = convert(song(), BANK)
    times = [i.time for i in seq.items]
    assert times == sorted(times)
    assert {i.track for i in seq.items} == {0, 1}
    assert seq.track_play[:2] == [0xFFFF, 0xFFFF] and seq.track_play[2] == 0
    seq.to_bytes()                                       # valid file


def test_peak_voices_are_reported_not_reduced():
    mf = mido.MidiFile(ticks_per_beat=120)
    t = mido.MidiTrack([mido.Message("note_on", channel=0, note=n, velocity=80, time=0) for n in range(60, 70)])
    mf.tracks.append(t)
    seq, rep = convert(mf, BANK)
    assert rep.peak_voices == 10 and rep.notes == 10
    assert sum(1 for i in seq.items if i.status & 0xF0 == 0x90) == 10
    assert any("steal" in w for w in rep.warnings)


def test_drum_notes_are_gated_and_rehits_not_cut():
    on = lambda t, n: (t, 0, mido.Message("note_on", channel=9, note=n, velocity=100))
    off = lambda t, n: (t, 0, mido.Message("note_off", channel=9, note=n))
    ev = [on(0, 36), on(5, 36), off(400, 36), off(401, 36), on(100, 42), off(1000, 42),
          (0, 0, mido.Message("note_on", channel=1, note=60, velocity=90))]
    out = _gate_drums(sorted(ev, key=lambda e: e[0]), gate=12)
    drums = [(t, m.type, m.note) for t, _, m in out if m.channel == 9]
    assert drums == [(0, "note_on", 36), (5, "note_off", 36), (5, "note_on", 36),
                     (17, "note_off", 36), (100, "note_on", 42), (112, "note_off", 42)]
    assert any(m.channel == 1 for _, _, m in out)                       # melodic untouched


def test_sixteen_channels_leave_no_room_for_the_title_and_do_not_crash():
    mf = mido.MidiFile(ticks_per_beat=120)
    mf.tracks.append(mido.MidiTrack(
        [mido.Message("note_on", channel=c, note=60, velocity=80, time=0) for c in range(16)]))
    seq, rep = convert(mf, BANK, title="MI1 999")
    assert len(rep.channel_slots) == 16 and "MI1 999" not in seq.track_names
    seq.to_bytes()
