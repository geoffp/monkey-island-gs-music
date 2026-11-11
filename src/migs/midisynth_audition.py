# SPDX-License-Identifier: GPL-3.0-or-later
"""Build MIDI Synth "audition" sequences: one hit per beat across a key range on one
bank slot, so each key of a drum kit (or any instrument) can be heard in synthLAB.

synthLAB's counter shows measure:beat; the companion .txt maps measure:beat -> key.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .midisynth import TRACK_ON, TRACKS, Bank, SeqItem, Sequence, WaveBank

BEATS_PER_MEASURE = 4
TICKS_PER_BEAT = 96
LEAD_IN_BEATS = BEATS_PER_MEASURE    # one measure before the first hit
TEMPO = 30                           # half-note BPM: 60 quarter BPM, one hit per second


def _zone_label(bank: Bank | None, slot: int, key: int,
                wave_names: dict[int, tuple[str, int]] | None) -> str:
    """The wave a key plays (by name, else its DOC page), "silent" outside every zone, or
    "" without a bank to look in."""
    if bank is None:
        return ""
    wl = next((wl for lo, hi, wl in bank.instruments[slot].zones() if lo <= key <= hi), None)
    if wl is None:
        return "silent"
    return (wave_names or {}).get(wl.addr_a, (f"page ${wl.addr_a:02X}",))[0]


def audition(bank_file: str, slot: int, lo: int, hi: int, bank: Bank | None = None,
             wave_names: dict[int, tuple[str, int]] | None = None,
             velocity: int = 100, gap_beats: int = 1) -> tuple[Sequence, list[str]]:
    """The sequence (keys lo..hi on `slot`, a hit every `gap_beats`, each held half a beat)
    and its measure:beat -> key table."""
    items, table = [], []
    for i, key in enumerate(range(lo, hi + 1)):
        beat = LEAD_IN_BEATS + i * gap_beats
        t = beat * TICKS_PER_BEAT
        items.append(SeqItem(0, t, 0x90 | slot, bytes([key, velocity])))
        items.append(SeqItem(0, t + TICKS_PER_BEAT // 2, 0x80 | slot, bytes([key, 0x40])))
        measure, beat_in = divmod(beat, BEATS_PER_MEASURE)
        table.append(f"{measure + 1:3d}:{beat_in + 1}  key {key:3d}  {_zone_label(bank, slot, key, wave_names)}")
    seq = Sequence(bank_name=bank_file, items=items, tempo=TEMPO,
                   track_names=[f"Audition s{slot}"] + [""] * (TRACKS - 1),
                   track_play=[TRACK_ON] + [0] * (TRACKS - 1))
    return seq, table


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bank", type=Path, help="bank file (.BNK); its name is referenced by the sequence")
    ap.add_argument("slot", type=int, choices=range(16), metavar="SLOT", help="instrument slot 0-15")
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
    header = (f"{stem}.SEQ: {args.bank.name} slot {args.slot} ({name}), keys {args.lo}-{args.hi}, "
              f"one hit per beat after a 1-measure lead-in\nmeasure:beat  key  zone sample")
    (args.out_dir / f"{stem}.txt").write_text("\n".join([header, *table]) + "\n")
    print(f"{stem}.SEQ: slot {args.slot} {name}, keys {args.lo}-{args.hi}, {seq.seconds():.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
