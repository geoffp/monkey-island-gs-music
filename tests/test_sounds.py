# SPDX-License-Identifier: GPL-3.0-or-later
"""The songs decide which MT-32 sounds a build samples; mt32sounds.toml only decides how
(migs.sounds). Sounds the songs play that no entry covers get defaults instead of being dropped."""

import pytest

from migs import mt32, sounds
from migs.ntpsong import Note

SPEC = {"bank": "T", "melodic": [{"slot": 1, "name": "Bass", "program": 65, "covers": [65, 66], "key": 36}],
        "drum": [{"slot": 9, "name": "Kick", "key": 36, "covers": [35, 36], "bytes": 2048}]}


def note(ch, key, t=0.0):
    return Note(t, t + 0.5, key, 100, ch)


def test_a_fully_covered_song_changes_nothing():
    """Monkey Island's case: every sound is listed, so the spec is returned as it is."""
    song = ([note(1, 40), note(2, 30), note(9, 35), note(9, 36)], {1: 65, 2: 66})
    lines = []
    assert sounds.complete(SPEC, [song], lines.append) is SPEC and lines == []


def test_an_uncovered_program_gets_a_default_sound():
    song = ([note(3, k) for k in (60, 64, 67, 72, 62)], {3: 50})
    lines = []
    out = sounds.complete(SPEC, [song], lines.append)
    m = out["melodic"][-1]
    assert m == {"slot": 2, "name": mt32.MT32_PRESET_NAMES[50], "program": 50, "covers": [50], "key": 64,
                 "default": True}
    assert out["melodic"][0] is SPEC["melodic"][0] and len(out["melodic"]) == 2
    assert "default for MT-32 program 50" in lines[0] and "5 notes" in lines[0]
    assert SPEC["melodic"] == [SPEC["melodic"][0]]                  # the input isn't modified


def test_defaults_take_free_bank_slots_then_slots_past_the_bank():
    """MIDI Synth melodic slots are 1-15 except 9 (a drum kit); NTP only needs them unique."""
    full = {**SPEC, "melodic": [{"slot": s, "name": f"S{s}", "program": s, "covers": [s], "key": 60}
                                for s in range(1, 16) if s != 9]}
    song = ([note(1, 60), note(2, 60)], {1: 100, 2: 101})
    out = sounds.complete(full, [song])
    assert [m["slot"] for m in out["melodic"][-2:]] == [200, 201]
    one_free = {**full, "melodic": full["melodic"][1:]}               # slot 1 free
    assert [m["slot"] for m in sounds.complete(one_free, [song])["melodic"][-2:]] == [1, 200]


def test_rhythm_keys_get_default_drums_unless_silent_on_the_mt32():
    song = ([note(9, 38), note(9, 38, 1.0), note(9, 76)], {})
    lines = []
    out = sounds.complete(SPEC, [song], lines.append)
    assert [d["key"] for d in out["drum"]] == [36, 38]
    assert out["drum"][-1] == {"slot": 9, "name": mt32.GM_DRUM_NAMES[38], "key": 38, "covers": [38],
                               "bytes": sounds.DRUM_BYTES, "default": True}
    assert 76 not in mt32.MT32_RHYTHM_KEYS and any("76" in l and "silent" in l for l in lines)


def test_notes_on_channels_without_a_program_count_for_nothing():
    """A channel that never gets a program change has no MT-32 sound to take a default from."""
    assert sounds.complete(SPEC, [([note(4, 60)], {})]) is SPEC


def test_played_collects_over_every_song():
    progs, keys = sounds.played([([note(1, 60), note(9, 36)], {1: 5}), ([note(2, 62), note(9, 36)], {2: 5})])
    assert progs == {5: [60, 62]} and keys == {36: 2}


def test_the_midisynth_bank_refuses_sounds_past_its_slots(tmp_path):
    from migs import mt32bank
    spec = {**SPEC, "melodic": [{**SPEC["melodic"][0], "slot": 200}]}
    with pytest.raises(ValueError, match="no MIDI Synth bank slot for Bass"):
        mt32bank.build(spec, tmp_path / "none", tmp_path, tmp_path)


def test_an_empty_sound_list_builds_every_sound_from_defaults(tmp_path):
    """A new game's mt32sounds.toml can hold no entries (or be absent): every program and MT-32
    rhythm key the songs play still gets a sound, and the NTP spec layering accepts it."""
    from migs import ntpmod
    (tmp_path / "s.toml").write_text("hold_seconds = 2.0\n")
    for path in (tmp_path / "s.toml", tmp_path / "none.toml"):
        spec = sounds.load(path, bank="TG1")
        assert spec["bank"] == "TG1" and spec["melodic"] == [] and spec["drum"] == []
        spec = ntpmod.ntp_spec(spec, {}, "per-song")
        out = sounds.complete(spec, [([note(1, 60), note(9, 36), note(9, 38)], {1: 9})])
        assert [m["program"] for m in out["melodic"]] == [9] and out["melodic"][0]["slot"] == 1
        assert [(d["key"], d["slot"]) for d in out["drum"]] == [(36, 9), (38, 9)]


def test_defaults_never_share_a_slot_with_a_kit_or_a_name_with_an_entry():
    spec = {"melodic": [{"slot": 1, "name": "Marimba", "program": 103, "covers": [103], "key": 60}],
            "drum": [{"slot": 2, "name": "Kick", "key": 36, "covers": [36], "bytes": 2048}]}
    out = sounds.complete(spec, [([note(1, 60), note(2, 60), note(9, 38)], {1: 104, 2: 50})])
    melodic = out["melodic"][1:]
    assert [m["slot"] for m in melodic] == [3, 4]                     # not 1 (melodic), 2 (a kit), 9 or 0
    assert melodic[1]["name"] == "Marimba (program 104)" and mt32.MT32_PRESET_NAMES[104] == "Marimba"
    assert out["drum"][-1]["slot"] == 2                                # into the existing kit


def test_a_full_kit_sends_default_drums_past_the_bank():
    spec = {"melodic": [], "drum": [{"slot": 9, "name": f"D{k}", "key": k, "covers": [k], "bytes": 512}
                                    for k in range(35, 43)]}
    out = sounds.complete(spec, [([note(9, 45)], {})])
    assert out["drum"][-1]["slot"] >= sounds.EXTRA_SLOT


def test_the_midisynth_bank_refuses_a_slot_used_by_a_sound_and_a_kit(tmp_path):
    from migs import mt32bank
    spec = {**SPEC, "drum": [{**SPEC["drum"][0], "slot": 1}]}
    with pytest.raises(ValueError, match=r"slot\(s\) \[1\] hold both"):
        mt32bank.build(spec, tmp_path / "none", tmp_path, tmp_path)


def test_custom_timbres_and_a_drivers_rhythm_keys_get_defaults_too():
    """iMUSE games: a custom timbre (id >= 128) is named by the timbre's own name; rhythm keys
    the game's driver maps (MI2: 24-34) are sounds, not silent."""
    song = ([Note(0, 0.5, 40, 100, 1, program=139), note(9, 24)], {})
    lines = []
    out = sounds.complete({"melodic": [], "drum": []}, [song], lines.append, timbre_names={139: "ReggaeBass"},
                          rhythm_keys=mt32.MT32_RHYTHM_KEYS | {24})
    assert [(m["program"], m["name"]) for m in out["melodic"]] == [(139, "ReggaeBass")]
    assert [d["key"] for d in out["drum"]] == [24] and "custom timbre 139" in lines[0]
    assert sounds.complete({"melodic": [], "drum": []}, [([note(9, 24)], {})])["drum"] == []
