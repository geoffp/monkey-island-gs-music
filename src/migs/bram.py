# SPDX-License-Identifier: GPL-3.0-or-later
"""Apple IIGS battery RAM (BRAM) in GSSquared .gs2 machine configs.

GSSquared stores the 256-byte BRAM as `bram = "<512 hex digits>"` in the .gs2 and writes it
back on quit. `migs.emu` (gs2-config) uses this to patch Control Panel settings and fixes the checksum, so the IIgs
accepts them (an invalid checksum makes the ROM reset BRAM to defaults).

Layout and checksum: GSSquared Docs/GSClockBatteryRAM.md (ROM routine BCheckSum).
"""

from __future__ import annotations

import re

SLOT_INTERNAL = {s: 0x20 + s for s in range(1, 8)}   # $21-$27: 0 = built-in, 1 = Your Card
STARTUP_SLOT = 0x28                                    # 0 = scan, 1-7 = slot, 8 RAM, 9 ROM disk
BRAM_RE = re.compile(r'^bram\s*=\s*"([0-9a-fA-F]{512})"\s*$', re.M)


def checksum(b: bytes) -> int:
    """ROM BCheckSum: from the word at $FA down to $00, ROL then ADC.

    The CPX #$FF loop test clears carry before every ROL, so the bit rotated in is always 0,
    while the bit rotated out is added back by the ADC. (Verified against BRAM saved by
    GSSquared.)
    """
    a = 0
    for x in range(0xFA, -1, -1):
        out = a >> 15
        a = (((a << 1) & 0xFFFF) + (b[x] | b[x + 1] << 8) + out) & 0xFFFF
    return a


def with_checksum(b: bytes) -> bytes:
    c = checksum(b)
    n = c ^ 0xAAAA
    return bytes(b[:0xFC]) + bytes([c & 0xFF, c >> 8, n & 0xFF, n >> 8])


def is_valid(b: bytes) -> bool:
    return len(b) == 256 and with_checksum(b) == bytes(b)


def patch(b: bytes, settings: dict[int, int]) -> bytes:
    out = bytearray(b)
    for addr, value in settings.items():
        out[addr] = value
    return with_checksum(bytes(out))
