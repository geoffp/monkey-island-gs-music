# SPDX-License-Identifier: GPL-3.0-or-later
"""NinjaTrackerPlus .NTP files: split into sections, and check that a set of songs shares one
resident instrument bank.

Layout (vendor/ntp/src/file_format.txt, version 2), in file order:
  header      "nfc!", version, #tracks, #instruments, #patterns, order length, Pascal song name
  tracks      3 bytes per track: output channel, stream-buffer page, oscillator byte
  instruments 14 bytes + Pascal name each: type, length (3), repeat (3), repeat length (3),
              volume, finetune, DOC page, DOC wave size/resolution
  order       pattern numbers
  patterns    64 rows x tracks x 4 bytes
  samples     DOC-ready bytes, instrument order

For the shared sample-set mode (docs/ninjatrackerplus.md), every song's `instruments` and
`samples` sections are byte-identical. A game can load them once, copy the samples to DOC RAM,
and then load only each song's header + tracks + order + patterns.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NTP:
    version: int
    tracks: int
    instruments: list[bytes]          # each record incl. its name
    patterns: int
    header: bytes                     # up to and including the song name
    track_bytes: bytes
    instrument_bytes: bytes           # all instrument records
    order: bytes
    pattern_bytes: bytes
    samples: bytes

    @property
    def song_part(self) -> bytes:
        """What a game with a resident bank loads per song."""
        return self.header + self.track_bytes + self.order + self.pattern_bytes


def parse(d: bytes) -> NTP:
    if d[:4] != b"nfc!":
        raise ValueError("not an NTP file (no 'nfc!')")
    ver, ntr, nins, npat, nord = d[4], d[5], d[6], d[7], d[8]
    p = 9
    if ver >= 2:
        p += 1 + d[p]
    header = d[:p]
    tb = d[p:p + ntr * (3 if ver >= 2 else 2)]
    p += len(tb)
    i0, ins = p, []
    for _ in range(nins):
        q = p
        p += 14 if ver >= 1 else 8
        if ver >= 2:
            p += 1 + d[p]
        ins.append(d[q:p])
    ib = d[i0:p]
    order = d[p:p + nord]
    p += nord
    pb = d[p:p + 4 * ntr * 64 * npat]
    p += len(pb)
    lengths = [int.from_bytes(r[1:4], "little") for r in ins] if ver >= 1 else [int.from_bytes(r[1:3], "little") for r in ins]
    samples = d[p:]
    if len(samples) != sum(lengths):
        raise ValueError(f"sample data is {len(samples)} bytes, instruments say {sum(lengths)}")
    return NTP(ver, ntr, ins, npat, header, tb, ib, order, pb, samples)


def check_shared(files: list[Path]) -> list[str]:
    """Problems that would stop these songs sharing one resident bank (empty = compatible)."""
    if not files:
        return ["no .NTP files"]
    ref = parse(files[0].read_bytes())
    problems = []
    for f in files[1:]:
        x = parse(f.read_bytes())
        if x.instrument_bytes != ref.instrument_bytes:
            n = sum(a != b for a, b in zip(x.instruments, ref.instruments)) + abs(len(x.instruments) - len(ref.instruments))
            problems.append(f"{f.name}: {n} instrument records differ from {files[0].name}")
        if x.samples != ref.samples:
            problems.append(f"{f.name}: sample data differs from {files[0].name}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--shared", action="store_true", help="require one shared instrument bank")
    args = ap.parse_args(argv)
    for f in args.files:
        x = parse(f.read_bytes())
        print(f"{f.name}: {x.tracks} tracks, {len(x.instruments)} instruments, {x.patterns} patterns; "
              f"song part {len(x.song_part)} B, instruments {len(x.instrument_bytes)} B, samples {len(x.samples)} B")
    if args.shared:
        problems = check_shared(args.files)
        for pr in problems:
            print("NOT SHARED:", pr)
        if problems:
            return 1
        print(f"shared bank OK: {len(args.files)} songs, identical instruments + samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
