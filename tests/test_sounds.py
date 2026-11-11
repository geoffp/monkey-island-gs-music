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
                                for s in sounds.BANK_SLOTS if s != 9]}
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
