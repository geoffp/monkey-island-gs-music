# SPDX-License-Identifier: GPL-3.0-or-later
"""Apple IIGS battery RAM (BRAM) in GSSquared .gs2 machine configs.

GSSquared stores the 256-byte BRAM as `bram = "<512 hex digits>"` in the .gs2 and writes it
back on quit. This tool patches Control Panel settings and fixes the checksum, so the IIgs
accepts them (an invalid checksum makes the ROM reset BRAM to defaults).

Layout and checksum: GSSquared Docs/GSClockBatteryRAM.md (ROM routine BCheckSum).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gs2", type=Path)
    ap.add_argument("--startup-slot", type=int, help="0 = scan, 1-7 = slot")
    ap.add_argument("--your-card", type=int, action="append", default=[], metavar="SLOT",
                    help="set slot to 'Your Card' (disables the built-in device), repeatable")
    ap.add_argument("--seed", type=Path,
                    help="hex BRAM image to insert when the .gs2 has none yet (e.g. first run)")
    args = ap.parse_args(argv)
    text = args.gs2.read_text()
    m = BRAM_RE.search(text)
    if not m and args.seed:
        seed = bytes.fromhex("".join(args.seed.read_text().split()))
        if not is_valid(seed):
            raise SystemExit(f"{args.seed}: not a valid 256-byte BRAM image")
        # bram is a root key and must come before any [[table]] (GSSquared SystemConfigTOML.md)
        cut = text.find("\n[[")
        cut = len(text) if cut < 0 else cut + 1
        text = text[:cut] + f'bram = "{seed.hex()}"\n\n' + text[cut:]
        m = BRAM_RE.search(text)
    if not m:
        print(f"{args.gs2}: no bram yet (GSSquared saves it on first quit); settings apply next run")
        return 0
    old = bytes.fromhex(m.group(1))
    settings = {SLOT_INTERNAL[s]: 1 for s in args.your_card}
    if args.startup_slot is not None:
        settings[STARTUP_SLOT] = args.startup_slot
    new = patch(old, settings)
    if new != old or m.group(1) not in args.gs2.read_text():
        args.gs2.write_text(text[: m.start(1)] + new.hex() + text[m.end(1):])
        print(f"{args.gs2}: bram updated " + ", ".join(f"${a:02X}={v}" for a, v in sorted(settings.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
