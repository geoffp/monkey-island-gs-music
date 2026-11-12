# SPDX-License-Identifier: GPL-3.0-or-later
"""iMUSE MT-32 songs (SCUMM v5) played into one event stream (migs.imuse), on synthetic
MIDI: parts, hooks, jumps and loops, custom timbres. Rules: docs/imuse-format.md."""

import struct

import mido
import pytest

from migs import imuse
from migs.ro import Kind

B = imuse.TICKS_PER_BEAT


def enc(*bs: int) -> list[int]:
    """iMUSE SysEx payload: each byte as two nibbles."""
    return [n for b in bs for n in ((b >> 4) & 0xF, b & 0xF)]


def alloc(ch, on=1, vol=127, pan=0, transpose=0, program=0, percussion=False):
    """7D 00: allocate part `ch` (sysex_scumm.cpp case 0)."""
    tb = 0x80 if percussion else transpose & 0xFF
    return mido.Message("sysex", data=[0x7D, 0x00, ch, *enc(on, 0, vol, pan & 0xFF, tb, 0, 2, program)])


def jump(hook, track, beat, tick):
    return mido.Message("sysex", data=[0x7D, 0x30, 0, *enc(hook, track >> 8, track & 0xFF, beat >> 8, beat & 0xFF,
                                                             tick >> 8, tick & 0xFF)])


def song(*tracks, ppqn=B) -> bytes:
    """A format-2 SMF with an MDhd header, as a ROL block holds it."""
    mf = mido.MidiFile(type=2, ticks_per_beat=ppqn)
    for evs in tracks:
        trk, last = mido.MidiTrack(), 0
        for t, m in sorted(evs, key=lambda e: e[0]):         # stable: same-tick order kept
            trk.append(m.copy(time=t - last))
            last = t
        mf.tracks.append(trk)
    import io
    buf = io.BytesIO()
    mf.save(file=buf)
    return b"MDhd" + struct.pack(">I", 8) + bytes(8) + buf.getvalue()


def note(t, ch, key, length=100, vel=100):
    return [(t, mido.Message("note_on", channel=ch, note=key, velocity=vel)),
            (t + length, mido.Message("note_off", channel=ch, note=key))]


def events(track, kind):
    return [(e.tick, e.channel, e.a, e.b) for e in track.events if e.kind == kind]


def test_a_part_allocation_sets_volume_pan_and_program_and_transposes_its_notes():
    rol = song([(0, alloc(0, vol=90, pan=-64 + 10, transpose=3, program=81)), *note(10, 0, 60)])
    tr, _ = imuse.read(rol, {})
    ch = 1                                           # channel 0 is moved onto MT-32 part 1 (MIDI channel 2)
    assert (0, ch, 7, 90) in events(tr, Kind.CC) and (0, ch, 10, 10) in events(tr, Kind.CC)
    assert events(tr, Kind.PROGRAM) == [(0, ch, 81, 0)]
    assert events(tr, Kind.NOTE_ON) == [(10, ch, 63, 100)]
    assert not tr.loops and tr.terminated


def test_percussion_parts_play_on_the_rhythm_channel_with_their_own_volume():
    rol = song([(0, alloc(12, vol=70, percussion=True)), (0, alloc(9, vol=110, percussion=True)),
                *note(10, 12, 36), *note(20, 9, 38)])
    tr, _ = imuse.read(rol, {})
    assert [(t, c, k) for t, c, k, _ in events(tr, Kind.NOTE_ON)] == [(10, 9, 36), (20, 9, 38)]
    assert [(t, v) for t, c, n, v in events(tr, Kind.CC) if c == 9 and n == 7] == [(10, 70), (20, 110)]


def test_a_jump_back_into_what_has_played_is_the_loop_and_stops_the_notes():
    """An unconditional jump (hook 0) forward skips; one back into played material is the loop
    (loop_start at the target's output tick). A jump with a hook id waits for a script: ignored."""
    rol = song([*note(0, 1, 60, 50), (B, jump(0, 0, 3, 0)),                 # skip beats 2-3
                *note(B + 10, 1, 61), (2 * B, jump(5, 0, 1, 0)),           # never taken
                *note(2 * B + 10, 1, 62, 2000), (4 * B, jump(0, 0, 4, 0)), *note(4 * B + 10, 1, 63)])
    tr, ignored = imuse.read(rol, {})
    keys = [k for _, _, k, _ in events(tr, Kind.NOTE_ON)]
    assert keys == [60, 62] and ignored == {"jump (script)": 1}
    # out ticks: beat 1 (0..B) then from file tick 2B at out B; the loop jumps at file 4B (out 3B) to 3B (out 2B)
    assert tr.loops and tr.loop_start == 2 * B and tr.end_tick == 3 * B
    offs = [(t, k) for t, _, k, _ in events(tr, Kind.NOTE_OFF)]
    assert (3 * B, 62) in offs                                              # held into the jump: stopped there


def test_a_long_counted_loop_is_the_songs_loop():
    """7D 50: repeat beats 2..4 `count` times (Player::onTimer); MI2 counts 100 or 1000."""
    w = lambda v: (v >> 8, v & 0xFF)
    counted = mido.Message("sysex", data=[0x7D, 0x50, 0, *enc(*w(1000), *w(2), *w(0), *w(4), *w(0))])
    rol = song([(0, counted), *note(10, 1, 60), *note(B + 10, 1, 62), *note(3 * B + 10, 1, 64), *note(5 * B, 1, 65)])
    tr, _ = imuse.read(rol, {})
    assert tr.loops and tr.loop_start == B and tr.end_tick == 3 * B
    assert [k for _, _, k, _ in events(tr, Kind.NOTE_ON)] == [60, 62]


def test_unconditional_part_hooks_switch_parts_on_and_off():
    off = mido.Message("sysex", data=[0x7D, 0x32, 1, *enc(0, 0)])           # part 1 off, hook 0
    on_script = mido.Message("sysex", data=[0x7D, 0x32, 1, *enc(7, 1)])     # hook 7: waits for a script
    rol = song([*note(0, 1, 60), (200, off), *note(300, 1, 62), (400, on_script), *note(500, 1, 64)])
    tr, _ = imuse.read(rol, {})
    assert [k for _, _, k, _ in events(tr, Kind.NOTE_ON)] == [60]


def test_a_roland_timbre_upload_becomes_the_parts_timbre():
    data = b"ReggaeBass" + bytes(i % 128 for i in range(236))
    roland = mido.Message("sysex", data=[0x41, 0x02, 0x16, 0x12, 0x04, 0x00, 0x00, *data, 0x00])
    rol = song([(0, alloc(2, program=5)), (1, roland), *note(10, 2, 40)])
    tbs = imuse.timbres(imuse.mdhd_smf(rol))
    assert [(tb.name, tb.data) for tb in tbs] == [("ReggaeBass", data)]
    tr, _ = imuse.read(rol, {tbs[0].key: 139})
    assert events(tr, Kind.TIMBRE) == [(1, 1, 139, 0)]                     # part 2 -> MT-32 part 1


def test_channels_go_onto_mt32_parts_in_order_of_first_note():
    rol = song([*note(0, 5, 60), *note(10, 0, 62), *note(20, 9, 36), *note(30, 3, 64)])
    tr, _ = imuse.read(rol, {})
    assert [(c, k) for _, c, k, _ in events(tr, Kind.NOTE_ON)] == [(1, 60), (2, 62), (9, 36), (3, 64)]


def test_the_smf_reader_takes_what_mido_refuses():
    """MI2 has SMPTE offsets with frame-rate code 4 (mido: KeyError)."""
    trk = bytes([0x00, 0xFF, 0x54, 0x05, 0x80, 0, 0, 0, 0,     # SMPTE offset, frame code 4
                 0x00, 0x90, 60, 100, 0x60, 60, 0,            # note on, then running status (vel 0)
                 0x00, 0xFF, 0x2F, 0x00])
    smf = b"MThd" + struct.pack(">IHHH", 6, 2, 1, B) + b"MTrk" + struct.pack(">I", len(trk)) + trk
    div, tracks = imuse.read_smf(smf)
    assert div == B and [(e.tick, e.status, e.data) for e in tracks[0] if e.status != 0xFF] == \
        [(0, 0x90, bytes([60, 100])), (0x60, 0x90, bytes([60, 0]))]
    with pytest.raises(Exception):
        import io
        mido.MidiFile(file=io.BytesIO(smf))
