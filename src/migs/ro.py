# SPDX-License-Identifier: GPL-3.0-or-later
"""Parser for the SCUMM v3/v4 'RO' (Roland MT-32) music stream.

Grammar follows ScummVM engines/scumm/midiparser_ro.cpp; see docs/ro-format.md:

    F0 n          delay n ticks          F1 n   delay 256 + n ticks
    8c k v        note off               9c k v note on (v = 0: note off)
    Bc c v        control change         Cc p   program change
    Ac            marker (one byte)      00     end          FF  end, loop

There is no running status: every event starts with a status byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

PPQN = 120
TEMPO_US = 500_000  # µs per quarter note: RO streams have no tempo events


class Kind(StrEnum):
    NOTE_ON = "note_on"
    NOTE_OFF = "note_off"
    CC = "cc"
    PROGRAM = "program"
    MARKER = "marker"
    TIMBRE = "timbre"          # iMUSE (SCUMM v5): the channel's custom MT-32 timbre `a` (an id >= 128)


@dataclass(frozen=True)
class Event:
    tick: int
    kind: Kind
    channel: int   # 0-based MIDI channel (for markers: the low nibble of the Ax byte)
    a: int = 0     # note / controller / program
    b: int = 0     # velocity / value


@dataclass
class Track:
    """A song as one stream of events. RO tracks use the defaults (120 PPQN, 120 BPM, loops
    from the start); the iMUSE reader (imuse.py) fills in the rest."""
    events: list[Event] = field(default_factory=list)
    end_tick: int = 0          # includes any delay before the terminator (loop length)
    loops: bool = False        # ended with FF (ScummVM: autoLoop)
    terminated: bool = False   # ended with 00 or FF (vs. running off the block end)
    ppqn: int = PPQN
    tempo_us: int = TEMPO_US   # the tempo at tick 0
    tempos: list[tuple[int, int]] = field(default_factory=list)   # later (tick, µs per quarter)
    loop_start: int = 0        # where a loop goes back to (tick)


def ticks_to_seconds(ticks: int) -> float:
    return ticks / PPQN * TEMPO_US / 1e6


DELAYS = {0xF0: 0, 0xF1: 256}             # status -> ticks added to the delay byte
END, END_LOOP = 0x00, 0xFF
PARAMS = {0x8: 2, 0x9: 2, 0xB: 2, 0xC: 1, 0xA: 0}   # command nibble -> parameter bytes


def parse(data: bytes) -> Track:
    """Parse an RO payload (the bytes after the 6-byte block header). Parsing stops at the
    first terminator; a stream without one runs to the end of the data."""
    track = Track()
    tick = i = 0
    while i < len(data):
        at, status = i, data[i]
        i += 1
        if status in (END, END_LOOP):
            track.terminated, track.loops = True, status == END_LOOP
            break
        if status < 0x80:
            raise ValueError(f"data byte {status:#04x} without status at offset {at}")
        cmd, ch = status >> 4, status & 0x0F
        n = 1 if status in DELAYS else PARAMS.get(cmd)
        if n is None:
            raise ValueError(f"unsupported status {status:#04x} at offset {at}")
        if i + n > len(data):
            raise ValueError(f"truncated event at offset {at}")
        p, i = data[i:i + n], i + n

        if status in DELAYS:
            tick += DELAYS[status] + p[0]
        elif cmd == 0xA:
            track.events.append(Event(tick, Kind.MARKER, ch))
        elif cmd == 0xC:
            track.events.append(Event(tick, Kind.PROGRAM, ch, p[0]))
        elif cmd == 0xB:
            track.events.append(Event(tick, Kind.CC, ch, p[0], p[1]))
        elif cmd == 0x9 and p[1] > 0:
            track.events.append(Event(tick, Kind.NOTE_ON, ch, p[0], p[1]))
        else:                                   # 8x, or 9x with velocity 0
            track.events.append(Event(tick, Kind.NOTE_OFF, ch, p[0], p[1] if cmd == 0x8 else 0))
    track.end_tick = tick
    return track
