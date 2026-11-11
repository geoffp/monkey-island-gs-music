# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert General MIDI files (build/music/midi/gm/) to Apple IIGS MIDI Synth sequences.

MIDI Synth model (docs/iigs-midisynth.md):
- the MIDI channel selects the instrument slot in the bank, so each source channel is
  rewritten to the slot that should play it (config/midisynth.toml); Program Change is dropped;
- only notes, CC7 (volume), CC64 (sustain) and pitch bend reach the synth; the rest is dropped;
- timestamps are 96 ticks per quarter note; we keep the source ticks and pick the tempo
  that makes them play at the right speed (MI1: 120 ticks at 120 BPM -> file tempo 75);
- polyphony is left to MIDI Synth's voice stealing (7 voices); we only report the peak.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import mido

from .midisynth import SEQ_TICKS_PER_QUARTER, TRACK_ON, TRACKS, Bank, SeqItem, Sequence

RHYTHM_CHANNEL = 9
KEEP_CC = {7, 64}                   # volume, sustain pedal
VOLUME = 7
MAX_VOICES = 7
DEFAULT_TEMPO_US = 500_000          # MIDI's default: 120 BPM
TEMPO_RANGE = range(5, 133)         # MIDI Synth file tempo (half-note BPM)
NAME_LEN = 15                       # track names are 16-byte Pascal strings
# MT-32 rhythm sounds are one-shots that ignore note-off, so MI1 often holds drum notes for a
# long time. A held note keeps its MIDI Synth voice busy, so drum note-offs are moved to at
# most this long after the note-on.
DRUM_GATE_SECONDS = 0.05


class Timed(NamedTuple):
    """A channel message at an absolute source tick, with its source track (for stable order)."""
    tick: int
    track: int
    msg: mido.Message


def _is_note_on(m: mido.Message) -> bool:
    return m.type == "note_on" and m.velocity > 0


def _is_note(m: mido.Message) -> bool:
    return m.type in ("note_on", "note_off")


@dataclass
class BankMap:
    """How a bank's slots play GM programs and rhythm keys (config/midisynth.toml)."""
    name: str
    file: str
    rhythm_slot: int
    fallback_slot: int              # for programs the bank doesn't map
    programs: dict[int, int]        # GM program -> slot
    drums: dict[int, int | list[int]]   # rhythm key -> key in the rhythm slot, or [slot, key]
    wave_file: str = ""

    @classmethod
    def from_config(cls, name: str, cfg: dict) -> BankMap:
        programs, drums = dict(cfg.get("programs", {})), dict(cfg.get("drums", {}))
        if "map_file" in cfg:                       # generated mapping (e.g. by mt32bank)
            m = tomllib.loads(Path(cfg["map_file"]).read_text())
            programs.update(m.get("programs", {}))
            drums.update(m.get("drums", {}))
        return cls(name, cfg["file"], cfg["rhythm_slot"], cfg["fallback_slot"],
                   {int(k): v for k, v in programs.items()},
                   {int(k): v for k, v in drums.items()},
                   cfg.get("wave_file", ""))

    def drum(self, key: int) -> tuple[int, int] | None:
        """(slot, key) for a rhythm key; plain ints mean the rhythm slot."""
        v = self.drums.get(key)
        if v is None:
            return None
        return (v[0], v[1]) if isinstance(v, list) else (self.rhythm_slot, v)

    def kit_slots(self) -> list[int]:
        """Every slot the rhythm channel plays through."""
        return sorted({self.rhythm_slot} | {v[0] for v in self.drums.values() if isinstance(v, list)})


@dataclass
class Report:
    warnings: list[str] = field(default_factory=list)
    channel_slots: dict[int, int] = field(default_factory=dict)
    peak_voices: int = 0
    notes: int = 0
    dropped: Counter[str] = field(default_factory=Counter)

    def drop(self, what: str) -> None:
        self.dropped[what] += 1


def load_config(path: Path, song_id: str | None, profile: str = "stock") -> tuple[BankMap, dict[int, int]]:
    """The profile's bank for a song, and the song's channel -> slot overrides (0-based)."""
    cfg = tomllib.loads(path.read_text())
    prof = cfg["profiles"][profile]
    song = cfg.get("songs", {}).get(song_id or "", {})
    if isinstance(song.get(profile), dict):         # per-profile song settings
        song = song[profile]
    bank_name = song.get("bank", prof["default_bank"])
    overrides = {int(k) - 1: v for k, v in song.get("channels", {}).items()}
    return BankMap.from_config(bank_name, cfg["banks"][bank_name]), overrides


def _tempo_and_scale(tpq: int, tempo_us: int) -> tuple[int, float]:
    """File tempo (half-note BPM) and tick scale factor for the source timing.

    Prefer keeping ticks unchanged: the sequencer counts 96 ticks per quarter, so a source
    tick lasts tempo_us/tpq; pick the quarter BPM where 96 ticks take 96 such ticks.
    """
    half = 60e6 / tempo_us * tpq / SEQ_TICKS_PER_QUARTER / 2
    if abs(half - round(half)) < 1e-9 and round(half) in TEMPO_RANGE:
        return round(half), 1.0
    # Otherwise rescale ticks to 96/quarter and round the tempo.
    half = round(60e6 / tempo_us / 2)
    return min(max(half, TEMPO_RANGE.start), TEMPO_RANGE.stop - 1), SEQ_TICKS_PER_QUARTER / tpq


def _merge_tracks(mf: mido.MidiFile) -> tuple[list[Timed], int]:
    """All channel messages in absolute time (stable across tracks), and the one tempo."""
    events: list[Timed] = []
    tempos = set()
    for ti, trk in enumerate(mf.tracks):
        t = 0
        for msg in trk:
            t += msg.time
            if msg.type == "set_tempo":
                tempos.add(msg.tempo)
            elif not msg.is_meta and msg.type != "sysex":
                events.append(Timed(t, ti, msg))
    if len(tempos) > 1:
        raise ValueError("tempo changes are not supported yet")
    events.sort(key=lambda e: e.tick)
    return events, next(iter(tempos), DEFAULT_TEMPO_US)


def _gate_drums(events: Iterable[tuple[int, int, mido.Message]], gate: int) -> list[Timed]:
    """Rhythm-channel notes: each hit's note-off = min(note-on + gate, next hit of that key).
    The original rhythm note-offs are dropped (the MT-32 ignores them anyway)."""
    hits: dict[int, list[Timed]] = {}
    out = []
    for e in map(Timed._make, events):
        m = e.msg
        if getattr(m, "channel", None) == RHYTHM_CHANNEL and _is_note(m):
            if _is_note_on(m):
                hits.setdefault(m.note, []).append(e)
                out.append(e)
            continue
        out.append(e)
    for note, ons in hits.items():
        for on, nxt in zip(ons, [*ons[1:], None]):
            off = on.tick + gate if nxt is None else min(on.tick + gate, nxt.tick)
            out.append(Timed(off, on.track, mido.Message("note_off", channel=RHYTHM_CHANNEL, note=note)))
    # stable sort; at equal times put note-offs first so a re-hit isn't cut by its own release
    out.sort(key=lambda e: (e.tick, e.msg.type != "note_off"))
    return out


def _assign_slots(events: list[Timed], bank: BankMap, overrides: dict[int, int], rep: Report) -> None:
    """rep.channel_slots: a slot for every channel that plays notes, from (in order) the
    song's overrides, the rhythm slot, the channel's first program, the fallback slot."""
    first_prog: dict[int, int] = {}
    for e in events:
        if e.msg.type == "program_change":
            first_prog.setdefault(e.msg.channel, e.msg.program)
    for ch in sorted({e.msg.channel for e in events if _is_note_on(e.msg)}):
        if ch in overrides:
            slot = overrides[ch]
        elif ch == RHYTHM_CHANNEL:
            slot = bank.rhythm_slot
        elif ch in first_prog:
            slot = bank.programs.get(first_prog[ch], bank.fallback_slot)
            if first_prog[ch] not in bank.programs:
                rep.warnings.append(f"ch {ch + 1}: GM program {first_prog[ch]} not mapped, "
                                    f"using fallback slot {slot}")
        else:
            slot = bank.fallback_slot
        rep.channel_slots[ch] = slot
    for s in sorted(set(rep.channel_slots.values())):
        shared = [c + 1 for c, v in rep.channel_slots.items() if v == s]
        if len(shared) > 1:
            rep.warnings.append(f"channels {shared} share slot {s} (volume/notes interact)")


def _key_ranges(bank_file: Bank, slots: Iterable[int]) -> dict[int, tuple[int, int] | None]:
    """Per slot, the lowest and highest key its instrument's zones cover (None: no zones)."""
    out = {}
    for s in slots:
        z = bank_file.instruments[s].zones()
        out[s] = (min(lo for lo, _, _ in z), max(hi for _, hi, _ in z)) if z else None
    return out


def convert(mf: mido.MidiFile, bank: BankMap, overrides: dict[int, int] | None = None,
            bank_file: Bank | None = None, title: str = "") -> tuple[Sequence, Report]:
    """Convert a GM MIDI file to a sequence for `bank`. With `bank_file`, notes outside
    their instrument's key zones are counted (they'd be silent) and tracks are named after
    the instruments."""
    overrides = overrides or {}
    rep = Report()
    events, tempo_us = _merge_tracks(mf)
    gate = max(1, round(DRUM_GATE_SECONDS * 1e6 / tempo_us * mf.ticks_per_beat))
    events = _gate_drums(events, gate)
    file_tempo, scale = _tempo_and_scale(mf.ticks_per_beat, tempo_us)

    _assign_slots(events, bank, overrides, rep)
    # Only channels that actually play notes (MI1 sends a stray program change on ch 9).
    events = [e for e in events if getattr(e.msg, "channel", None) in rep.channel_slots]
    track_of = {ch: i for i, ch in enumerate(rep.channel_slots)}    # one seq track per channel
    kit_slots = bank.kit_slots()
    ranges = _key_ranges(bank_file, {*rep.channel_slots.values(), *kit_slots}) if bank_file else {}

    items: list[SeqItem] = []
    sounding: set[tuple[int, int]] = set()          # (slot, key)
    first_vol: dict[int, int] = {}
    silent = 0
    for tick_src, _, m in events:
        ch = m.channel
        slot = rep.channel_slots[ch]
        tick, trk = round(tick_src * scale), track_of[ch]
        as_kit = ch == RHYTHM_CHANNEL and ch not in overrides
        if _is_note(m):
            note = m.note
            if as_kit:
                hit = bank.drum(note)
                if hit is None:
                    rep.drop(f"unmapped drum key {note}")
                    continue
                slot, note = hit
            if _is_note_on(m):
                rep.notes += 1
                sounding.add((slot, note))
                rep.peak_voices = max(rep.peak_voices, len(sounding))
                r = ranges.get(slot)
                if bank_file and (r is None or not r[0] <= note <= r[1]):
                    silent += 1
                items.append(SeqItem(trk, tick, 0x90 | slot, bytes([note, m.velocity])))
            else:
                sounding.discard((slot, note))
                items.append(SeqItem(trk, tick, 0x80 | slot, bytes([note, 0x40])))
        elif m.type == "control_change":
            if m.control not in KEEP_CC:
                rep.drop(f"CC{m.control}")
                continue
            # rhythm-channel controllers go to every kit slot it plays through
            for sl in kit_slots if as_kit else [slot]:
                if m.control == VOLUME:
                    first_vol.setdefault(sl, m.value)
                items.append(SeqItem(trk, tick, 0xB0 | sl, bytes([m.control, m.value])))
        elif m.type == "pitchwheel":
            v = m.pitch + 8192
            items.append(SeqItem(trk, tick, 0xE0 | slot, bytes([v & 0x7F, v >> 7])))
        else:
            rep.drop(m.type)
    if silent:
        rep.warnings.append(f"{silent} notes fall outside their instrument's key zones (silent)")
    if rep.peak_voices > MAX_VOICES:
        rep.warnings.append(f"peak {rep.peak_voices} simultaneous notes > {MAX_VOICES} voices "
                            "(MIDI Synth will steal voices)")

    seq = Sequence(bank_name=bank.file, items=items, tempo=file_tempo,
                   track_names=[""] * TRACKS, track_play=[0] * TRACKS)
    for ch, i in track_of.items():
        if bank_file:
            name = f"{ch + 1} {bank_file.instruments[rep.channel_slots[ch]].name}"
        else:
            name = "Rhythm" if ch == RHYTHM_CHANNEL else f"Ch{ch + 1}"
        seq.track_names[i] = name[:NAME_LEN]
        seq.track_play[i] = TRACK_ON
    if title and len(track_of) < TRACKS:            # the title goes in the first unused name
        seq.track_names[len(track_of)] = title[:NAME_LEN]
    for slot, vol in first_vol.items():
        seq.channel_volume[slot] = vol
    return seq, rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="GM MIDI files")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("-c", "--config", type=Path, default=Path("config/midisynth.toml"))
    ap.add_argument("-p", "--profile", default="stock", help="export profile in the config")
    ap.add_argument("-b", "--bank-dir", type=Path,
                    help="directory holding the bank files, to check key zones")
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for path in args.inputs:
        song_id = path.stem.split("-")[0]
        bank, overrides = load_config(args.config, song_id, args.profile)
        bank_file = Bank.from_bytes((args.bank_dir / bank.file).read_bytes()) if args.bank_dir else None
        seq, rep = convert(mido.MidiFile(path), bank, overrides, bank_file, title=f"MI1 {song_id}")
        # ProDOS names: max 15 chars, letters/digits/periods, starting with a letter.
        out = args.out_dir / f"MI{song_id}.SEQ"
        out.write_bytes(seq.to_bytes())
        slots = " ".join(f"{c + 1}>{s}" for c, s in sorted(rep.channel_slots.items()))
        print(f"{path.name:<20} -> {out.name:<11} {len(seq.items):5d} items {seq.seconds():6.1f}s "
              f"tempo {seq.tempo} peak {rep.peak_voices:2d}  [{slots}]")
        for w in rep.warnings:
            print(f"    warning: {w}")
        if rep.dropped:
            print("    dropped: " + ", ".join(f"{k} x{v}" for k, v in sorted(rep.dropped.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
