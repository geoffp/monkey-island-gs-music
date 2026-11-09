# SPDX-License-Identifier: GPL-3.0-or-later
"""Apple IIGS MIDI Synth file formats: sequence ($D5), instrument bank ($D6), wave bank ($D8).

Layouts and semantics: docs/iigs-midisynth.md. All values are little-endian; names are
16-byte Pascal strings (length byte + up to 15 latin-1 bytes, zero padded).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SEQ_TYPE, SEQ_AUX = 0xD5, 0x0001
BNK_TYPE, BNK_AUX = 0xD6, 0x0001
WAV_TYPE, WAV_AUX = 0xD8, 0x0004

FORMAT_VERSION = 0x0100
TRACKS = CHANNELS = INSTRUMENTS = 16
NAME_SIZE = 16

SEQ_HEADER_SIZE = 0x1D2
SEQ_TICKS_PER_QUARTER = 96          # fixed by the sequencer
END_OF_SEQ = b"\xff\xff"
THRU = 0xFFFF                       # t_channel: keep each event's own channel
TRACK_ON = 0xFFFF

INSTRUMENT_SIZE = 288               # InstrumentRec: two generators
ENVELOPE_SIZE = WAVELIST_SIZE = 16
WAVELISTS = 8                       # per generator
BANK_HEADER_SIZE = 0x400
BANK_NAMES_AT = 0x300

WAVE_HEADER_SIZE = 0x900
WAVE_ENTRIES_AT, WAVE_ENTRY_SIZE, MAX_WAVES = 0x100, 32, 64
DOC_RAM = 0x10000


def _pstr(raw: bytes) -> str:
    """A Pascal string (tolerating a length byte longer than the field)."""
    return raw[1:1 + min(raw[0], len(raw) - 1)].decode("latin-1")


def _pack_pstr(s: str, size: int = NAME_SIZE) -> bytes:
    b = s.encode("latin-1")
    if len(b) > size - 1:
        raise ValueError(f"name {s!r} longer than {size - 1} bytes")
    return bytes([len(b)]) + b.ljust(size - 1, b"\x00")


def _owner(owner: str) -> bytes:
    """The 16-byte owner field: plain latin-1 text, zero padded (not a Pascal string)."""
    return owner.encode("latin-1")[:NAME_SIZE].ljust(NAME_SIZE, b"\x00")


def _bank_ref(name: str) -> bytes:
    """Bank name field: Pascal string, then the bank's ProDOS file type and aux type
    (synthLAB writes $00D6, $0001 right after the name), zero padded to 16 bytes."""
    b = name.encode("latin-1")
    if len(b) > NAME_SIZE - 5:
        raise ValueError(f"bank name {name!r} longer than {NAME_SIZE - 5} bytes (needs room for type/aux)")
    return (bytes([len(b)]) + b + struct.pack("<HH", BNK_TYPE, BNK_AUX)).ljust(NAME_SIZE, b"\x00")


# ---------------------------------------------------------------------------
# Sequence

@dataclass(frozen=True)
class SeqItem:
    """One 8-byte sequence entry: track, 24-bit time, data count, status, 2 data bytes."""
    track: int
    time: int        # absolute ticks (96 per quarter note at the file tempo)
    status: int      # MIDI status byte, or a sequencer command $00-$7F
    data: bytes = b""

    def pack(self) -> bytes:
        if not 0 <= self.time < 1 << 24:
            raise ValueError(f"timestamp {self.time} out of 24-bit range")
        if len(self.data) > 2:
            raise ValueError(f"at most 2 data bytes, not {len(self.data)}")
        d1, d2 = self.data.ljust(2, b"\x00")
        t = self.time
        # the time is stored high byte first, then low, then middle
        return bytes([self.track, t >> 16 & 0xFF, t & 0xFF, t >> 8 & 0xFF,
                      len(self.data), self.status, d1, d2])

    @classmethod
    def unpack(cls, b: bytes) -> SeqItem:
        track, hi, lo, mid, count, status, d1, d2 = b
        if count > 2:
            raise ValueError(f"illegal data byte count {count}")
        return cls(track, hi << 16 | mid << 8 | lo, status, bytes([d1, d2])[:count])


# Sequence header: "MSEQ", version, header size, owner, bank reference, 16 track names, then
# these words, then 32 reserved bytes.
_SEQ_WORDS_AT = 0x28 + TRACKS * NAME_SIZE
_SEQ_WORDS = struct.Struct("<16H16H16H5H16H")   # volumes, channels, play, 5 settings, outputs


@dataclass
class Sequence:
    bank_name: str
    items: list[SeqItem] = field(default_factory=list)
    track_names: list[str] = field(default_factory=lambda: [f"Track {i + 1}" for i in range(TRACKS)])
    channel_volume: list[int] = field(default_factory=lambda: [127] * CHANNELS)
    track_channel: list[int] = field(default_factory=lambda: [THRU] * TRACKS)
    track_play: list[int] = field(default_factory=lambda: [TRACK_ON] + [0] * (TRACKS - 1))
    track_out: list[int] = field(default_factory=lambda: [0] * TRACKS)
    rec_track: int = 0xFFFF
    tempo: int = 60                 # half-note BPM (synthLAB convention)
    beats_per_measure: int = 4
    beat_value: int = 1             # 0 half, 1 quarter, 2 eighth...
    ticks_per_beat: int = 96
    owner: str = "migs"
    version: int = FORMAT_VERSION

    def to_bytes(self) -> bytes:
        if not 5 <= self.tempo <= 132:
            raise ValueError(f"tempo {self.tempo} outside 5-132 (half-note BPM)")
        if any(b.time < a.time for a, b in zip(self.items, self.items[1:])):
            raise ValueError("seq items must be in non-decreasing time order")
        names = (self.track_names + [""] * TRACKS)[:TRACKS]
        header = b"".join([
            b"MSEQ", struct.pack("<HH", self.version, SEQ_HEADER_SIZE),
            _pack_pstr(self.owner), _bank_ref(self.bank_name),
            *(_pack_pstr(n) for n in names),
            _SEQ_WORDS.pack(*self.channel_volume, *self.track_channel, *self.track_play,
                            self.rec_track, self.tempo, self.beats_per_measure, self.beat_value,
                            self.ticks_per_beat, *self.track_out),
            bytes(32),
        ])
        assert len(header) == SEQ_HEADER_SIZE
        return header + b"".join(i.pack() for i in self.items) + END_OF_SEQ

    @classmethod
    def from_bytes(cls, d: bytes) -> Sequence:
        if d[:4] != b"MSEQ":
            raise ValueError("not a MIDI Synth sequence (no MSEQ signature)")
        version, items_at = struct.unpack_from("<HH", d, 4)
        w = _SEQ_WORDS.unpack_from(d, _SEQ_WORDS_AT)
        s = cls(
            bank_name=_pstr(d[0x18:0x28]), owner=_pstr(d[0x08:0x18]), version=version,
            track_names=[_pstr(d[0x28 + i * NAME_SIZE:0x38 + i * NAME_SIZE]) for i in range(TRACKS)],
            channel_volume=list(w[0:16]), track_channel=list(w[16:32]), track_play=list(w[32:48]),
            rec_track=w[48], tempo=w[49], beats_per_measure=w[50], beat_value=w[51],
            ticks_per_beat=w[52], track_out=list(w[53:69]),
        )
        p = items_at
        while d[p:p + 2] != END_OF_SEQ:
            if p + 8 > len(d):
                raise ValueError("sequence truncated (no $FFFF end marker)")
            s.items.append(SeqItem.unpack(d[p:p + 8]))
            p += 8
        return s

    def seconds(self) -> float:
        """Playing time of the last item (synthfile: ticks * 30 / (96 * tempo))."""
        return (self.items[-1].time if self.items else 0) * 30 / (96 * self.tempo)


# ---------------------------------------------------------------------------
# Instrument bank

@dataclass(frozen=True)
class Wavelist:
    """A decoded WavelistRec (ERS 18-23): a key zone's two oscillators A and B."""
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

    @classmethod
    def unpack(cls, r: bytes) -> Wavelist:
        # r[7:10] and r[13:16] are oscillator A's and B's tuning (octave, semitone, fine)
        return cls(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[10], r[11], r[12])


_VOL_A, _VOL_B = 6, 12                  # volume bytes within a WavelistRec


def _silenced(wl: bytes) -> bytes:
    """A WavelistRec with both oscillators' volumes set to 0."""
    b = bytearray(wl)
    b[_VOL_A] = b[_VOL_B] = 0
    return bytes(b)


@dataclass
class Instrument:
    name: str
    raw: bytes                      # the 288-byte InstrumentRec

    @classmethod
    def build(cls, name: str, gen1: tuple[bytes, list[bytes]],
              gen2: tuple[bytes, list[bytes]] | None = None) -> Instrument:
        """From (EnvelopeRec, [WavelistRec...]) per generator (see envelope()/wavelist()).
        Unused wavelists are zero (TopKey 0). Gen 2 defaults to silent copies of Gen 1."""
        def generator(env: bytes, wls: list[bytes]) -> bytes:
            if len(wls) > WAVELISTS:
                raise ValueError(f"at most {WAVELISTS} wavelists per generator")
            return env + b"".join(wls) + bytes(WAVELIST_SIZE * (WAVELISTS - len(wls)))
        if gen2 is None:
            env, wls = gen1
            gen2 = (env, [_silenced(w) for w in wls])
        raw = generator(*gen1) + generator(*gen2)
        assert len(raw) == INSTRUMENT_SIZE
        return cls(name, raw)

    def gen1_wavelists(self) -> list[Wavelist]:
        at = ENVELOPE_SIZE
        return [Wavelist.unpack(self.raw[at + i * WAVELIST_SIZE:at + (i + 1) * WAVELIST_SIZE])
                for i in range(WAVELISTS)]

    def zones(self) -> list[tuple[int, int, Wavelist]]:
        """Active key zones (lo, hi, wavelist), per MIDI Synth's first-match TopKey scan: a
        wavelist whose TopKey is below the zone start is never reached."""
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
    return bytes([*attack, *decay1, *decay2, sustain, decay3_rate, *release1, *release2,
                  release3_rate, decay_gain, velocity_gain, pitch_bend_range])


def wavelist(top_key: int, config: int, a: tuple[int, int, int, int, int, int],
             b: tuple[int, int, int, int, int, int] | None = None, stereo=0, detune=0) -> bytes:
    """WavelistRec (ERS 18-23). a/b = (page, size code, volume, octave, semitone, fine);
    b defaults to a silent copy of a."""
    b = b or (a[0], a[1], 0, a[3], a[4], a[5])
    return bytes([top_key, config, stereo, detune, *a, *b])


@dataclass
class Bank:
    wave_name: str
    num_inst: int
    instruments: list[Instrument]

    def to_bytes(self, owner: str = "migs") -> bytes:
        if len(self.instruments) != INSTRUMENTS:
            raise ValueError(f"a bank has exactly {INSTRUMENTS} instrument slots")
        h = bytearray(BANK_HEADER_SIZE)
        h[0:4] = b"INST"
        struct.pack_into("<HH", h, 4, FORMAT_VERSION, BANK_HEADER_SIZE)
        h[0x08:0x18] = _owner(owner)
        h[0x18:0x28] = _pack_pstr(self.wave_name)
        h[0x2B] = 15                                  # master volume, as in every stock bank
        h[0x2C] = self.num_inst
        for i, inst in enumerate(self.instruments):
            at = BANK_NAMES_AT + i * NAME_SIZE
            h[at:at + NAME_SIZE] = _pack_pstr(inst.name)
        return bytes(h) + b"".join(i.raw for i in self.instruments)

    @classmethod
    def from_bytes(cls, d: bytes) -> Bank:
        if d[:4] != b"INST":
            raise ValueError("not a MIDI Synth instrument file (no INST signature)")
        insts = [Instrument(_pstr(d[BANK_NAMES_AT + i * NAME_SIZE:BANK_NAMES_AT + (i + 1) * NAME_SIZE]),
                            d[BANK_HEADER_SIZE + i * INSTRUMENT_SIZE:BANK_HEADER_SIZE + (i + 1) * INSTRUMENT_SIZE])
                 for i in range(INSTRUMENTS)]
        return cls(_pstr(d[0x18:0x28]), d[0x2C], insts)


@dataclass
class WaveBank:
    waves: dict[int, tuple[str, int]]   # page address -> (name, size code)
    ram: bytes = b""                    # 64 KB DOC RAM image (when built or read)

    def to_bytes(self, owner: str = "migs") -> bytes:
        if len(self.waves) > MAX_WAVES:
            raise ValueError(f"at most {MAX_WAVES} wave table entries")
        ram = self.ram or bytes(DOC_RAM)
        if len(ram) != DOC_RAM:
            raise ValueError("DOC RAM image must be 64 KB")
        h = bytearray(WAVE_HEADER_SIZE)
        h[0:4] = b"WAVE"
        struct.pack_into("<HH", h, 4, FORMAT_VERSION, WAVE_HEADER_SIZE)
        h[0x08:0x18] = _owner(owner)
        h[0x18] = len(self.waves)
        for i, (page, (name, size)) in enumerate(sorted(self.waves.items())):
            e = WAVE_ENTRIES_AT + i * WAVE_ENTRY_SIZE
            h[e:e + NAME_SIZE] = _pack_pstr(name)
            h[e + 17], h[e + 19] = page, size
        return bytes(h) + ram

    @classmethod
    def from_bytes(cls, d: bytes) -> WaveBank:
        if d[:4] != b"WAVE":
            raise ValueError("not a MIDI Synth wave file (no WAVE signature)")
        waves = {}
        for i in range(d[0x18]):
            e = d[WAVE_ENTRIES_AT + i * WAVE_ENTRY_SIZE:WAVE_ENTRIES_AT + (i + 1) * WAVE_ENTRY_SIZE]
            waves.setdefault(e[17], (_pstr(e[:NAME_SIZE]), e[19]))   # first entry per page wins
        return cls(waves, d[WAVE_HEADER_SIZE:WAVE_HEADER_SIZE + DOC_RAM])
