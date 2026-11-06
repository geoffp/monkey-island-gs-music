# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert General MIDI files (music/midi/gm/) to Apple IIGS MIDI Synth sequences.

MIDI Synth model (docs/iigs-midisynth.md):
- the MIDI channel selects the instrument slot in the bank, so each source channel is
  rewritten to the slot that should play it (gs/instruments.toml); Program Change is dropped;
- only notes, CC7 (volume), CC64 (sustain) and pitch bend reach the synth; the rest is dropped;
- timestamps are 96 ticks per quarter note; we keep the source ticks and pick the tempo
  that makes them play at the right speed (MI1: 120 ticks at 120 BPM -> file tempo 75);
- polyphony is left to MIDI Synth's voice stealing (7 voices); we only report the peak.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import mido

from .midisynth import SEQ_TICKS_PER_QUARTER, TRACK_ON, Bank, SeqItem, Sequence

RHYTHM_CHANNEL = 9
KEEP_CC = {7, 64}
MAX_VOICES = 7


@dataclass
class BankMap:
    name: str
    file: str
    rhythm_slot: int
    fallback_slot: int
    programs: dict[int, int]
    drums: dict[int, int]
    wave_file: str = ""

    @classmethod
    def from_config(cls, name: str, cfg: dict) -> BankMap:
        return cls(name, cfg["file"], cfg["rhythm_slot"], cfg["fallback_slot"],
                   {int(k): v for k, v in cfg.get("programs", {}).items()},
                   {int(k): v for k, v in cfg.get("drums", {}).items()},
                   cfg.get("wave_file", ""))


@dataclass
class Report:
    warnings: list[str] = field(default_factory=list)
    channel_slots: dict[int, int] = field(default_factory=dict)
    peak_voices: int = 0
    notes: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, what: str) -> None:
        self.dropped[what] = self.dropped.get(what, 0) + 1


def load_config(path: Path, song_id: str | None) -> tuple[BankMap, dict[int, int]]:
    cfg = tomllib.loads(path.read_text())
    song = cfg.get("songs", {}).get(song_id or "", {})
    bank_name = song.get("bank", cfg["default_bank"])
    overrides = {int(k) - 1: v for k, v in song.get("channels", {}).items()}
    return BankMap.from_config(bank_name, cfg["banks"][bank_name]), overrides


def _tempo_and_scale(tpq: int, tempo_us: int) -> tuple[int, float]:
    """File tempo (half-note BPM) and tick scale factor for the source timing.

    Prefer keeping ticks unchanged: the sequencer counts 96 ticks per quarter, so a source
    tick lasts tempo_us/tpq; pick the quarter BPM where 96 ticks take 96 such ticks.
    """
    quarter_bpm = 60e6 / tempo_us * tpq / SEQ_TICKS_PER_QUARTER
    half = quarter_bpm / 2
    if abs(half - round(half)) < 1e-9 and 5 <= round(half) <= 132:
        return round(half), 1.0
    # Otherwise rescale ticks to 96/quarter and round the tempo.
    half = round(60e6 / tempo_us / 2)
    return max(5, min(132, half)), SEQ_TICKS_PER_QUARTER / tpq


def convert(mf: mido.MidiFile, bank: BankMap, overrides: dict[int, int] | None = None,
            bank_file: Bank | None = None, title: str = "") -> tuple[Sequence, Report]:
    overrides = overrides or {}
    rep = Report()

    # Absolute-time merge, keeping per-track order stable.
    events: list[tuple[int, int, mido.Message]] = []
    tempos = set()
    for ti, trk in enumerate(mf.tracks):
        t = 0
        for msg in trk:
            t += msg.time
            if msg.type == "set_tempo":
                tempos.add((t, msg.tempo))
            elif not msg.is_meta and msg.type != "sysex":
                events.append((t, ti, msg))
    events.sort(key=lambda e: e[0])
    if len({tp for _, tp in tempos}) > 1:
        raise ValueError("tempo changes are not supported yet")
    tempo_us = next(iter(tempos))[1] if tempos else 500_000
    file_tempo, scale = _tempo_and_scale(mf.ticks_per_beat, tempo_us)

    # Channel -> slot from the first program change (or overrides / rhythm).
    first_prog: dict[int, int] = {}
    for _, _, m in events:
        if m.type == "program_change":
            first_prog.setdefault(m.channel, m.program)
    # Only channels that actually play notes (MI1 sends a stray program change on ch 9).
    used = sorted({m.channel for _, _, m in events if m.type == "note_on" and m.velocity > 0})
    events = [e for e in events if not hasattr(e[2], "channel") or e[2].channel in used]
    for ch in used:
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
    slots = list(rep.channel_slots.values())
    for s in set(slots):
        shared = [c + 1 for c, v in rep.channel_slots.items() if v == s]
        if len(shared) > 1:
            rep.warnings.append(f"channels {shared} share slot {s} (volume/notes interact)")

    track_of = {ch: i for i, ch in enumerate(used)}      # one seq track per source channel
    zones = {}
    if bank_file:
        for s in set(slots):
            z = bank_file.instruments[s].zones()
            zones[s] = (min(lo for lo, _, _ in z), max(hi for _, hi, _ in z)) if z else None

    items: list[SeqItem] = []
    active: set[tuple[int, int]] = set()
    first_vol: dict[int, int] = {}
    silent = 0
    for t, _, m in events:
        if not hasattr(m, "channel"):
            continue
        ch = m.channel
        slot = rep.channel_slots[ch]
        tick = round(t * scale)
        trk = track_of[ch]
        if m.type in ("note_on", "note_off"):
            note = m.note
            if ch == RHYTHM_CHANNEL and ch not in overrides:
                if note not in bank.drums:
                    rep.drop(f"unmapped drum key {note}")
                    continue
                note = bank.drums[note]
            on = m.type == "note_on" and m.velocity > 0
            if on:
                rep.notes += 1
                active.add((slot, note))
                rep.peak_voices = max(rep.peak_voices, len(active))
                z = zones.get(slot)
                if bank_file and (z is None or not z[0] <= note <= z[1]):
                    silent += 1
                items.append(SeqItem(trk, tick, 0x90 | slot, bytes([note, m.velocity])))
            else:
                active.discard((slot, note))
                items.append(SeqItem(trk, tick, 0x80 | slot, bytes([note, 0x40])))
        elif m.type == "control_change":
            if m.control not in KEEP_CC:
                rep.drop(f"CC{m.control}")
                continue
            if m.control == 7:
                first_vol.setdefault(slot, m.value)
            items.append(SeqItem(trk, tick, 0xB0 | slot, bytes([m.control, m.value])))
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

    seq = Sequence(bank_name=bank.file, items=items, tempo=file_tempo)
    seq.track_names = [""] * 16
    seq.track_play = [0] * 16
    for ch, i in track_of.items():
        name = "Rhythm" if ch == RHYTHM_CHANNEL else f"Ch{ch + 1}"
        if bank_file:
            name = f"{ch + 1} {bank_file.instruments[rep.channel_slots[ch]].name}"
        seq.track_names[i] = name[:15]
        seq.track_play[i] = TRACK_ON
    if title:
        seq.track_names[len(track_of)] = title[:15]
    for slot, vol in first_vol.items():
        seq.channel_volume[slot] = vol
    return seq, rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="GM MIDI files")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("-c", "--config", type=Path, default=Path("gs/instruments.toml"))
    ap.add_argument("-b", "--bank-dir", type=Path,
                    help="directory holding the bank files, to check key zones")
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for path in args.inputs:
        song_id = path.stem.split("-")[0]
        bank, overrides = load_config(args.config, song_id)
        bank_file = None
        if args.bank_dir:
            bank_file = Bank.from_bytes((args.bank_dir / bank.file).read_bytes())
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
