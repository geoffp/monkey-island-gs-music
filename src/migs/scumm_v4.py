# SPDX-License-Identifier: GPL-3.0-or-later
"""SCUMM v4 resource access (Monkey Island 1, DOS VGA floppy).

A v4 file is a tree of blocks, each `uint32 LE size` (including the 6-byte header) + a 2-char
tag. The index `000.LFL` holds directories (rooms, sounds, ...); each `DISKnn.LEC` (XOR 0x69)
is an `LE` block: an `FO` table of room offsets, then one `LF` block per room. Format notes:
docs/scumm-v4-resources.md.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

XOR_KEY = 0x69
HEADER = 6                       # block header: size (4) + tag (2)
LF_DATA = HEADER + 2             # an LF block's data starts after its room number
DIRECTORY_TAGS = (b"0R", b"0S", b"0N", b"0C")   # rooms, scripts, sounds, costumes


def xor(data: bytes, key: int = XOR_KEY) -> bytes:
    return data.translate(bytes(b ^ key for b in range(256)))


def _find_file(directory: Path, name: str) -> Path:
    """Case-insensitive file lookup (DOS file names)."""
    exact = directory / name
    if exact.exists():
        return exact
    for p in directory.iterdir():
        if p.name.lower() == name.lower():
            return p
    raise FileNotFoundError(f"{name} not found in {directory}")


class Block(NamedTuple):
    offset: int
    size: int        # including the header
    tag: bytes

    @property
    def end(self) -> int:
        return self.offset + self.size


def block_at(data: bytes, off: int) -> Block:
    """The block whose header starts at `off`."""
    if off + HEADER > len(data):
        raise ValueError(f"block header at {off} runs past the end of the data")
    size, tag = struct.unpack_from("<I2s", data, off)
    if size < HEADER:
        raise ValueError(f"bad block {tag!r} at {off}: size {size}")
    return Block(off, size, tag)


def blocks(data: bytes, start: int = 0, end: int | None = None) -> Iterator[Block]:
    """The consecutive blocks in data[start:end]."""
    end = len(data) if end is None else end
    off = start
    while off + HEADER <= end:
        b = block_at(data, off)
        yield b
        off = b.end


def _table(data: bytes, off: int, count: int) -> list[tuple[int, int]]:
    """`count` 5-byte entries (uint8, uint32 LE) starting at `off`."""
    return list(struct.iter_unpack("<BI", data[off:off + 5 * count]))


@dataclass
class Index:
    rooms: list[tuple[int, int]]   # room -> (disk number, offset)
    sounds: list[tuple[int, int]]  # sound -> (room, offset within room LF data)

    @classmethod
    def parse(cls, data: bytes) -> Index:
        if data[4:6] != b"RN":
            data = xor(data)  # other releases may encrypt the index
        if data[4:6] != b"RN":
            raise ValueError("not a SCUMM v4 index (expected 'RN' block first)")
        dirs = {b.tag: _table(data, b.offset + HEADER + 2, struct.unpack_from("<H", data, b.offset + HEADER)[0])
                for b in blocks(data) if b.tag in DIRECTORY_TAGS}
        if b"0R" not in dirs or b"0N" not in dirs:
            raise ValueError("index has no room (0R) or sound (0N) directory")
        return cls(rooms=dirs[b"0R"], sounds=dirs[b"0N"])


@dataclass
class SoundResource:
    sound_id: int
    tag: bytes        # b"RO" (Roland MT-32) or b"SO" (WA/AD container)
    payload: bytes    # block contents after the 6-byte header
    disk: int
    room: int
    offset: int       # absolute offset of the block in the (decrypted) disk file


@dataclass
class _Disk:
    data: bytes             # decrypted
    fo: dict[int, int]      # room -> LF block offset


class Game:
    """Read-only access to a SCUMM v4 game directory."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.index = Index.parse(_find_file(self.dir, "000.LFL").read_bytes())
        self._disks: dict[int, _Disk] = {}

    def _disk(self, n: int) -> _Disk:
        """Disk file `n`, decrypted and with its FO table (cached)."""
        if n not in self._disks:
            name = f"DISK{n:02d}.LEC"
            data = xor(_find_file(self.dir, name).read_bytes())
            le = block_at(data, 0)
            if le.tag != b"LE" or le.size != len(data):
                raise ValueError(f"{name}: expected an LE block covering the file")
            fo = block_at(data, HEADER)
            if fo.tag != b"FO":
                raise ValueError(f"{name}: expected an FO block")
            count = data[fo.offset + HEADER]
            self._disks[n] = _Disk(data, dict(_table(data, fo.offset + HEADER + 1, count)))
        return self._disks[n]

    @staticmethod
    def _lf_room(data: bytes, b: Block) -> int:
        return struct.unpack_from("<H", data, b.offset + HEADER)[0]

    def room_lf(self, room: int) -> tuple[bytes, int, int]:
        """Return (disk data, LF block offset, disk number) for a room."""
        disk_no = self.index.rooms[room][0]
        disk = self._disk(disk_no)
        # FO offsets point at the LF block; fall back to a scan if they don't.
        off = disk.fo.get(room)
        if off is None or disk.data[off + 4:off + 6] != b"LF":
            off = self._scan_lf(disk.data, room)
        if self._lf_room(disk.data, block_at(disk.data, off)) != room:
            raise ValueError(f"LF at {off} on disk {disk_no} is not room {room}")
        return disk.data, off, disk_no

    @classmethod
    def _scan_lf(cls, data: bytes, room: int) -> int:
        start = block_at(data, HEADER).end                   # after the FO block
        for b in blocks(data, start):
            if b.tag == b"LF" and cls._lf_room(data, b) == room:
                return b.offset
        raise KeyError(f"room {room} not found")

    def sound(self, sound_id: int) -> SoundResource:
        room, rel = self.index.sounds[sound_id]
        if room == 0:
            raise KeyError(f"sound {sound_id} is not used")
        data, lf_off, disk_no = self.room_lf(room)
        lf = block_at(data, lf_off)
        b = block_at(data, lf_off + LF_DATA + rel)   # offsets are relative to the LF data
        if b.tag not in (b"RO", b"SO") or b.end > lf.end:
            raise ValueError(f"sound {sound_id}: unexpected block {b.tag!r} size {b.size} at {b.offset}")
        return SoundResource(sound_id, b.tag, data[b.offset + HEADER:b.end], disk_no, room, b.offset)
