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
import re
import sys
from pathlib import Path

from . import bram

SPEEDS = ("1.024mhz", "2.8mhz", "7.159mhz", "14.3mhz", "ludicrous")


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(name: str, speed: str, cards: dict[int, str], disks: list[tuple[int, int, str]],
           machine_id: str | None = None, bram_hex: str | None = None) -> str:
    if speed not in SPEEDS:
        raise ValueError(f"speed must be one of {SPEEDS}")
    out = ["gs2_version = 1"]
    if machine_id:
        out.append(f"id = {_toml_str(machine_id)}")
    out += [f"name = {_toml_str(name)}", 'platform = "apple2gs"', f"speed = {_toml_str(speed)}"]
    if bram_hex:
        out.append(f"bram = {_toml_str(bram_hex)}")      # root key: before any [[table]]
    for slot, card in sorted(cards.items()):
        out += ["", "[[cards]]", f"slot = {slot}", f"card = {_toml_str(card)}"]
    for slot, drive, image in disks:
        out += ["", "[[storage]]", f"slot = {slot}", f"drive = {drive}", f"image = {_toml_str(image)}"]
    return "\n".join(out) + "\n"


def write_config(path: Path, name: str, speed: str, cards: dict[int, str],
                 disks: list[tuple[int, int, str]], settings: dict[int, int],
                 seed: bytes | None = None) -> bytes | None:
    """(Re)write `path`, keeping its id and BRAM; returns the BRAM written (or None)."""
    machine_id, b = None, None
    if path.exists():
        old = path.read_text()
        if m := re.search(r'^id\s*=\s*"([^"]*)"', old, re.M):
            machine_id = m.group(1)
        if m := bram.BRAM_RE.search(old):
            b = bytes.fromhex(m.group(1))
    if b is None or not bram.is_valid(b):
        b = seed
    if b is not None:
        b = bram.patch(b, settings)
    path.write_text(render(name, speed, cards, disks, machine_id, b.hex() if b else None))
    return b


def _slot_kv(s: str) -> tuple[int, str]:
    k, v = s.split("=", 1)
    return int(k), v


def _disk(s: str) -> tuple[int, int, str]:
    where, image = s.split("=", 1)
    slot, drive = where.split(":")
    return int(slot), int(drive), image


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gs2", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--speed", default="ludicrous", choices=SPEEDS)
    ap.add_argument("--card", type=_slot_kv, action="append", default=[], metavar="SLOT=TYPE",
                    help="e.g. 7=bazfast3")
    ap.add_argument("--disk", type=_disk, action="append", default=[], metavar="SLOT:DRIVE=IMAGE",
                    help="image path relative to the .gs2's directory, e.g. 7:1=MI.HD.PO")
    ap.add_argument("--startup-slot", type=int, help="BRAM startup slot (0 = scan)")
    ap.add_argument("--your-card", type=int, action="append", default=[], metavar="SLOT",
                    help="BRAM: set slot to 'Your Card' (built-in device off / slot card used)")
    ap.add_argument("--seed", type=Path, help="hex BRAM image to use when the .gs2 has none")
    args = ap.parse_args(argv)
    settings = {bram.SLOT_INTERNAL[s]: 1 for s in args.your_card}
    if args.startup_slot is not None:
        settings[bram.STARTUP_SLOT] = args.startup_slot
    seed = bytes.fromhex("".join(args.seed.read_text().split())) if args.seed else None
    if seed is not None and not bram.is_valid(seed):
        raise SystemExit(f"{args.seed}: not a valid 256-byte BRAM image")
    args.gs2.parent.mkdir(parents=True, exist_ok=True)
    b = write_config(args.gs2, args.name, args.speed, dict(args.card), args.disk, settings, seed)
    note = "" if b else " (no BRAM yet: Control Panel settings apply after GSSquared's first save)"
    print(f"{args.gs2}: {args.speed}, cards {dict(args.card) or '-'}, "
          f"{len(args.disk)} disk(s){note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
