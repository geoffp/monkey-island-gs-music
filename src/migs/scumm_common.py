# SPDX-License-Identifier: GPL-3.0-or-later
"""What the SCUMM v4 and v5 readers share: the XOR key of the data files and the block record.

The two block layouts differ (v4: `uint32 LE size` + 2-char tag; v5: 4-char tag + `uint32 BE
size`), so each reader keeps its own `block_at`/`blocks`.
"""

from __future__ import annotations

from typing import NamedTuple

XOR_KEY = 0x69


def xor(data: bytes, key: int = XOR_KEY) -> bytes:
    return data.translate(bytes(b ^ key for b in range(256)))


class Block(NamedTuple):
    offset: int
    size: int        # including the header
    tag: bytes

    @property
    def end(self) -> int:
        return self.offset + self.size
