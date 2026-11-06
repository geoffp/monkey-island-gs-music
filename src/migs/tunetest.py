# SPDX-License-Identifier: GPL-3.0-or-later
"""Tuning experiment: a bank + sequence that make MIDI Synth reveal how it turns
(key, octave, semitone, fine, wave size) into DOC oscillator registers.

Each instrument plays one sine cycle (stored at 256 B, 1 KB or 8 KB) with a different tuning.
TUNE.SEQ plays A4 (69) then A3 (57) on each instrument in turn, 2.5 s per note, with
1 s quarter notes (half-note tempo 30). While it plays, `gs2-docscope` samples the
DOC registers; `analyze()` fits the result.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from .midisynth import TRACK_ON, Bank, Instrument, SeqItem, Sequence, WaveBank, envelope, wavelist

# (name, page, size code, octave, semitone, fine)
CASES = [
    ("256 o3", 0x10, 0, 3, 0, 0),
    ("1K o3", 0x20, 2, 3, 0, 0),
    ("8K o3", 0x40, 5, 3, 0, 0),
    ("256 o4", 0x10, 0, 4, 0, 0),
    ("256 o3 s7 f32", 0x10, 0, 3, 7, 32),
    ("8K o1", 0x40, 5, 1, 0, 0),
]
# Experiment 2 (TUNE2): which DOC resolution MIDI Synth picks for each wave size.
# One sine cycle per wave, octave 4; sizes 512 B - 32 KB, each aligned to its size
# (pages $00-$01 are left free for synthLAB's metronome).
CASES2 = [
    ("512 o4", 0x02, 1, 4, 0, 0),
    ("1K o4", 0x04, 2, 4, 0, 0),
    ("2K o4", 0x08, 3, 4, 0, 0),
    ("4K o4", 0x10, 4, 4, 0, 0),
    ("8K o4", 0x20, 5, 4, 0, 0),
    ("16K o4", 0x40, 6, 4, 0, 0),
    ("32K o4", 0x80, 7, 4, 0, 0),
]
NOTES = (69, 57)
NOTE_SECONDS = 3            # slot per note; note held 2.5 s
TICKS_PER_SECOND = 96       # half-note tempo 30 = 60 quarter BPM


def sine_cycle(n: int) -> bytes:
    """One cycle over n bytes, DOC format: unsigned 8-bit, never 0 (0 halts the oscillator)."""
    return bytes(max(1, min(255, round(128 + 120 * math.sin(2 * math.pi * i / n)))) for i in range(n))


def build(cases=CASES, notes=NOTES, name="TUNE") -> tuple[Bank, WaveBank, Sequence]:
    ram = bytearray(b"\x80" * 0x10000)
    waves = {}
    for page, size in {(c[1], c[2]) for c in cases}:
        n = 256 << size
        ram[page * 256 : page * 256 + n] = sine_cycle(n)
        waves[page] = (f"Sine {n}", size)
    env = envelope(sustain=127, release1=(0, 31))
    insts = [Instrument.build(label, (env, [wavelist(127, 0, (page, size, 127, o, s, f))]))
             for label, page, size, o, s, f in cases]
    insts += [Instrument("", bytes(288))] * (16 - len(insts))
    bank = Bank(f"{name}.WAV", len(cases), insts)

    items = []
    for i in range(len(cases)):
        for j, note in enumerate(notes):
            t = (1 + (i * len(notes) + j) * NOTE_SECONDS) * TICKS_PER_SECOND
            items.append(SeqItem(0, t, 0x90 | i, bytes([note, 100])))
            items.append(SeqItem(0, t + int(2.5 * TICKS_PER_SECOND), 0x80 | i, bytes([note, 0x40])))
    seq = Sequence(bank_name=f"{name}.BNK", items=items, tempo=30)
    seq.track_names = ["Tuning test"] + [""] * 15
    seq.track_play = [TRACK_ON] + [0] * 15
    return bank, WaveBank(waves, bytes(ram)), seq


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, cases, notes in (("TUNE", CASES, NOTES), ("TUNE2", CASES2, (69,))):
        bank, wb, seq = build(cases, notes, name)
        (args.out_dir / f"{name}.BNK").write_bytes(bank.to_bytes())
        (args.out_dir / f"{name}.WAV").write_bytes(wb.to_bytes())
        (args.out_dir / f"{name}.SEQ").write_bytes(seq.to_bytes())
        print(f"{name}.SEQ/.BNK/.WAV -> {args.out_dir}: {len(cases)} cases x notes {notes}, {seq.seconds():.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
