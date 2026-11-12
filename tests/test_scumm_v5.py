# SPDX-License-Identifier: GPL-3.0-or-later
"""SCUMM v5 containers (migs.scumm_v5) and what the v5 path adds downstream: custom MT-32
timbres in the extracted MIDI (extract_music), notes' programs (ntpsong.read_notes), Munt
renders (munt), the rhythm keys a driver sets up (mt32). Synthetic data throughout."""

import struct

import mido
import pytest

from migs import extract_music as em
from migs import imuse, mt32, munt, ro, scumm_v5
from migs import ntpsong as sg

import test_imuse as ti


def blk(tag: bytes, body: bytes, header_in_size=True) -> bytes:
    return tag + struct.pack(">I", len(body) + (8 if header_in_size else 0)) + body


def v5_game(tmp_path, rol: bytes, sound_id=3):
    """index (DSOU) + data (LECF: LOFF, one LFLF with a ROOM and the sound), XOR 0x69."""
    room = blk(b"ROOM", bytes(16))
    soun = blk(b"SOUN", blk(b"SOU ", blk(b"ADL ", b"\x01\x02", False) + blk(b"ROL ", rol, False)))
    loff_len = 8 + 1 + 5
    room_at = 8 + loff_len + 8                     # LECF header, LOFF, LFLF header
    lflf = blk(b"LFLF", room + soun)
    loff = blk(b"LOFF", bytes([1]) + struct.pack("<BI", 1, room_at))
    data = blk(b"LECF", loff + lflf)
    n = sound_id + 1
    rooms, offs = [0] * n, [0] * n
    rooms[sound_id], offs[sound_id] = 1, len(room)
    index = blk(b"RNAM", b"\x00") + blk(b"DSOU", struct.pack("<H", n) + bytes(rooms) + struct.pack(f"<{n}I", *offs))
    (tmp_path / "game.000").write_bytes(scumm_v5.xor(index))
    (tmp_path / "game.001").write_bytes(scumm_v5.xor(data))
    return scumm_v5.Game(tmp_path)


def test_a_sound_is_found_through_the_index_and_its_room_offset(tmp_path):
    """Sizes are BE and include the header, except the SOU container's children's (ScummVM
    readSoundResource adds 8)."""
    g = v5_game(tmp_path, b"MThd-not-really")
    assert g.sounds[3] == (1, 24) and g.sounds[0] == (0, 0)
    s = g.sound(3)
    assert s.parts == {b"ADL ": b"\x01\x02", b"ROL ": b"MThd-not-really"}
    with pytest.raises(KeyError):
        g.sound(1)


def _timbre_song():
    data = b"ReggaeBass" + bytes(i % 128 for i in range(236))
    roland = mido.Message("sysex", data=[0x41, 0x02, 0x16, 0x12, 0x04, 0x00, 0x00, *data, 0x00])
    return data, ti.song([(0, ti.alloc(2, program=5)), (1, roland), *ti.note(10, 2, 40), *ti.note(300, 2, 43)])


def test_v5_songs_extract_with_their_custom_timbres(tmp_path):
    """The MT-32 MIDI says which timbre a channel plays (a text event the NTP build reads) and
    uploads it (DT1 to that part's timbre temporary area); the GM MIDI maps it by name."""
    data, rol = _timbre_song()
    g = v5_game(tmp_path, rol)
    songs, timbres = em.load_songs_v5(g, {}, {}, label="T5")
    assert [s.sound_id for s in songs] == [3] and [tb.name for tb in timbres] == ["ReggaeBass"]
    ctx = em.Context({128: timbres[0]}, [bytes.fromhex("41101612030110407f0600")], {"ReggaeBass": 32})
    mt = em.build_midi(songs[0], gm=False, ctx=ctx)
    msgs = [m for trk in mt.tracks for m in trk]
    assert any(m.type == "text" and m.text == "timbre ch1 128 ReggaeBass" for m in msgs)
    dt1 = [bytes(m.data) for m in msgs if m.type == "sysex" and len(m.data) > 200]
    assert dt1 == [em.roland_dt1(em.timbre_temp_addr(1), data)] and dt1[0][4:7] == b"\x04\x00\x00"
    assert bytes(next(m for m in mt.tracks[0] if m.type == "sysex").data) == ctx.init[0]
    gm = em.build_midi(songs[0], gm=True, ctx=ctx)
    assert [m.program for trk in gm.tracks for m in trk if m.type == "program_change"] == [mt32.MT32_TO_GM[5], 32]
    mt.save(p := tmp_path / "3.mid")
    notes, chan_prog, _ = sg.read_notes(p)
    assert [(n.key, n.program) for n in notes] == [(40, 128), (43, 128)]
    assert sg.program_of(notes[0], chan_prog) == 128
    toml = em.timbres_toml(timbres, {"ReggaeBass": 32})
    (tmp_path / "timbres.toml").write_text(toml)
    assert em.read_timbres(tmp_path / "timbres.toml") == {128: timbres[0]}
    assert em.read_timbres(tmp_path / "none.toml") == {}


def test_timbre_temporary_areas_are_246_bytes_apart_in_7_bit_addresses():
    assert em.timbre_temp_addr(1) == 0x04 << 14
    assert em.timbre_temp_addr(2) == 0x04 << 14 | 0x01 << 7 | 0x76          # 04 01 76


def test_a_note_takes_the_program_its_channel_has_at_its_onset(tmp_path):
    """iMUSE parts change programs mid-song (MI2: 30 channels); MI1's never do."""
    p = ti_midi(tmp_path, [mido.Message("program_change", channel=1, program=10, time=0),
                           mido.Message("note_on", channel=1, note=60, velocity=90, time=0),
                           mido.Message("note_off", channel=1, note=60, time=60),
                           mido.Message("program_change", channel=1, program=20, time=0),
                           mido.Message("note_on", channel=1, note=62, velocity=90, time=0),
                           mido.Message("note_off", channel=1, note=62, time=60)])
    notes, chan_prog, _ = sg.read_notes(p)
    assert [sg.program_of(n, chan_prog) for n in notes] == [10, 20] and chan_prog == {1: 10}


def ti_midi(tmp_path, msgs):
    mf = mido.MidiFile(ticks_per_beat=120)
    mf.tracks.append(mido.MidiTrack(msgs))
    mf.save(p := tmp_path / "p.mid")
    return p


def test_munt_renders_a_custom_timbre_after_the_drivers_setup():
    """The set-up first, then reverb off, then the timbre into part 1's temporary area (no
    program change after it, which would replace it). Cache names say what plays: a preset's
    number (unchanged for MI1), else the timbre's content and the set-up."""
    data = bytes(range(10)) + bytes(236)
    setup = (bytes.fromhex("41101612030110407f0600"),)
    mf = munt._note_file(139, 40, 1.0, 100, 100, setup, data)
    sx = [bytes(m.data) for m in mf.tracks[0] if m.type == "sysex"]
    assert sx[0] == setup[0] and sx[1] == bytes(munt.reverb_off().data)
    assert sx[2][:7] == bytes([0x41, 0x10, 0x16, 0x12, 0x04, 0x00, 0x00]) and sx[2][7:-1] == data
    assert not any(m.type == "program_change" for m in mf.tracks[0])
    assert munt.sound_tag(13) == "p013" and munt.sound_tag(None) == "rhythm"
    assert munt.sound_tag(139, (), data).startswith("t") and munt.sound_tag(13, setup).startswith("p013-i")
    m = munt.Munt(None, None, None, setup, ((139, data),))
    assert m.timbre(139) == data and m.timbre(13) is None and m.sound_tag(139) == munt.sound_tag(139, setup, data)
    with pytest.raises(ValueError, match="custom timbre"):
        munt.render(None, None, None, program=139, key=40, hold=1.0)


def test_a_drivers_rhythm_setup_adds_rhythm_keys():
    """MI2's driver maps keys 24-34 (rhythm setup 03 01 10, 4 bytes a key)."""
    s = em.roland_dt1(mt32.RHYTHM_SETUP, bytes([0x40, 0x64, 0x07, 0x00] * 11))
    assert mt32.rhythm_keys([s]) - mt32.MT32_RHYTHM_KEYS == frozenset(range(24, 35))
    assert mt32.rhythm_keys() == mt32.MT32_RHYTHM_KEYS
    system = em.roland_dt1(0x10 << 14, bytes(23))
    assert mt32.rhythm_keys([system]) == mt32.MT32_RHYTHM_KEYS


def test_track_seconds_follow_tempo_changes():
    t = ro.Track(ppqn=480, tempo_us=500_000, tempos=[(960, 250_000)])
    assert em.track_seconds(t, 960) == pytest.approx(1.0) and em.track_seconds(t, 1920) == pytest.approx(1.5)
