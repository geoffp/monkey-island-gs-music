# SPDX-License-Identifier: GPL-3.0-or-later
"""Estimate MIDI Synth voice occupancy for a sequence + bank.

MIDI Synth frees a voice only when BOTH generators' envelopes reach zero (ERS p.16), not at
note-off, and has 7 voices. This replays the sequence against the bank's envelopes
(linear ramps, ERS rate table) and reports how often more than 7 voices would be needed,
i.e. how much voice stealing to expect.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .midisynth_tuning import ENVELOPE_RATE_SECONDS
from .midisynth import ENVELOPE_SIZE, INSTRUMENT_SIZE, Bank, Sequence

VOICES = 7
FULL_SCALE = 127
GEN2_ENVELOPE = INSTRUMENT_SIZE // 2         # generator 2's EnvelopeRec offset


def _ramp(rate: int, span: float) -> float:
    """Seconds an envelope at `rate` takes to move `span` levels."""
    return ENVELOPE_RATE_SECONDS[rate] * span / FULL_SCALE


def envelope_end(e: bytes, held: float) -> float:
    """Seconds from note-on until a 16-byte EnvelopeRec reaches zero, for a note held `held` s.

    While held, the envelope ramps through attack, decay 1, decay 2 and (at the decay 3 rate)
    to the sustain level; at note-off it ramps down through release 1, 2 and 3 from wherever
    it got to. A segment ending at 0 before note-off ends the note by itself.
    """
    held_segments = [(e[0], e[1]), (e[2], e[3]), (e[4], e[5]), (e[6], e[7])]
    release_segments = [(e[8], e[9]), (e[10], e[11]), (0, e[12])]
    t, lvl = 0.0, 0
    for target, rate in held_segments:
        dur = _ramp(rate, abs(target - lvl))
        if t + dur >= held:                                    # note-off inside this segment
            lvl = round(lvl + (target - lvl) * ((held - t) / dur if dur else 1))
            break
        t, lvl = t + dur, target
        if lvl == 0:
            return t                                          # finished before note-off
    t = held                                                  # (else: held at sustain)
    for target, rate in release_segments:
        if lvl == 0:
            break
        t += _ramp(rate, max(lvl - target, 0))
        lvl = min(lvl, target)
    return t


class Span(NamedTuple):
    start: float        # s, note-on
    free: float         # s, when the voice is released (both envelopes at 0)
    slot: int


def spans(seq: Sequence, bank: Bank) -> list[Span]:
    """A voice span per note. Note-offs match the oldest sounding note of their slot and key.
    A note still sounding at the end is held until the sequence's last item: MI1 re-strikes
    keys that are already sounding, and a few songs (151, 152, 160, 167, 168) have one more
    note-on than note-off for such a key. Whether MIDI Synth's note-off releases one voice
    or every voice of the key isn't documented, so this is the conservative estimate."""
    sec = lambda tick: tick * 30 / (96 * seq.tempo)
    sounding: dict[tuple[int, int], list[float]] = {}

    def span(slot: int, t0: float, t_off: float) -> Span:
        raw = bank.instruments[slot].raw
        held = t_off - t0
        return Span(t0, t0 + max(envelope_end(raw[:ENVELOPE_SIZE], held),
                                 envelope_end(raw[GEN2_ENVELOPE:GEN2_ENVELOPE + ENVELOPE_SIZE], held)), slot)

    out = []
    for it in seq.items:
        kind, slot = it.status & 0xF0, it.status & 0x0F
        if kind not in (0x80, 0x90):
            continue
        key = (slot, it.data[0])
        if kind == 0x90 and it.data[1] > 0:
            sounding.setdefault(key, []).append(sec(it.time))
        elif sounding.get(key):
            out.append(span(slot, sounding[key].pop(0), sec(it.time)))
    end = seq.seconds()
    out += [span(slot, t0, end) for (slot, _), starts in sounding.items() for t0 in starts]
    return sorted(out)


@dataclass
class VoiceReport:
    notes: int
    peak: int                   # most voices needed at once
    seconds_over: float         # time with more than VOICES voices needed
    length: float               # s
    by_slot: Counter[int]       # notes per slot

    @property
    def percent_over(self) -> float:
        return 100 * self.seconds_over / self.length if self.length else 0.0


def report(seq: Sequence, bank: Bank) -> VoiceReport:
    sp = spans(seq, bank)
    # voice starts and ends; at equal times an end comes first (sorts as -1)
    changes = sorted([(s.start, 1) for s in sp] + [(s.free, -1) for s in sp])
    busy = peak = 0
    last = over = 0.0
    for t, d in changes:
        if busy > VOICES:
            over += t - last
        last = t
        busy += d
        peak = max(peak, busy)
    return VoiceReport(len(sp), peak, over, seq.seconds(), Counter(s.slot for s in sp))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seqs", nargs="+", type=Path)
    ap.add_argument("-b", "--bank-dir", type=Path, required=True)
    args = ap.parse_args(argv)
    print(f"{'sequence':<12} {'notes':>5} {'peak':>4} {'>7 voices':>10}")
    for p in args.seqs:
        seq = Sequence.from_bytes(p.read_bytes())
        bank = Bank.from_bytes((args.bank_dir / seq.bank_name).read_bytes())
        r = report(seq, bank)
        print(f"{p.name:<12} {r.notes:5d} {r.peak:4d} {r.seconds_over:6.1f}s {r.percent_over:3.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
