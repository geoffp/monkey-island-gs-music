# SPDX-License-Identifier: GPL-3.0-or-later
"""SCUMM v4 resource access (Monkey Island 1, DOS VGA floppy).

Format notes: docs/scumm-v4-resources.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

XOR_KEY = 0x69


def xor(data: bytes, key: int = XOR_KEY) -> bytes:
    return bytes(b ^ key for b in data)


def _find_file(directory: Path, name: str) -> Path:
    """Case-insensitive file lookup (DOS file names)."""
    exact = directory / name
    if exact.exists():
        return exact
    for p in directory.iterdir():
        if p.name.lower() == name.lower():
            return p
    raise FileNotFoundError(f"{name} not found in {directory}")


def block_header(data: bytes, off: int) -> tuple[int, bytes]:
    """v4 block header: uint32 LE size (includes the 6-byte header) + 2-char tag."""
    size = struct.unpack_from("<I", data, off)[0]
    return size, data[off + 4 : off + 6]


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
        dirs: dict[bytes, list[tuple[int, int]]] = {}
        off = 0
        while off + 6 <= len(data):
            size, tag = block_header(data, off)
            if size < 6:
                raise ValueError(f"bad index block {tag!r} at {off}")
            if tag in (b"0R", b"0S", b"0N", b"0C"):
                count = struct.unpack_from("<H", data, off + 6)[0]
                dirs[tag] = [
                    (data[off + 8 + i * 5], struct.unpack_from("<I", data, off + 9 + i * 5)[0])
                    for i in range(count)
                ]
            off += size
        return cls(rooms=dirs[b"0R"], sounds=dirs[b"0N"])


@dataclass
class SoundResource:
    sound_id: int
    tag: bytes        # b"RO" (Roland MT-32) or b"SO" (WA/AD container)
    payload: bytes    # block contents after the 6-byte header
    disk: int
    room: int
    offset: int       # absolute offset of the block in the (decrypted) disk file


class Game:
    """Read-only access to a SCUMM v4 game directory."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.index = Index.parse(_find_file(self.dir, "000.LFL").read_bytes())
        self._disks: dict[int, tuple[bytes, dict[int, int]]] = {}

    def _disk(self, n: int) -> tuple[bytes, dict[int, int]]:
        """Decrypted disk file plus its FO table (room -> LF offset)."""
        if n not in self._disks:
            data = xor(_find_file(self.dir, f"DISK{n:02d}.LEC").read_bytes())
            size, tag = block_header(data, 0)
            if tag != b"LE" or size != len(data):
                raise ValueError(f"DISK{n:02d}.LEC: expected LE block covering the file")
            fo_size, fo_tag = block_header(data, 6)
            if fo_tag != b"FO":
                raise ValueError(f"DISK{n:02d}.LEC: expected FO block")
            count = data[12]
            fo = {
                data[13 + i * 5]: struct.unpack_from("<I", data, 14 + i * 5)[0]
                for i in range(count)
            }
            self._disks[n] = (data, fo)
        return self._disks[n]

    def room_lf(self, room: int) -> tuple[bytes, int, int]:
        """Return (disk data, LF block offset, disk number) for a room."""
        disk_no = self.index.rooms[room][0]
        data, fo = self._disk(disk_no)
        # FO offsets point at the LF block; fall back to a scan if they don't.
        off = fo.get(room)
        if off is None or data[off + 4 : off + 6] != b"LF":
            off = self._scan_lf(data, room)
        if struct.unpack_from("<H", data, off + 6)[0] != room:
            raise ValueError(f"LF at {off} on disk {disk_no} is not room {room}")
        return data, off, disk_no

    @staticmethod
    def _scan_lf(data: bytes, room: int) -> int:
        off = 6 + block_header(data, 6)[0]
        while off + 8 <= len(data):
            size, tag = block_header(data, off)
            if tag == b"LF" and struct.unpack_from("<H", data, off + 6)[0] == room:
                return off
            off += size
        raise KeyError(f"room {room} not found")

    def sound(self, sound_id: int) -> SoundResource:
        room, rel = self.index.sounds[sound_id]
        if room == 0:
            raise KeyError(f"sound {sound_id} is not used")
        data, lf_off, disk_no = self.room_lf(room)
        off = lf_off + 8 + rel  # offsets are relative to the LF data (after size, tag, room)
        size, tag = block_header(data, off)
        lf_size = block_header(data, lf_off)[0]
        if tag not in (b"RO", b"SO") or off + size > lf_off + lf_size:
            raise ValueError(f"sound {sound_id}: unexpected block {tag!r} size {size} at {off}")
        return SoundResource(sound_id, tag, data[off + 6 : off + size], disk_no, room, off)
