# SPDX-License-Identifier: GPL-3.0-or-later
"""Write a ProTracker MOD with up to 31 samples and any number of channels.

Layout (all big-endian): 20-byte title; 31 sample headers (22-byte name, length in words,
finetune, volume, loop start and loop length in words); song length, restart byte (127),
128-entry order list; the channel tag ("nCHN" below 10 channels, "nnCH" from 10, which
trackers and NTP's converter expect); patterns of 64 rows x channels x 4-byte cells; sample
data, each padded to an even length.
"""

from __future__ import annotations

import struct

from .ntpsamples import ModSample
from .ntpsong import ROWS, Grid

MAX_SAMPLES, ORDERS = 31, 128


def _sample_header(s: ModSample | None) -> bytes:
    if s is None:
        return bytes(22) + struct.pack(">HBBHH", 0, 0, 0, 0, 1)
    words = (len(s.mod_bytes()) + 1) // 2
    loop_start, loop_len = (s.loop_start // 2, s.loop_len // 2) if s.loop_len else (0, 1)
    return (s.name.encode("latin-1")[:22].ljust(22, b"\0")
            + struct.pack(">HBBHH", words, s.finetune & 0x0F, s.default_volume, loop_start, loop_len))


def _cell(note: int, inst: int, eff: int, par: int, periods: list[int]) -> bytes:
    """A 4-byte cell: sample high nibble + 12-bit period, sample low nibble + effect + param."""
    period = periods[note - 1] if note else 0
    eff, par = (0, 0) if eff < 0 else (eff, par)
    return struct.pack(">HH", (inst & 0xF0) << 8 | period, (inst & 0x0F) << 12 | eff << 8 | par)


def mod_file(title: str, samples: list[ModSample], grid: Grid, patterns: int, tables) -> bytes:
    """The MOD bytes: `patterns` patterns played once each in order, notes as finetune-0 periods
    (`tables` from ntpsamples.period_tables; finetune lives in the sample headers)."""
    if len(samples) > MAX_SAMPLES:
        raise ValueError(f"{len(samples)} samples > {MAX_SAMPLES}")
    tracks = len(grid.cells)
    out = bytearray(title.encode("latin-1")[:20].ljust(20, b"\0"))
    for i in range(MAX_SAMPLES):
        out += _sample_header(samples[i] if i < len(samples) else None)
    out += bytes([patterns, 127]) + bytes(range(patterns)) + bytes(ORDERS - patterns)
    out += (f"{tracks}CHN" if tracks < 10 else f"{tracks:02d}CH").encode()
    periods = tables[0]
    empty = (0, 0, -1, 0)
    for row in range(patterns * ROWS):
        for tr in range(tracks):
            out += _cell(*grid.cells[tr].get(row, empty), periods)
    for s in samples:
        raw = s.mod_bytes()
        out += raw + (b"\0" if len(raw) % 2 else b"")
    return bytes(out)
