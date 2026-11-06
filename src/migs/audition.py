# SPDX-License-Identifier: GPL-3.0-or-later
"""Build MIDI Synth "audition" sequences: one hit per beat across a key range on one
bank slot, so each key of a drum kit (or any instrument) can be heard in synthLAB.

synthLAB's counter shows measure:beat; the companion .txt maps measure:beat -> key.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .midisynth import TRACK_ON, Bank, SeqItem, Sequence, WaveBank

BEATS_PER_MEASURE = 4
TICKS_PER_BEAT = 96


def audition(bank_file: str, slot: int, lo: int, hi: int, bank: Bank | None = None,
             wave_names: dict[int, tuple[str, int]] | None = None,
             velocity: int = 100, gap_beats: int = 1) -> tuple[Sequence, list[str]]:
    items, table = [], []
    zones = bank.instruments[slot].zones() if bank else []
    for i, key in enumerate(range(lo, hi + 1)):
        t = (i * gap_beats + BEATS_PER_MEASURE) * TICKS_PER_BEAT   # one measure of lead-in
        items.append(SeqItem(0, t, 0x90 | slot, bytes([key, velocity])))
        items.append(SeqItem(0, t + TICKS_PER_BEAT // 2, 0x80 | slot, bytes([key, 0x40])))
        beat = i * gap_beats + BEATS_PER_MEASURE
        zone = next((wl for zlo, zhi, wl in zones if zlo <= key <= zhi), None)
        if zone:
            label = (wave_names or {}).get(zone.addr_a, ("page $%02X" % zone.addr_a,))[0]
        else:
            label = "silent" if bank else ""
        table.append(f"{beat // BEATS_PER_MEASURE + 1:3d}:{beat % BEATS_PER_MEASURE + 1}  key {key:3d}  {label}")
    seq = Sequence(bank_name=bank_file, items=items, tempo=30)     # 60 quarter BPM: 1 hit/second
    seq.track_names = [f"Audition s{slot}"] + [""] * 15
    seq.track_play = [TRACK_ON] + [0] * 15
    return seq, table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bank", type=Path, help="bank file (.BNK); its name is referenced by the sequence")
    ap.add_argument("slot", type=int, help="instrument slot 0-15")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--lo", type=int, default=30)
    ap.add_argument("--hi", type=int, default=100)
    ap.add_argument("--wave", type=Path, help="wave file, to label zones by wave name")
    args = ap.parse_args(argv)
    bank = Bank.from_bytes(args.bank.read_bytes())
    names = WaveBank.from_bytes(args.wave.read_bytes()).waves if args.wave else None
    seq, table = audition(args.bank.name.upper(), args.slot, args.lo, args.hi, bank, names)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"AUDIT{args.slot:02d}"
    (args.out_dir / f"{stem}.SEQ").write_bytes(seq.to_bytes())
    name = bank.instruments[args.slot].name
    (args.out_dir / f"{stem}.txt").write_text(
        f"{stem}.SEQ: {args.bank.name} slot {args.slot} ({name}), keys {args.lo}-{args.hi}, "
        f"one hit per beat after a 1-measure lead-in\nmeasure:beat  key  zone sample\n" + "\n".join(table) + "\n")
    print(f"{stem}.SEQ: slot {args.slot} {name}, keys {args.lo}-{args.hi}, {seq.seconds():.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
