"""MIDI output: faithful MT-32 timing and the General MIDI conversion rules."""

import tomllib

import mido

from migs import mt32, ro
from migs.extract_music import Song, build_midi, patch_notes


def make_song(data: bytes) -> Song:
    return Song(sound_id=200, duplicates=[], name="", track=ro.parse(data),
                resource_room=94, resource_disk=4, resource_offset=0, size=len(data) + 6)


# Panpipes on ch2, pan hard one way, a note; drums incl. key 76 (unmapped on MT-32).
DATA = bytes([
    0xC1, 77, 0xB1, 10, 0x00,
    0x91, 72, 100, 0x99, 36, 90, 0x99, 76, 90,
    0xA0, 0xF0, 60,
    0x81, 72, 0, 0x89, 36, 0, 0x89, 76, 0,
    0xF0, 60, 0xFF,
])


def abs_messages(track: mido.MidiTrack):
    tick = 0
    for msg in track:
        tick += msg.time
        yield tick, msg


def channel_events(mf: mido.MidiFile):
    return [(t, m) for trk in mf.tracks[1:] for t, m in abs_messages(trk) if not m.is_meta]


def test_mt32_rendition_is_faithful():
    song = make_song(DATA)
    mf = build_midi(song, gm=False)
    assert mf.ticks_per_beat == ro.PPQN and mf.type == 1
    got = sorted((t, m.type, m.channel, m.bytes()[1]) for t, m in channel_events(mf))
    want = sorted((e.tick, {"cc": "control_change", "program": "program_change"}.get(e.kind, e.kind),
                   e.channel, e.a) for e in song.track.events if e.kind != "marker")
    assert got == want
    assert any(m.type == "program_change" and m.program == 77 for _, m in channel_events(mf))


def test_every_track_ends_at_song_end_and_loop_is_marked():
    mf = build_midi(make_song(DATA), gm=False)
    for trk in mf.tracks:
        end_tick, last = list(abs_messages(trk))[-1]
        assert last.type == "end_of_track" and end_tick == 120
    markers = [(t, m.text) for t, m in abs_messages(mf.tracks[0]) if m.type == "marker"]
    assert markers[0] == (0, "loopStart") and markers[-1] == (120, "loopEnd")
    assert (0, "A0") in markers
    tempo = [m.tempo for m in mf.tracks[0] if m.type == "set_tempo"]
    assert tempo == [ro.TEMPO_US]


def test_gm_rendition_applies_conversion_rules():
    mf = build_midi(make_song(DATA), gm=True)
    evs = channel_events(mf)
    progs = [m.program for _, m in evs if m.type == "program_change"]
    assert progs == [mt32.MT32_TO_GM[77]] == [75]                  # Panpipes -> Pan Flute
    pans = [m.value for _, m in evs if m.type == "control_change" and m.control == 10]
    # tick-0 MT-32 power-on default for ch2 (flipped), then the song's own pan 0 flipped to 127
    assert pans == [mt32.flip_pan(mt32.MT32_DEFAULT_PANNING[0]), 127]
    drums = {m.note for _, m in evs if m.type.startswith("note") and m.channel == 9}
    assert drums == {36}                                           # key 76 dropped
    reverb = {m.channel for _, m in evs if m.type == "control_change" and m.control == 91}
    assert reverb == {1, 9}
    assert any(m.type == "sysex" and list(m.data) == [0x7E, 0x7F, 0x09, 0x01] for m in mf.tracks[0])


def test_flip_pan_keeps_center():
    assert mt32.flip_pan(0x40) == 0x40
    assert mt32.flip_pan(0) == 127 and mt32.flip_pan(127) == 1


def test_mt32_tables_cover_every_program():
    assert len(mt32.MT32_PRESET_NAMES) == len(mt32.MT32_TO_GM) == len(mt32.GM_NAMES) == 128
    assert all(0 <= p < 128 for p in mt32.MT32_TO_GM)
    assert mt32.default_pan(1) == mt32.MT32_DEFAULT_PANNING[0] and len(mt32.MT32_DEFAULT_PANNING) == 8


def test_midi_files_survive_a_save_load_round_trip(tmp_path):
    for gm in (False, True):
        mf = build_midi(make_song(DATA), gm=gm)
        p = tmp_path / f"t{gm}.mid"
        mf.save(p)
        back = mido.MidiFile(p)
        assert [list(abs_messages(t)) for t in back.tracks] == [list(abs_messages(t)) for t in mf.tracks]


def test_patch_notes_are_valid_toml_with_channel_statistics():
    notes = tomllib.loads(patch_notes(make_song(DATA)))
    assert notes["song"]["loops"] is True and notes["song"]["max_polyphony"] == 3
    by_ch = {c["channel"]: c for c in notes["channel"]}
    assert by_ch[2]["mt32_name"] == "Panpipes" and by_ch[2]["initial_pan_mt32"] == 0
    assert by_ch[10]["role"] == "rhythm" and by_ch[10]["max_polyphony"] == 2
    assert by_ch[10]["rhythm_keys"]["76"][1].startswith("UNMAPPED")
