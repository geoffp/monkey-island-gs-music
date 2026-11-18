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
FX_SET_VOLUME = 0xC


SAMPLE_FIELDS = struct.Struct(">HBBHH")       # length (words), finetune, volume, loop start, loop length (words)
EMPTY_SAMPLE_HEADER = bytes(22) + SAMPLE_FIELDS.pack(0, 0, 0, 0, 1)


def pack_head(title: bytes, headers: list[bytes], patterns: int, tracks: int) -> bytes:
    """Everything before the pattern data: the title, the 31 sample headers (empty ones fill the
    rest), the song length and order list (`patterns` patterns once each) and the channel tag."""
    out = bytearray(title[:20].ljust(20, b"\0"))
    for i in range(MAX_SAMPLES):
        out += headers[i] if i < len(headers) else EMPTY_SAMPLE_HEADER
    out += bytes([patterns, 127]) + bytes(range(patterns)) + bytes(ORDERS - patterns)
    out += (f"{tracks}CHN" if tracks < 10 else f"{tracks:02d}CH").encode()
    return bytes(out)


def pack_cell(period: int, inst: int, eff: int, par: int) -> bytes:
    """A 4-byte cell: sample high nibble + 12-bit period, sample low nibble + effect + param."""
    return struct.pack(">HH", (inst & 0xF0) << 8 | period, (inst & 0x0F) << 12 | eff << 8 | par)


def _sample_header(s: ModSample, vmap: list[int] | None = None) -> bytes:
    words = (len(s.mod_bytes()) + 1) // 2
    loop_start, loop_len = (s.loop_start // 2, s.loop_len // 2) if s.loop_len else (0, 1)
    return (s.name.encode("latin-1")[:22].ljust(22, b"\0")
            + SAMPLE_FIELDS.pack(words, s.finetune & 0x0F, vmap[s.default_volume] if vmap else s.default_volume,
                                 loop_start, loop_len))


def _cell(note: int, inst: int, eff: int, par: int, periods: list[int]) -> bytes:
    """A cell from a note number (1-based into `periods`, 0 = none); an effect below 0 is no effect."""
    period = periods[note - 1] if note else 0
    eff, par = (0, 0) if eff < 0 else (eff, par)
    return pack_cell(period, inst, eff, par)


def mod_file(title: str, samples: list[ModSample], grid: Grid, patterns: int, tables,
             volume_map: list[int] | None = None) -> bytes:
    """The MOD bytes: `patterns` patterns played once each in order, notes as finetune-0 periods
    (`tables` from ntpsamples.period_tables; finetune lives in the sample headers).

    `volume_map` (65 entries, migs.docvolume) rewrites every volume on the way out, the samples'
    default volumes and each Cxx (set volume): the build plans in linear volumes, and this turns
    them into what the real DOC's curve needs."""
    if len(samples) > MAX_SAMPLES:
        raise ValueError(f"{len(samples)} samples > {MAX_SAMPLES}")
    tracks = len(grid.cells)
    out = bytearray(pack_head(title.encode("latin-1"), [_sample_header(s, volume_map) for s in samples],
                              patterns, tracks))
    periods = tables[0]
    empty = (0, 0, -1, 0)
    for row in range(patterns * ROWS):
        for tr in range(tracks):
            note, inst, eff, par = grid.cells[tr].get(row, empty)
            if volume_map and eff == FX_SET_VOLUME:
                par = volume_map[min(par, 64)]
            out += _cell(note, inst, eff, par, periods)
    for s in samples:
        raw = s.mod_bytes()
        out += raw + (b"\0" if len(raw) % 2 else b"")
    return bytes(out)
