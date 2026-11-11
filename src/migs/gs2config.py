# SPDX-License-Identifier: GPL-3.0-or-later
"""Write GSSquared machine configs (.gs2) for this project.

GSSquared rewrites the .gs2 on quit (adding `id`, defaults, and battery RAM as `bram`), so we
keep an existing file's `id` and `bram`, regenerate everything else from the arguments, and
patch the Control Panel settings we need into the BRAM.

Format: GSSquared Docs/SystemConfigTOML.md. BazFast (`bazfast3`) is its SmartPort hard-disk
card with zero-cycle "Super DMA" transfers (Docs/Storage.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import NamedTuple

from . import bram

SPEEDS = ("1.024mhz", "2.8mhz", "7.159mhz", "14.3mhz", "ludicrous")
ID_RE = re.compile(r'^id\s*=\s*"([^"]*)"', re.M)


class Disk(NamedTuple):
    slot: int
    drive: int
    image: str          # path relative to the .gs2's directory


def _toml_str(s: str) -> str:
    """A TOML basic string. JSON's string escapes (\\\\, \\", \\n, \\uXXXX, ...) are all valid
    TOML, so json.dumps produces one."""
    return json.dumps(s, ensure_ascii=False)


def render_config(name: str, speed: str, cards: Mapping[int, str], disks: Iterable[Disk],
                  machine_id: str | None = None, bram_hex: str | None = None) -> str:
    """The .gs2 text: root keys first (TOML puts every key after a [[table]] header into
    that table, so `bram` must come before the cards), then one table per card and disk."""
    if speed not in SPEEDS:
        raise ValueError(f"speed must be one of {SPEEDS}")
    lines = ["gs2_version = 1"]
    if machine_id:
        lines.append(f"id = {_toml_str(machine_id)}")
    lines += [f"name = {_toml_str(name)}", 'platform = "apple2gs"', f"speed = {_toml_str(speed)}"]
    if bram_hex:
        lines.append(f"bram = {_toml_str(bram_hex)}")
    for slot, card in sorted(cards.items()):
        lines += ["", "[[cards]]", f"slot = {slot}", f"card = {_toml_str(card)}"]
    for slot, drive, image in disks:
        lines += ["", "[[storage]]", f"slot = {slot}", f"drive = {drive}", f"image = {_toml_str(image)}"]
    return "\n".join(lines) + "\n"


def write_config(path: Path, name: str, speed: str, cards: Mapping[int, str], disks: Iterable[Disk],
                 settings: Mapping[int, int], seed: bytes | None = None) -> bytes | None:
    """(Re)write `path`, keeping the `id` and BRAM GSSquared saved in it. The BRAM (the
    saved one if valid, else `seed`) gets `settings` patched in. Returns the BRAM written, or
    None if there was none to start from (GSSquared then writes its own on first quit)."""
    machine_id, b = None, None
    if path.exists():
        old = path.read_text()
        if m := ID_RE.search(old):
            machine_id = m.group(1)
        if m := bram.BRAM_RE.search(old):
            b = bytes.fromhex(m.group(1))
    if b is None or not bram.is_valid(b):
        b = seed
    if b is not None:
        b = bram.patch(b, settings)
    path.write_text(render_config(name, speed, cards, disks, machine_id, b.hex() if b else None))
    return b


# argparse `type=` converters: a ValueError becomes a clean "invalid value" usage error.

def _card(s: str) -> tuple[int, str]:
    slot, card = s.split("=", 1)
    return int(slot), card


def _disk(s: str) -> Disk:
    where, image = s.split("=", 1)
    slot, drive = where.split(":")
    return Disk(int(slot), int(drive), image)


def _read_hex(path: Path) -> bytes:
    return bytes.fromhex("".join(path.read_text().split()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gs2", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--speed", default="7.159mhz", choices=SPEEDS)
    ap.add_argument("--card", type=_card, action="append", default=[], metavar="SLOT=TYPE",
                    help="e.g. 7=bazfast3")
    ap.add_argument("--disk", type=_disk, action="append", default=[], metavar="SLOT:DRIVE=IMAGE",
                    help="image path relative to the .gs2's directory, e.g. 7:1=MI.NTP.PO")
    ap.add_argument("--startup-slot", type=int, choices=range(10), metavar="0-9",
                    help="BRAM startup slot (0 = scan, 1-7 = slot, 8 = RAM disk, 9 = ROM disk)")
    ap.add_argument("--your-card", type=int, action="append", default=[], choices=range(1, 8), metavar="SLOT",
                    help="BRAM: set slot to 'Your Card' (built-in device off / slot card used)")
    ap.add_argument("--seed", type=Path, help="hex BRAM image to use when the .gs2 has none")
    args = ap.parse_args(argv)

    settings = {bram.SLOT_INTERNAL[s]: 1 for s in args.your_card}
    if args.startup_slot is not None:
        settings[bram.STARTUP_SLOT] = args.startup_slot
    seed = _read_hex(args.seed) if args.seed else None
    if seed is not None and not bram.is_valid(seed):
        raise SystemExit(f"{args.seed}: not a valid {bram.SIZE}-byte BRAM image")

    cards = dict(args.card)
    args.gs2.parent.mkdir(parents=True, exist_ok=True)
    b = write_config(args.gs2, args.name, args.speed, cards, args.disk, settings, seed)
    note = "" if b else " (no BRAM yet: Control Panel settings apply after GSSquared's first save)"
    print(f"{args.gs2}: {args.speed}, cards {cards or '-'}, {len(args.disk)} disk(s){note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
