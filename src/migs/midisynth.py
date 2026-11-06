# SPDX-License-Identifier: GPL-3.0-or-later
"""Apple IIGS MIDI Synth file formats: sequence ($D5), instrument bank ($D6), wave bank ($D8).

Layouts and semantics: docs/iigs-midisynth.md. All values are little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SEQ_TYPE, SEQ_AUX = 0xD5, 0x0001
BNK_TYPE, BNK_AUX = 0xD6, 0x0001
WAV_TYPE, WAV_AUX = 0xD8, 0x0004

SEQ_HEADER_SIZE = 0x1D2
SEQ_TICKS_PER_QUARTER = 96          # fixed by the sequencer
END_OF_SEQ = b"\xff\xff"
THRU = 0xFFFF                       # t_channel: keep each event's own channel
TRACK_ON = 0xFFFF


def _pstr(raw: bytes) -> str:
    return raw[1 : 1 + min(raw[0], len(raw) - 1)].decode("latin-1")


def _pack_pstr(s: str, size: int = 16) -> bytes:
    b = s.encode("latin-1")
    if len(b) > size - 1:
        raise ValueError(f"name {s!r} longer than {size - 1} bytes")
    return bytes([len(b)]) + b + bytes(size - 1 - len(b))


def _bank_ref(name: str) -> bytes:
    """Bank name field: Pascal string, then the bank's ProDOS file type and aux type
    (synthLAB writes $00D6, $0001 right after the name), zero padded to 16 bytes."""
    b = name.encode("latin-1")
    if len(b) > 11:
        raise ValueError(f"bank name {name!r} longer than 11 bytes (needs room for type/aux)")
    ref = bytes([len(b)]) + b + struct.pack("<HH", BNK_TYPE, BNK_AUX)
    return ref + bytes(16 - len(ref))


# ---------------------------------------------------------------------------
# Sequence

@dataclass(frozen=True)
class SeqItem:
    track: int
    time: int        # absolute ticks (96 per quarter note at the file tempo)
    status: int      # MIDI status byte, or a sequencer command $00-$7F
    data: bytes = b""

    def pack(self) -> bytes:
        if not 0 <= self.time < 1 << 24:
            raise ValueError(f"timestamp {self.time} out of 24-bit range")
        d = self.data + bytes(2 - len(self.data))
        t = self.time
        return bytes([self.track, t >> 16 & 0xFF, t & 0xFF, t >> 8 & 0xFF,
                      len(self.data), self.status, d[0], d[1]])

    @classmethod
    def unpack(cls, b: bytes) -> SeqItem:
        track, hi, lo, mid, count, status, d1, d2 = b
        if count > 2:
            raise ValueError(f"illegal data byte count {count}")
        return cls(track, hi << 16 | mid << 8 | lo, status, bytes([d1, d2])[:count])


@dataclass
class Sequence:
    bank_name: str
    items: list[SeqItem] = field(default_factory=list)
    track_names: list[str] = field(default_factory=lambda: [f"Track {i + 1}" for i in range(16)])
    channel_volume: list[int] = field(default_factory=lambda: [127] * 16)
    track_channel: list[int] = field(default_factory=lambda: [THRU] * 16)
    track_play: list[int] = field(default_factory=lambda: [TRACK_ON] + [0] * 15)
    track_out: list[int] = field(default_factory=lambda: [0] * 16)
    rec_track: int = 0xFFFF
    tempo: int = 60                 # half-note BPM (synthLAB convention)
    beats_per_measure: int = 4
    beat_value: int = 1             # 0 half, 1 quarter, 2 eighth...
    ticks_per_beat: int = 96
    owner: str = "migs"
    version: int = 0x0100

    def to_bytes(self) -> bytes:
        if not 5 <= self.tempo <= 132:
            raise ValueError(f"tempo {self.tempo} outside 5-132 (half-note BPM)")
        last = -1
        for it in self.items:
            if it.time < last:
                raise ValueError("seq items must be in non-decreasing time order")
            last = it.time
        h = bytearray()
        h += b"MSEQ" + struct.pack("<HH", self.version, SEQ_HEADER_SIZE)
        h += _pack_pstr(self.owner) + _bank_ref(self.bank_name)
        names = (self.track_names + [""] * 16)[:16]
        h += b"".join(_pack_pstr(n) for n in names)
        for arr in (self.channel_volume, self.track_channel, self.track_play):
            h += struct.pack("<16H", *arr)
        h += struct.pack("<5H", self.rec_track, self.tempo, self.beats_per_measure,
                         self.beat_value, self.ticks_per_beat)
        h += struct.pack("<16H", *self.track_out) + bytes(32)
        assert len(h) == SEQ_HEADER_SIZE
        return bytes(h) + b"".join(i.pack() for i in self.items) + END_OF_SEQ

    @classmethod
    def from_bytes(cls, d: bytes) -> Sequence:
        if d[:4] != b"MSEQ":
            raise ValueError("not a MIDI Synth sequence (no MSEQ signature)")
        version, off = struct.unpack_from("<HH", d, 4)
        w = struct.unpack_from("<85H", d, 0x128)
        s = cls(
            bank_name=_pstr(d[0x18:0x28]), owner=_pstr(d[0x08:0x18]), version=version,
            track_names=[_pstr(d[0x28 + i * 16 : 0x38 + i * 16]) for i in range(16)],
            channel_volume=list(w[0:16]), track_channel=list(w[16:32]), track_play=list(w[32:48]),
            rec_track=w[48], tempo=w[49], beats_per_measure=w[50], beat_value=w[51],
            ticks_per_beat=w[52], track_out=list(w[53:69]),
        )
        p = off
        while p + 2 <= len(d) and d[p : p + 2] != END_OF_SEQ:
            if p + 8 > len(d):
                raise ValueError("sequence truncated (no $FFFF end marker)")
            s.items.append(SeqItem.unpack(d[p : p + 8]))
            p += 8
        if d[p : p + 2] != END_OF_SEQ:
            raise ValueError("no $FFFF end marker")
        return s

    def seconds(self) -> float:
        """Playing time of the last item (synthfile: ticks * 30 / (96 * tempo))."""
        return (self.items[-1].time if self.items else 0) * 30 / (96 * self.tempo)


# ---------------------------------------------------------------------------
# Instrument bank (read-only for now)

@dataclass(frozen=True)
class Wavelist:
    top_key: int
    osc_config: int
    stereo: int
    detune: int
    addr_a: int
    size_a: int
    vol_a: int
    addr_b: int
    size_b: int
    vol_b: int


@dataclass
class Instrument:
    name: str
    raw: bytes                      # the 288-byte InstrumentRec

    @classmethod
    def build(cls, name: str, gen1: tuple[bytes, list[bytes]],
              gen2: tuple[bytes, list[bytes]] | None = None) -> Instrument:
        """From (EnvelopeRec, [WavelistRec...]) per generator (see envelope()/wavelist()).
        Unused wavelists are zero (TopKey 0). Gen 2 defaults to silent copies of Gen 1."""
        def gen(env: bytes, wls: list[bytes]) -> bytes:
            if len(wls) > 8:
                raise ValueError("at most 8 wavelists per generator")
            return env + b"".join(wls) + bytes(16 * (8 - len(wls)))
        if gen2 is None:
            env, wls = gen1
            gen2 = (env, [w[:6] + b"\x00" + w[7:12] + b"\x00" + w[13:] for w in wls])  # volumes 0
        raw = gen(*gen1) + gen(*gen2)
        assert len(raw) == 288
        return cls(name, raw)

    def gen1_wavelists(self) -> list[Wavelist]:
        out = []
        for i in range(8):
            r = self.raw[16 + i * 16 : 32 + i * 16]
            out.append(Wavelist(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10], r[11], r[12]))
        return out

    def zones(self) -> list[tuple[int, int, Wavelist]]:
        """Active key zones (lo, hi, wavelist), per MIDI Synth's first-match TopKey scan."""
        zones, lo = [], 0
        for wl in self.gen1_wavelists():
            if wl.top_key >= lo:
                zones.append((lo, wl.top_key, wl))
                lo = wl.top_key + 1
            if lo > 127:
                break
        return zones


def envelope(attack=(127, 31), decay1=(127, 31), decay2=(127, 31), sustain=127, decay3_rate=31,
             release1=(0, 20), release2=(0, 31), release3_rate=31, decay_gain=0,
             velocity_gain=4, pitch_bend_range=0) -> bytes:
    """EnvelopeRec (ERS 14-17): (level 0-127, rate 0-31) pairs; rate 31 = instant."""
    return bytes([attack[0], attack[1], decay1[0], decay1[1], decay2[0], decay2[1], sustain,
                  decay3_rate, release1[0], release1[1], release2[0], release2[1], release3_rate,
                  decay_gain, velocity_gain, pitch_bend_range])


def wavelist(top_key: int, config: int, a: tuple[int, int, int, int, int, int],
             b: tuple[int, int, int, int, int, int] | None = None, stereo=0, detune=0) -> bytes:
    """WavelistRec (ERS 18-23). a/b = (page, size code, volume, octave, semitone, fine)."""
    b = b or (a[0], a[1], 0, a[3], a[4], a[5])
    return bytes([top_key, config, stereo, detune, *a, *b])


@dataclass
class Bank:
    wave_name: str
    num_inst: int
    instruments: list[Instrument]

    def to_bytes(self, owner: str = "migs") -> bytes:
        if len(self.instruments) != 16:
            raise ValueError("a bank has exactly 16 instrument slots")
        h = bytearray(0x400)
        h[0:4] = b"INST"
        struct.pack_into("<HH", h, 4, 0x0100, 0x0400)
        h[0x08:0x18] = owner.encode("latin-1")[:16].ljust(16, b"\x00")
        h[0x18:0x28] = _pack_pstr(self.wave_name)
        h[0x2B] = 15                                  # master volume, as in every stock bank
        h[0x2C] = self.num_inst
        for i, inst in enumerate(self.instruments):
            h[0x300 + i * 16 : 0x310 + i * 16] = _pack_pstr(inst.name)
        return bytes(h) + b"".join(i.raw for i in self.instruments)

    @classmethod
    def from_bytes(cls, d: bytes) -> Bank:
        if d[:4] != b"INST":
            raise ValueError("not a MIDI Synth instrument file (no INST signature)")
        insts = [Instrument(_pstr(d[0x300 + i * 16 : 0x310 + i * 16]),
                            d[0x400 + i * 288 : 0x400 + (i + 1) * 288]) for i in range(16)]
        return cls(_pstr(d[0x18:0x28]), d[0x2C], insts)


@dataclass
class WaveBank:
    waves: dict[int, tuple[str, int]]   # page address -> (name, size code)
    ram: bytes = b""                    # 64 KB DOC RAM image (when built or read)

    def to_bytes(self, owner: str = "migs") -> bytes:
        if len(self.waves) > 64:
            raise ValueError("at most 64 wave table entries")
        ram = self.ram or bytes(0x10000)
        if len(ram) != 0x10000:
            raise ValueError("DOC RAM image must be 64 KB")
        h = bytearray(0x900)
        h[0:4] = b"WAVE"
        struct.pack_into("<HH", h, 4, 0x0100, 0x0900)
        h[0x08:0x18] = owner.encode("latin-1")[:16].ljust(16, b"\x00")
        h[0x18] = len(self.waves)
        for i, (page, (name, size)) in enumerate(sorted(self.waves.items())):
            e = 0x100 + i * 32
            h[e : e + 16] = _pack_pstr(name)
            h[e + 17], h[e + 19] = page, size
        return bytes(h) + ram

    @classmethod
    def from_bytes(cls, d: bytes) -> WaveBank:
        if d[:4] != b"WAVE":
            raise ValueError("not a MIDI Synth wave file (no WAVE signature)")
        waves = {}
        for i in range(d[0x18]):
            e = d[0x100 + i * 32 : 0x120 + i * 32]
            waves.setdefault(e[17], (_pstr(e[:16]), e[19]))
        return cls(waves, d[0x900:0x10900])
