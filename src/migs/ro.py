"""Parser for the SCUMM v3/v4 'RO' (Roland MT-32) music stream.

Grammar follows ScummVM engines/scumm/midiparser_ro.cpp; see docs/ro-format.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PPQN = 120
TEMPO_US = 500_000  # µs per quarter note


@dataclass(frozen=True)
class Event:
    tick: int
    kind: str      # "note_on" | "note_off" | "cc" | "program" | "marker"
    channel: int   # 0-based MIDI channel (for markers: the low nibble of the Ax byte)
    a: int = 0     # note / controller / program
    b: int = 0     # velocity / value


@dataclass
class Track:
    events: list[Event] = field(default_factory=list)
    end_tick: int = 0
    loops: bool = False        # ended with FF (ScummVM: autoLoop)
    terminated: bool = False   # ended with 00 or FF (vs. running off the block end)


def parse(data: bytes) -> Track:
    """Parse an RO payload (the bytes after the 6-byte block header)."""
    t = Track()
    tick = 0
    i = 0
    n = len(data)

    def need(k: int) -> None:
        if i + k > n:
            raise ValueError(f"truncated event at offset {i - 1}")

    while i < n:
        status = data[i]
        i += 1
        if status in (0xF0, 0xF1):          # delay
            need(1)
            tick += data[i] + (256 if status == 0xF1 else 0)
            i += 1
            continue
        if status in (0x00, 0xFF):          # end of track; FF = loop
            t.terminated = True
            t.loops = status == 0xFF
            break
        if status < 0x80:
            raise ValueError(f"data byte {status:#04x} without status at offset {i - 1}")
        cmd, ch = status >> 4, status & 0x0F
        if cmd == 0xA:                      # one-byte marker
            t.events.append(Event(tick, "marker", ch))
        elif cmd == 0xC:
            need(1)
            t.events.append(Event(tick, "program", ch, data[i]))
            i += 1
        elif cmd in (0x8, 0x9, 0xB):
            need(2)
            a, b = data[i], data[i + 1]
            i += 2
            if cmd == 0x9 and b > 0:
                t.events.append(Event(tick, "note_on", ch, a, b))
            elif cmd in (0x8, 0x9):
                t.events.append(Event(tick, "note_off", ch, a, b if cmd == 0x8 else 0))
            else:
                t.events.append(Event(tick, "cc", ch, a, b))
        else:
            raise ValueError(f"unsupported status {status:#04x} at offset {i - 1}")
    t.end_tick = tick
    return t
