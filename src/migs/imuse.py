# SPDX-License-Identifier: GPL-3.0-or-later
"""iMUSE MT-32 songs (SCUMM v5: Monkey Island 2, Fate of Atlantis) as one linear event stream.

A sound's `ROL ` block is an `MDhd` header and a Standard MIDI File (format 2, 480 PPQN)
whose meaning lives in iMUSE SysEx (manufacturer 0x7D) and Roland SysEx (0x41). This plays
it the way ScummVM's iMUSE player does (`ref/scummvm/engines/scumm/imuse/`: imuse_player.cpp,
imuse_part.cpp, sysex_scumm.cpp) and writes down what an MT-32 would receive:

- a *part* per MIDI channel: on/off, volume, pan, transpose (the part's and the song's,
  clamped to +-12 as the MI2/Indy4 MT-32 driver does), percussion, program;
- `7D 00` allocates a part (these settings and a program); `7D 01` shuts it down;
- `7D 30` hook jumps go to (track, beat, tick); only unconditional ones (hook id 0) are
  followed, since a non-zero id waits for a game script. A jump stops the sounding notes
  (MidiParser::jumpToTick). A jump back into what has already played is the song's loop;
- `7D 31`-`35` hooks (global transpose, part on/off, volume, program, part transpose) are
  applied when their id is 0, for the same reason;
- `7D 50` counted loops repeat a span the given number of times; MI2's count 100 or 1000
  (hours), so one of `FOREVER` passes or more is taken as the song's loop;
- `41 pp ...` uploads a custom timbre for part pp: a Roland DT1 of a whole 246-byte timbre
  (Instrument_Roland). It becomes a TIMBRE event with the timbre's id (`timbre_ids`);
- percussion parts (channel 9, or flagged by `7D 00`) all play on the rhythm channel, and a
  rhythm note carries its part's volume (Part::noteOn: `_rhyState.vol`).

Not followed: pitch bend, detune, modulation and the effect level (the NTP pipeline has no
use for them), hooks that wait for scripts, and the MT-32 part allocation by priority
(iMUSE plays at most 8 melodic parts at once on an MT-32; this keeps every part).
Format notes: docs/imuse-format.md.
"""

from __future__ import annotations

import hashlib
import struct
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .ro import Event, Kind, Track

IMUSE, ROLAND = 0x7D, 0x41
TICKS_PER_BEAT = 480
RHYTHM = 9
TIMBRE_BYTES = 246                 # one MT-32 timbre (common + 4 partials)
ROLAND_TIMBRE_LEN = 254            # 41 pp 16 12 aa aa aa <246 bytes> checksum
FOREVER = 2                        # a counted loop (7D 50) of this many passes or more is the song's loop (MI2's count 100 or 1000)
MAX_TICKS = 480 * 2 * 60 * 30      # half an hour at 120 BPM: give up on a song that never ends or loops
PASSED_CCS = (1, 7, 10, 64)        # modulation, volume, pan, sustain


@dataclass(frozen=True)
class Timbre:
    name: str                      # the timbre's own name (10 characters, trailing spaces dropped)
    data: bytes                    # the 246 timbre bytes

    @property
    def key(self) -> str:
        return hashlib.sha1(self.data).hexdigest()[:8]


@dataclass
class SmfEvent:
    tick: int
    status: int                    # 0x80-0xEF channel message, 0xF0 SysEx, 0xFF meta
    data: bytes                    # SysEx: from the manufacturer byte; meta: type byte + data


def read_timbres(path: Path) -> dict[int, Timbre]:
    """A game's timbres.toml (extract_music writes it): program id -> timbre; {} when there is
    none (a game without custom timbres)."""
    if not path.is_file():
        return {}
    return {e["program"]: Timbre(e["name"], bytes.fromhex(e["data"]))
            for e in tomllib.loads(path.read_text()).get("timbre", [])}


def mdhd_smf(rol: bytes) -> bytes:
    """The Standard MIDI File in a ROL block (after its MDhd header, if any)."""
    i = rol.find(b"MThd")
    if i < 0:
        raise ValueError("no MThd in the ROL block")
    return rol[i:]


def read_smf(smf: bytes) -> tuple[int, list[list[SmfEvent]]]:
    """(division, tracks) with absolute ticks. Tolerant of what mido rejects (MI2 has SMPTE
    offsets with frame-rate code 4); running status is supported."""
    if smf[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hl = struct.unpack_from(">I", smf, 4)[0]
    division = struct.unpack_from(">H", smf, 12)[0]
    p, tracks = 8 + hl, []
    while p + 8 <= len(smf) and smf[p:p + 4] == b"MTrk":
        n = struct.unpack_from(">I", smf, p + 4)[0]
        d, p = smf[p + 8:p + 8 + n], p + 8 + n
        evs: list[SmfEvent] = []
        i = t = 0
        run = 0

        def vlq() -> int:
            nonlocal i
            v = 0
            while True:
                c = d[i]
                i += 1
                v = (v << 7) | (c & 0x7F)
                if not c & 0x80:
                    return v
        while i < len(d):
            t += vlq()
            st = d[i]
            if st & 0x80:
                i += 1
            elif run:
                st = run
            else:
                raise ValueError(f"data byte without status at {i}")
            if st == 0xFF:
                ty = d[i]
                i += 1
                ln = vlq()
                evs.append(SmfEvent(t, st, bytes([ty]) + d[i:i + ln]))
                i += ln
                if ty == 0x2F:
                    break
            elif st in (0xF0, 0xF7):
                ln = vlq()
                body = d[i:i + ln]
                evs.append(SmfEvent(t, st, body[:-1] if body.endswith(b"\xf7") else body))
                i += ln
            else:
                run = st
                k = 1 if st >> 4 in (0xC, 0xD) else 2
                evs.append(SmfEvent(t, st, d[i:i + k]))
                i += k
        tracks.append(evs)
    return division, tracks


def decode(src: bytes) -> bytes:
    """iMUSE SysEx payload bytes: each byte is sent as two nibbles (Player::decode_sysex_bytes)."""
    return bytes(((src[j] << 4) & 0xFF) | (src[j + 1] & 0xF) for j in range(0, len(src) - 1, 2))


def roland_timbre(sysex: bytes) -> Timbre | None:
    """The timbre in a Roland custom-instrument SysEx (from the 0x41 byte), else None."""
    if len(sysex) < 7 + TIMBRE_BYTES or sysex[0] != ROLAND:
        return None
    data = sysex[7:7 + TIMBRE_BYTES]
    return Timbre(data[:10].decode("latin1").rstrip(), data)


def timbres(smf: bytes) -> list[Timbre]:
    """Every custom timbre a song uploads, in order of appearance."""
    _, tracks = read_smf(smf)
    out: list[Timbre] = []
    for evs in tracks:
        for e in evs:
            if e.status == 0xF0 and (tb := roland_timbre(e.data)) and tb not in out:
                out.append(tb)
    return out


def _s8(b: int) -> int:
    return b - 256 if b > 127 else b


@dataclass
class _Part:
    on: bool = True
    percussion: bool = False
    volume: int = 127
    transpose: int = 0             # the part's own (-128: none)


@dataclass
class _Segment:
    track: int
    start: int                     # file ticks
    end: int | None
    out: int                       # output tick of `start`


@dataclass
class Reader:
    """Plays one song (see the module docstring). `timbre_ids` maps timbre key -> id."""
    timbre_ids: dict[str, int]
    track: Track = field(default_factory=lambda: Track(ppqn=TICKS_PER_BEAT))
    parts: dict[int, _Part] = field(default_factory=dict)
    transpose: int = 0             # the song's (global) transpose
    sounding: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)  # (part, key) -> (ch, key)
    rhythm_volume: int = -1
    ignored: dict[str, int] = field(default_factory=dict)

    def part(self, ch: int) -> _Part:
        if ch not in self.parts:
            self.parts[ch] = _Part(percussion=ch == RHYTHM)
        return self.parts[ch]

    def emit(self, tick: int, kind: Kind, ch: int, a: int = 0, b: int = 0) -> None:
        self.track.events.append(Event(tick, kind, ch, a, b))

    def out_channel(self, ch: int) -> int:
        return RHYTHM if self.part(ch).percussion else (15 if ch == RHYTHM else ch)

    def notes_off(self, tick: int, only: int | None = None) -> None:
        for (pch, key), (ch, k) in list(self.sounding.items()):
            if only is None or pch == only:
                self.emit(tick, Kind.NOTE_OFF, ch, k, 0)
                del self.sounding[(pch, key)]

    def eff_transpose(self, p: _Part) -> int:
        if p.transpose == -128:
            return 0
        return max(-12, min(12, p.transpose + self.transpose))

    def skip(self, what: str) -> None:
        self.ignored[what] = self.ignored.get(what, 0) + 1

    def channel_event(self, out: int, e: SmfEvent) -> None:
        cmd, ch = e.status >> 4, e.status & 0x0F
        p = self.part(ch)
        if cmd == 0x9 and e.data[1] > 0:
            if not p.on:
                return
            key = e.data[0] if p.percussion else e.data[0] + self.eff_transpose(p)
            if not 0 <= key < 128:
                return
            och = self.out_channel(ch)
            if p.percussion and p.volume != self.rhythm_volume:
                self.emit(out, Kind.CC, RHYTHM, 7, p.volume)
                self.rhythm_volume = p.volume
            if (ch, e.data[0]) in self.sounding:              # struck again: the MT-32 retriggers
                old = self.sounding.pop((ch, e.data[0]))
                self.emit(out, Kind.NOTE_OFF, *old, 0)
            self.emit(out, Kind.NOTE_ON, och, key, e.data[1])
            self.sounding[(ch, e.data[0])] = (och, key)
        elif cmd in (0x8, 0x9):
            if (ch, e.data[0]) in self.sounding:
                self.emit(out, Kind.NOTE_OFF, *self.sounding.pop((ch, e.data[0])), e.data[1] if cmd == 0x8 else 0)
        elif cmd == 0xB:
            c, v = e.data
            if c == 7:
                p.volume = v
                if not p.percussion:
                    self.emit(out, Kind.CC, self.out_channel(ch), 7, v)
            elif c in PASSED_CCS and not p.percussion:
                self.emit(out, Kind.CC, self.out_channel(ch), c, v)
            else:
                self.skip(f"CC{c}")
        elif cmd == 0xC:
            if not p.percussion:
                self.emit(out, Kind.PROGRAM, self.out_channel(ch), e.data[0])
        elif cmd == 0xE:
            self.skip("pitch bend")
        else:
            self.skip(f"status {e.status:#x}")

    def allocate(self, out: int, d: bytes) -> None:
        """7D 00: part settings (sysex_scumm.cpp case 0)."""
        ch = d[0] & 0x0F
        b = bytes(1) + decode(d[1:])
        p = self.part(ch)
        p.on, p.percussion = bool(b[1] & 1), bool(b[5] & 0x80)
        p.volume, p.transpose = b[3], _s8(b[5])
        if not p.on:
            self.notes_off(out, ch)
        if not p.percussion:
            och = self.out_channel(ch)
            self.emit(out, Kind.CC, och, 7, b[3])
            self.emit(out, Kind.CC, och, 10, max(0, min(127, _s8(b[4]) + 64)))
            self.emit(out, Kind.PROGRAM, och, b[8])

    def hook(self, out: int, code: int, d: bytes) -> None:
        """7D 31-35 with hook id 0 (unconditional)."""
        if code == 0x31:
            b = decode(d[1:])
            if b[0]:
                return self.skip("hook (script)")
            rel, v = b[1], _s8(b[2])
            if -24 <= v <= 24 and rel <= 1:
                self.transpose = max(-7, min(7, self.transpose + v)) if rel else v
            return
        ch, b = d[0] & 0x0F, decode(d[1:])
        if b[0]:
            return self.skip("hook (script)")
        p = self.part(ch)
        if code == 0x32:
            p.on = b[1] != 0
            if not p.on:
                self.notes_off(out, ch)
        elif code == 0x33:
            p.volume = b[1]
            if not p.percussion:
                self.emit(out, Kind.CC, self.out_channel(ch), 7, b[1])
        elif code == 0x34 and not p.percussion:
            self.emit(out, Kind.PROGRAM, self.out_channel(ch), b[1])
        elif code == 0x35:
            self.skip("part transpose hook")

    def play(self, smf: bytes) -> Track:
        division, tracks = read_smf(smf)
        if division != TICKS_PER_BEAT:
            raise ValueError(f"division {division}: iMUSE songs are {TICKS_PER_BEAT} ticks per beat")
        tr = self.track
        played: list[_Segment] = []
        seg = _Segment(0, 0, None, 0)
        i = 0
        counted: tuple[int, int, int] | None = None      # (count, from tick, to tick)
        tempo_set = False
        while True:
            evs = tracks[seg.track]
            if i >= len(evs):
                tr.end_tick = seg.out + ((evs[-1].tick if evs else seg.start) - seg.start)
                tr.terminated = True
                break
            e = evs[i]
            if counted and counted[0] and e.tick >= counted[1]:   # Player::onTimer
                out = seg.out + counted[1] - seg.start
                self.notes_off(out)
                seg.end = counted[1]
                played.append(seg)
                back = next((s for s in played if s.track == seg.track and s.start <= counted[2] < s.end), None)
                if counted[0] >= FOREVER and back is not None:
                    tr.loops, tr.terminated = True, True
                    tr.loop_start, tr.end_tick = back.out + counted[2] - back.start, out
                    break
                counted = (counted[0] - 1, counted[1], counted[2])
                seg, i = _Segment(seg.track, counted[2], None, out), self._index(evs, counted[2])
                continue
            out = seg.out + e.tick - seg.start
            if out > MAX_TICKS:
                raise ValueError("the song neither ends nor loops within half an hour")
            i += 1
            if e.status < 0xF0:
                self.channel_event(out, e)
            elif e.status == 0xFF:
                if e.data[0] == 0x51 and len(e.data) == 4:
                    us = int.from_bytes(e.data[1:4], "big")
                    if out == 0 and not tempo_set:
                        tr.tempo_us = us
                    elif not tr.tempos or tr.tempos[-1][1] != us:
                        tr.tempos.append((out, us))
                    tempo_set = True
                elif e.data[0] == 0x2F:
                    tr.end_tick, tr.terminated = out, True
                    self.notes_off(out)
                    break
            elif e.data and e.data[0] == ROLAND:
                tb = roland_timbre(e.data)
                ch = e.data[1] & 0x0F
                if tb is None:
                    self.skip("Roland SysEx (not a timbre)")
                elif not self.part(ch).percussion:
                    self.emit(out, Kind.TIMBRE, self.out_channel(ch), self.timbre_ids[tb.key])
            elif e.data and e.data[0] == IMUSE and len(e.data) > 1:
                code, d = e.data[1], e.data[2:]
                if code == 0x00:
                    self.allocate(out, d)
                elif code == 0x01:
                    self.notes_off(out, d[0] & 0x0F)
                    self.parts.pop(d[0] & 0x0F, None)
                elif code == 0x02:
                    pass                                  # start of song
                elif code == 0x30:
                    b = decode(d[1:])
                    if b[0]:
                        self.skip("jump (script)")
                        continue
                    to_track = b[1] << 8 | b[2]
                    to = (b[3] << 8 | b[4]) - 1
                    to = max(0, to) * TICKS_PER_BEAT + (b[5] << 8 | b[6])
                    if not 0 <= to_track < len(tracks):
                        self.skip("jump to a missing track")
                        continue
                    self.notes_off(out)
                    seg.end = e.tick
                    played.append(seg)
                    back = next((s for s in played if s.track == to_track and s.start <= to < s.end), None)
                    if back is not None:                  # into what has played: the loop
                        tr.loops, tr.terminated = True, True
                        tr.loop_start, tr.end_tick = back.out + to - back.start, out
                        break
                    seg, i = _Segment(to_track, to, None, out), self._index(tracks[to_track], to)
                elif 0x31 <= code <= 0x35:
                    self.hook(out, code, d)
                elif code == 0x50:
                    b = decode(d[1:])
                    w = lambda k: b[k] << 8 | b[k + 1]
                    counted = (w(0), max(0, w(6) - 1) * TICKS_PER_BEAT + w(8),
                               max(0, w(2) - 1) * TICKS_PER_BEAT + w(4))
                elif code == 0x51:
                    counted = None
                elif code == 0x40:
                    pass                                  # marker for scripts
                else:
                    self.skip(f"iMUSE SysEx {code:#04x}")
            else:
                self.skip("SysEx (other)")
        tr.events.sort(key=lambda ev: ev.tick)            # stable: stream order within a tick
        tr.events = remap_channels(tr.events)
        return tr

    @staticmethod
    def _index(evs: list[SmfEvent], tick: int) -> int:
        """The first event at or after `tick` (MidiParser::jumpToTick)."""
        return next((k for k, e in enumerate(evs) if e.tick >= tick), len(evs))


MT32_PARTS = range(1, 9)           # MIDI channels of the MT-32's melodic parts 1-8 (0-based; the MI2 driver's map)
SPARE = range(10, 16)              # channels an MT-32 doesn't hear, for parts beyond eight


def remap_channels(events: list[Event]) -> list[Event]:
    """Melodic channels onto the MT-32's parts in order of first note (iMUSE assigns hardware
    channels itself; channel 0, which MI2 uses most, is not an MT-32 part). Rhythm stays on 9.
    Parts beyond eight go to channels the MT-32 ignores: kept for the NTP build, silent in a
    whole-song MT-32 render. Channels with no notes keep their order after those with notes."""
    order = []
    for e in sorted(events, key=lambda ev: ev.kind != Kind.NOTE_ON):
        if e.channel != RHYTHM and e.channel not in order:
            order.append(e.channel)
    slots = [*MT32_PARTS, *SPARE, 0]
    if len(order) > len(slots):
        raise ValueError(f"{len(order)} melodic channels: more than MIDI has")
    to = dict(zip(order, slots)) | {RHYTHM: RHYTHM}
    return [Event(e.tick, e.kind, to[e.channel], e.a, e.b) for e in events]


def read(rol: bytes, timbre_ids: dict[str, int]) -> tuple[Track, dict[str, int]]:
    """(track, counts of what was not followed) for one ROL block."""
    r = Reader(timbre_ids)
    return r.play(mdhd_smf(rol)), r.ignored
