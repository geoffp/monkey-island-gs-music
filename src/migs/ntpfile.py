# SPDX-License-Identifier: GPL-3.0-or-later
"""NinjaTrackerPlus .NTP files: split into sections, and check that a set of songs shares one
resident instrument bank.

Layout (vendor/ntp/src/file_format.txt, versions 0-2), in file order:
  header      "nfc!", version, #tracks, #instruments, #patterns, order length,
              [v2] Pascal song name
  tracks      per track: output channel, [v1+] stream-buffer page, [v2] oscillator byte
  instruments per instrument: type, length (v0: 2 bytes, v1+: 3), [v1+] repeat (3) and
              repeat length (3), volume, finetune, DOC page, DOC wave size/resolution,
              [v2] Pascal name
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

MAGIC = b"nfc!"
ROWS, CELL = 64, 4                  # pattern rows; bytes per cell


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


class _Reader:
    """A cursor over the file that fails clearly when a section runs past the end."""

    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, n: int, what: str) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError(f"truncated {what} at offset {self.pos}")
        self.pos += n
        return self.data[self.pos - n:self.pos]

    def pascal(self, what: str) -> bytes:
        """A Pascal string, its length byte included."""
        n = self.take(1, what)[0]
        return bytes([n]) + self.take(n, what)


def parse(d: bytes) -> NTP:
    if d[:4] != MAGIC:
        raise ValueError("not an NTP file (no 'nfc!')")
    r = _Reader(d)
    r.take(9, "header")
    ver, ntr, nins, npat, nord = d[4:9]
    if ver > 2:
        raise ValueError(f"unknown NTP version {ver}")
    if ver >= 2:
        r.pascal("song name")
    header = d[:r.pos]
    track_bytes = r.take(ntr * (1 + ver), "track data")      # +1 byte per version
    length_size = 3 if ver >= 1 else 2
    fixed = 1 + length_size + (6 if ver >= 1 else 0) + 4     # type, length, [repeat], 4 bytes
    start, instruments, lengths = r.pos, [], []
    for i in range(nins):
        rec = r.take(fixed, f"instrument {i + 1}")
        if ver >= 2:
            rec += r.pascal(f"instrument {i + 1} name")
        instruments.append(rec)
        lengths.append(int.from_bytes(rec[1:1 + length_size], "little"))
    instrument_bytes = d[start:r.pos]
    order = r.take(nord, "pattern order")
    pattern_bytes = r.take(CELL * ntr * ROWS * npat, "pattern data")
    samples = d[r.pos:]
    if len(samples) != sum(lengths):
        raise ValueError(f"sample data is {len(samples)} bytes, instruments say {sum(lengths)}")
    return NTP(ver, ntr, instruments, npat, header, track_bytes, instrument_bytes, order,
               pattern_bytes, samples)


def check_shared(files: list[Path]) -> list[str]:
    """Problems that would stop these songs sharing one resident bank (empty = compatible)."""
    if not files:
        return ["no .NTP files"]
    ref = parse(files[0].read_bytes())
    problems = []
    for f in files[1:]:
        x = parse(f.read_bytes())
        if x.instrument_bytes != ref.instrument_bytes:
            n = (sum(a != b for a, b in zip(x.instruments, ref.instruments))
                 + abs(len(x.instruments) - len(ref.instruments)))
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
