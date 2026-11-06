"""GM MIDI -> MIDI Synth sequence conversion rules."""

import mido
import pytest

from migs.mid2seq import BankMap, _tempo_and_scale, convert

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
    assert offs == [36, 80]                              # 42 (vel-0 on = off) -> hi-hat zone 80; 76 dropped
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
