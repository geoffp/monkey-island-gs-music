# SPDX-License-Identifier: GPL-3.0-or-later
"""SCUMM v4 container access, using a tiny synthetic game, plus the real game if present."""

import struct
from pathlib import Path

import pytest

from migs import games
from migs.extract_music import load_songs
from migs.scumm_v4 import Game, blocks, xor

ROOT = Path(__file__).resolve().parents[1]
MONKEY = games.load("monkey", ROOT)          # the real-game test checks Monkey Island's facts
REAL_GAME = MONKEY.data


def blk(tag: bytes, body: bytes) -> bytes:
    return struct.pack("<I", len(body) + 6) + tag + body


def directory(tag: bytes, entries) -> bytes:
    return blk(tag, struct.pack("<H", len(entries)) + b"".join(struct.pack("<BI", a, b) for a, b in entries))


@pytest.fixture
def tiny_game(tmp_path):
    """Room 7 on disk 1 holds a room RO block, two identical RO songs and an SO sound."""
    song = bytes([0xC1, 77, 0x91, 60, 100, 0xF0, 120, 0x81, 60, 0, 0xFF])
    other = bytes([0x91, 62, 100, 0xF0, 60, 0x00])
    children = [blk(b"RO", b"HDroomdata"), blk(b"RO", song), blk(b"SO", blk(b"AD", b"\x7f\x00\x80")),
                blk(b"RO", song), blk(b"RO", other)]
    offs, pos = [], 0
    for c in children:
        offs.append(pos)        # sound offsets are relative to the LF data start
        pos += len(c)
    lf = struct.pack("<I", 8 + pos) + b"LF" + struct.pack("<H", 7) + b"".join(children)
    fo = blk(b"FO", bytes([1]) + struct.pack("<BI", 7, 6 + 12))   # LE header + FO block
    disk = blk(b"LE", fo + lf)
    (tmp_path / "DISK01.LEC").write_bytes(xor(disk))

    rooms = [(0, 0)] * 8
    rooms[7] = (1, 0)
    sounds = [(0, 0), (7, offs[1]), (7, offs[2]), (7, offs[3]), (7, offs[4])]
    index = blk(b"RN", b"\x00") + directory(b"0R", rooms) + directory(b"0S", []) + \
        directory(b"0N", sounds) + directory(b"0C", [])
    (tmp_path / "000.lfl").write_bytes(index)        # lower case: lookup is case-insensitive
    return tmp_path, song


def test_sound_lookup_resolves_offsets_relative_to_lf_data(tiny_game):
    path, song = tiny_game
    g = Game(path)
    res = g.sound(1)
    assert res.tag == b"RO" and res.payload == song and res.room == 7 and res.disk == 1
    assert g.sound(2).tag == b"SO"
    with pytest.raises(KeyError):
        g.sound(0)                                    # unused directory entry


def test_load_songs_takes_ro_only_and_dedupes(tiny_game):
    path, _ = tiny_game
    songs = load_songs(Game(path), {"1": "Test Song"})
    assert [(s.sound_id, s.duplicates) for s in songs] == [(1, [3]), (4, [])]
    assert songs[0].stem == "001-test-song" and songs[0].track.loops
    assert songs[1].stem == "004" and not songs[1].track.loops


@pytest.mark.skipif(not REAL_GAME.exists(), reason="game data not present")
def test_real_game_mt32_soundtrack():
    """docs/games/monkey.md: 19 sounds (150-168), 16 distinct, all in room 94 on DISK04."""
    assert MONKEY.md5_mismatches() == []
    warnings = []
    songs = load_songs(Game(REAL_GAME), {}, warn=warnings.append, label=MONKEY.label)
    assert warnings and "[72, 74, 76, 78, 80, 82, 84, 86, 88, 90, 92, 94, 95, 96, 97]" in warnings[0]
    assert [s.sound_id for s in songs] == [150, 151, 152, 153, 154, *range(158, 169)]
    assert next(s for s in songs if s.sound_id == 154).duplicates == [155, 156, 157]
    assert all(s.resource_room == 94 and s.resource_disk == 4 for s in songs)
    lengths = {s.sound_id: s.track.end_tick for s in songs}
    assert round(lengths[160] / 120 * 0.5, 1) == 93.6                 # matches docs/games/monkey.md
    assert songs[0].title == "MI1 sound 150"


def test_a_zero_size_block_is_an_error_not_an_endless_scan():
    data = blk(b"AA", b"xy") + struct.pack("<I", 0) + b"BB"
    with pytest.raises(ValueError, match="size 0"):
        list(blocks(data))
