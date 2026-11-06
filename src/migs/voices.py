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
from pathlib import Path

from .doc_tuning import ENVELOPE_RATE_SECONDS as RATE_T
from .midisynth import Bank, Sequence

VOICES = 7


def envelope_end(e: bytes, held: float) -> float:
    """Seconds from note-on until a 16-byte EnvelopeRec reaches zero, for a note held `held` s."""
    t, lvl = 0.0, 0
    for target, rate in ((e[0], e[1]), (e[2], e[3]), (e[4], e[5]), (e[6], e[7])):
        dur = RATE_T[rate] * abs(target - lvl) / 127
        if t + dur >= held:                                    # note-off inside this segment
            lvl = round(lvl + (target - lvl) * ((held - t) / dur if dur else 1))
            t = held
            break
        t, lvl = t + dur, target
        if lvl == 0:
            return t                                          # finished before note-off
    else:
        t = held                                              # held at sustain until note-off
    for target, rate in ((e[8], e[9]), (e[10], e[11]), (0, e[12])):
        if lvl == 0:
            break
        t += RATE_T[rate] * max(lvl - target, 0) / 127
        lvl = min(lvl, target)
    return t


def spans(seq: Sequence, bank: Bank) -> list[tuple[float, float, int]]:
    """(start s, voice-free s, slot) per note."""
    sec = lambda tick: tick * 30 / (96 * seq.tempo)
    on: dict[tuple[int, int], list[float]] = {}
    out = []
    for it in seq.items:
        st, ch = it.status & 0xF0, it.status & 0x0F
        if st == 0x90 and it.data[1] > 0:
            on.setdefault((ch, it.data[0]), []).append(sec(it.time))
        elif st in (0x80, 0x90) and on.get((ch, it.data[0])):
            t0 = on[(ch, it.data[0])].pop(0)
            raw = bank.instruments[ch].raw
            held = sec(it.time) - t0
            out.append((t0, t0 + max(envelope_end(raw[:16], held), envelope_end(raw[144:160], held)), ch))
    return out


def report(seq: Sequence, bank: Bank) -> dict:
    sp = spans(seq, bank)
    events = sorted([(a, 1) for a, _, _ in sp] + [(b, -1) for _, b, _ in sp])
    cur = peak = 0
    last = over = 0.0
    for t, d in events:
        if cur > VOICES:
            over += t - last
        last = t
        cur += d
        peak = max(peak, cur)
    by_slot = Counter(ch for _, _, ch in sp)
    return {"notes": len(sp), "peak": peak, "seconds_over": over,
            "length": seq.seconds(), "by_slot": by_slot}


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
        pct = 100 * r["seconds_over"] / r["length"] if r["length"] else 0
        print(f"{p.name:<12} {r['notes']:5d} {r['peak']:4d} {r['seconds_over']:6.1f}s {pct:3.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
