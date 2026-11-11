# SPDX-License-Identifier: GPL-3.0-or-later
"""Apple IIGS battery RAM (BRAM) in GSSquared .gs2 machine configs.

GSSquared stores the 256-byte BRAM as `bram = "<512 hex digits>"` in the .gs2 and writes it
back on quit. `migs.gs2config` (gs2-config) uses this module to patch Control Panel settings and to
fix the checksum, so the IIgs accepts them (an invalid checksum makes the ROM reset BRAM to
its defaults).

Layout and checksum: GSSquared Docs/GSClockBatteryRAM.md (ROM routine BCheckSum).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

SIZE = 256
CHECKSUM_AT = 0xFC          # checksum word, then its complement (checksum ^ $AAAA)
COMPLEMENT_MASK = 0xAAAA

SLOT_INTERNAL = {s: 0x20 + s for s in range(1, 8)}   # $21-$27: 0 = built-in, 1 = Your Card
STARTUP_SLOT = 0x28                                    # 0 = scan, 1-7 = slot, 8 RAM, 9 ROM disk

BRAM_RE = re.compile(r'^bram\s*=\s*"([0-9a-fA-F]{512})"\s*$', re.M)


def checksum(b: bytes) -> int:
    """ROM BCheckSum over the settings bytes: for each 16-bit little-endian word starting at
    $FA, $F9, ..., $00 (the words overlap by a byte), rotate the sum left, then add the word.

    The CPX #$FF loop test clears carry before every ROL, so the bit rotated in is always 0,
    while the bit rotated out comes back through the ADC's carry. (Verified against BRAM saved
    by GSSquared.)
    """
    a = 0
    for x in range(CHECKSUM_AT - 2, -1, -1):
        carry = a >> 15
        a = (((a << 1) & 0xFFFF) + (b[x] | b[x + 1] << 8) + carry) & 0xFFFF
    return a


def with_checksum(b: bytes) -> bytes:
    """`b` with its checksum and complement rewritten (bytes $FC-$FF)."""
    if len(b) != SIZE:
        raise ValueError(f"BRAM is {SIZE} bytes, not {len(b)}")
    c = checksum(b)
    n = c ^ COMPLEMENT_MASK
    return bytes(b[:CHECKSUM_AT]) + bytes([c & 0xFF, c >> 8, n & 0xFF, n >> 8])


def is_valid(b: bytes) -> bool:
    """True if `b` is a whole BRAM image whose checksum the ROM would accept."""
    return len(b) == SIZE and with_checksum(b) == bytes(b)


def patch(b: bytes, settings: Mapping[int, int]) -> bytes:
    """`b` with each `settings` address set to its value, and the checksum fixed."""
    out = bytearray(b)
    for addr, value in settings.items():
        if not 0 <= addr < CHECKSUM_AT:
            raise ValueError(f"BRAM address ${addr:02X} is not a setting")
        out[addr] = value
    return with_checksum(bytes(out))
