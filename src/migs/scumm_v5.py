# SPDX-License-Identifier: GPL-3.0-or-later
"""SCUMM v5 resource access (Monkey Island 2, Fate of Atlantis: DOS).

Two files, both XOR 0x69: the index `<name>.000` and the data `<name>.001`. Each is a run of
blocks, `4-char tag` + `uint32 BE size` (including the 8-byte header). The index holds
directories (`DSOU`: sounds); the data file is one `LECF` block: a `LOFF` table of room
offsets, then one `LFLF` per room. A sound's directory offset is relative to its room's
`LOFF` offset. A `SOUN` block holds a `SOU ` container whose children are one per driver
(`ROL ` MT-32, `ADL `, `SPK `, `GMD ` ...); *their* size fields exclude the 8-byte header.
Format notes and sources: docs/scumm-v5-resources.md.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

XOR_KEY = 0x69
HEADER = 8


def xor(data: bytes, key: int = XOR_KEY) -> bytes:
    return bytes(b ^ key for b in data)


class Block(NamedTuple):
    offset: int
    size: int        # including the header
    tag: bytes

    @property
    def end(self) -> int:
        return self.offset + self.size


def block_at(data: bytes, off: int, header_in_size: bool = True) -> Block:
    if off + HEADER > len(data):
        raise ValueError(f"block header at {off} runs past the end of the data")
    tag, size = data[off:off + 4], struct.unpack_from(">I", data, off + 4)[0]
    size += 0 if header_in_size else HEADER
    if size < HEADER or off + size > len(data):
        raise ValueError(f"bad block {tag!r} at {off}: size {size}")
    return Block(off, size, tag)


def blocks(data: bytes, start: int = 0, end: int | None = None, header_in_size: bool = True) -> Iterator[Block]:
    end = len(data) if end is None else end
    off = start
    while off + HEADER <= end:
        b = block_at(data, off, header_in_size)
        yield b
        off = b.end


def _file(directory: Path, ext: str) -> Path:
    """The one `*.<ext>` file in the game directory (any case)."""
    found = sorted(p for p in directory.iterdir() if p.suffix.lower() == f".{ext}")
    if len(found) != 1:
        raise FileNotFoundError(f"{directory}: expected one *.{ext} file, found {[p.name for p in found]}")
    return found[0]


def directory(index: bytes, tag: bytes) -> list[tuple[int, int]]:
    """A v5 index directory: (room, offset) per resource id (room 0 = unused)."""
    for b in blocks(index):
        if b.tag == tag:
            p = b.offset + HEADER
            n = struct.unpack_from("<H", index, p)[0]
            rooms = index[p + 2:p + 2 + n]
            offs = struct.unpack_from(f"<{n}I", index, p + 2 + n)
            return list(zip(rooms, offs))
    raise ValueError(f"index has no {tag.decode()} directory")


@dataclass
class SoundResource:
    sound_id: int
    room: int
    offset: int                      # absolute offset of the SOUN block in the data file
    parts: dict[bytes, bytes]        # driver tag (b"ROL ", b"ADL ", ...) -> its contents


class Game:
    """Read-only access to a SCUMM v5 game directory."""

    def __init__(self, directory_: str | Path):
        self.dir = Path(directory_)
        index = xor(_file(self.dir, "000").read_bytes())
        self.sounds = directory(index, b"DSOU")
        self.data = xor(_file(self.dir, "001").read_bytes())
        lecf = block_at(self.data, 0)
        if lecf.tag != b"LECF":
            raise ValueError("data file: expected an LECF block")
        loff = block_at(self.data, HEADER)
        if loff.tag != b"LOFF":
            raise ValueError("data file: expected a LOFF block")
        n = self.data[loff.offset + HEADER]
        self.rooms = {self.data[loff.offset + HEADER + 1 + 5 * i]:
                      struct.unpack_from("<I", self.data, loff.offset + HEADER + 2 + 5 * i)[0] for i in range(n)}

    def sound(self, sound_id: int) -> SoundResource:
        room, rel = self.sounds[sound_id]
        if room == 0:
            raise KeyError(f"sound {sound_id} is not used")
        b = block_at(self.data, self.rooms[room] + rel)
        if b.tag != b"SOUN":
            raise ValueError(f"sound {sound_id}: expected SOUN, found {b.tag!r} at {b.offset}")
        parts: dict[bytes, bytes] = {}
        if self.data[b.offset + HEADER:b.offset + HEADER + 4] == b"SOU " and \
                struct.unpack_from(">I", self.data, b.offset + 12)[0] == 0:
            return SoundResource(sound_id, room, b.offset, parts)      # an empty container (MI2: 2 sounds)
        inner = block_at(self.data, b.offset + HEADER)
        if inner.tag == b"SOU ":
            for c in blocks(self.data, inner.offset + HEADER, inner.end, header_in_size=False):
                parts.setdefault(c.tag, self.data[c.offset + HEADER:c.end])
        else:                            # a bare resource (e.g. MIDI): kept under its own tag
            parts[inner.tag] = self.data[inner.offset + HEADER:inner.end]
        return SoundResource(sound_id, room, b.offset, parts)
