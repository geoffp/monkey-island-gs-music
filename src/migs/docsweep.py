# SPDX-License-Identifier: GPL-3.0-or-later
"""DOC volume sweep: a test song for measuring the real DOC's volume curve (docs/es5503.md).

The real Ensoniq DOC's volume register isn't linear ("silent below 17", Ian Brumby's
measurements), and every emulator we use treats it as linear. This writes a MOD that NTP plays
as a measurement. Record the IIgs line out and run `doc-sweep-analyse` on the recording.

Every sample is one 256-byte table looped whole. NTP's converter puts such a sample on one
free-running oscillator (`ntpinstrument::get_osc_count`) and gives track n oscillator n − 1
(`assign oscillators per track`). So the tones sit on known oscillators:

  oscillator 0  (track 1)   TEST: test tone, 7 cycles per table
  oscillator 1  (track 2)   NEIGHBOUR BEFORE: 11 cycles, the slot just before TEST_B
  oscillator 2  (track 3)   TEST_B: test tone, for the neighbour section
  oscillator 3  (track 4)   NEIGHBOUR AFTER: 11 cycles, the slot just after TEST_B (control)
  oscillator 9  (track 10)  PILOT: 22 cycles, constant volume all song, the level reference

At C-3 (period 214, about 16.6 kHz) the tones are near 453, 712 and 1424 Hz: none is a
harmonic of another, so each one's level can be read from the spectrum. Measuring every tone
against the pilot cancels any gain change after the DOC: a mixer, a recorder's automatic level.

Sections, one step = STEP_ROWS rows (0.96 s at speed 6, 125 BPM):
  lead-in   pilot alone (LEAD_ROWS), so the analysis can lock on;
  A         TEST at MOD volume 64, 63, ... 0 (NTP: DOC volume = MOD × 1020/256);
  B         TEST_B at a few quiet volumes, with NEIGHBOUR BEFORE, then AFTER, at several volumes;
  C         TEST with a half-amplitude table at a few volumes: is the threshold on the register
            or on sample × volume?
The step list is written beside the MOD as JSON, for the analysis.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import modfile

TABLE = 256
PERIOD_C3 = 214
SPEED, BPM = 6, 125
ROWS = 64
STEP_ROWS, LEAD_ROWS = 8, 16
TRACKS = 10
TEST, NB_BEFORE, TEST_B, NB_AFTER, PILOT = 0, 1, 2, 3, 9   # track index = oscillator number
CYCLES = {"test": 7, "test_half": 7, "neighbour": 11, "pilot": 22}
SAMPLES = ["test", "test_half", "neighbour", "pilot"]       # MOD sample numbers 1..4
PILOT_VOLUME = 32
NEIGHBOUR_TEST_VOLUMES = (6, 10, 20)
NEIGHBOUR_VOLUMES = (0, 16, 32, 64)
HALF_VOLUMES = (64, 32, 16, 8, 6, 5, 4)


def table(cycles: int, amplitude: float) -> bytes:
    """A 256-byte signed sine table with `cycles` whole cycles. No byte is 0x80 (-128) and the
    converter's unsigned form has no 0 byte, which would halt the oscillator."""
    out = bytearray()
    for i in range(TABLE):
        v = round(amplitude * math.sin(2 * math.pi * cycles * i / TABLE))
        out.append(max(-127, min(127, v)) & 0xFF)
    return bytes(out)


def sample_data() -> dict[str, bytes]:
    return {"test": table(CYCLES["test"], 120), "test_half": table(CYCLES["test_half"], 60),
            "neighbour": table(CYCLES["neighbour"], 120), "pilot": table(CYCLES["pilot"], 120)}


def plan() -> list[dict]:
    """The steps after the lead-in: for each, the volume (0–64) of each sounding track and the
    sample on TEST. Volumes missing from a step are 0."""
    steps = [{"section": "A", "test": v} for v in range(64, -1, -1)]
    for side in ("before", "after"):
        for tv in NEIGHBOUR_TEST_VOLUMES:
            for nv in NEIGHBOUR_VOLUMES:
                if side == "after" and nv == 0:
                    continue                      # the "before" pass has the no-neighbour case
                steps.append({"section": "B", "test_b": tv, f"neighbour_{side}": nv})
    steps += [{"section": "C", "test_half": v} for v in HALF_VOLUMES]
    return steps


def mod_bytes() -> tuple[bytes, list[dict]]:
    steps = plan()
    rows = LEAD_ROWS + STEP_ROWS * len(steps) + STEP_ROWS          # a silent step at the end
    patterns = -(-rows // ROWS)
    cells: list[dict[int, tuple[int, int, int, int]]] = [dict() for _ in range(TRACKS)]

    def put(track: int, row: int, sample: int, eff: int, par: int) -> None:
        cells[track][row] = (PERIOD_C3 if sample else 0, sample, eff, par)

    put(PILOT, 0, SAMPLES.index("pilot") + 1, 0xC, PILOT_VOLUME)
    put(TEST, 0, 0, 0xF, SPEED)
    put(TEST_B, 0, 0, 0xF, BPM)
    # each tone track: (track, sample, key in the plan)
    voices = [(TEST, "test", "test"), (TEST, "test_half", "test_half"),
              (NB_BEFORE, "neighbour", "neighbour_before"), (TEST_B, "test", "test_b"),
              (NB_AFTER, "neighbour", "neighbour_after")]
    playing: dict[int, tuple[str, int]] = {}          # track -> (sample, volume)
    timeline = []
    for i, step in enumerate(steps):
        row = LEAD_ROWS + i * STEP_ROWS
        want: dict[int, tuple[str, int]] = {}
        for track, sample, key in voices:
            if step.get(key):
                want[track] = (sample, step[key])
        for track in (TEST, NB_BEFORE, TEST_B, NB_AFTER):
            new, old = want.get(track), playing.get(track)
            if new and (not old or old[0] != new[0]):
                put(track, row, SAMPLES.index(new[0]) + 1, 0xC, new[1])   # (re)start the tone
            elif new and new[1] != old[1]:
                put(track, row, 0, 0xC, new[1])
            elif old and not new:
                put(track, row, 0, 0xC, 0)
        playing = {t: v for t, v in want.items()}
        timeline.append({**step, "row": row})
    end = LEAD_ROWS + len(steps) * STEP_ROWS
    for track in playing:
        put(track, end, 0, 0xC, 0)
    put(PILOT, end, 0, 0xC, 0)
    put(TEST, rows - 1, 0, 0xF, 0)                    # F00: NTP ends the song

    data = sample_data()
    headers = [name.encode().ljust(22, b"\0") + modfile.SAMPLE_FIELDS.pack(TABLE // 2, 0, 64, 0, TABLE // 2)
               for name in SAMPLES]
    out = bytearray(modfile.pack_head(b"DOC VOLUME SWEEP", headers, patterns, TRACKS))
    for row in range(patterns * ROWS):
        for track in range(TRACKS):
            period, sample, eff, par = cells[track].get(row, (0, 0, 0, 0))
            out += modfile.pack_cell(period, sample, eff, par)
    for name in SAMPLES:
        out += data[name]
    row_s = 2.5 * SPEED / BPM
    meta = {"row_seconds": row_s, "lead_rows": LEAD_ROWS, "step_rows": STEP_ROWS,
            "period": PERIOD_C3, "table": TABLE, "cycles": CYCLES, "pilot_volume": PILOT_VOLUME,
            "oscillators": {"test": TEST, "neighbour_before": NB_BEFORE, "test_b": TEST_B,
                            "neighbour_after": NB_AFTER, "pilot": PILOT},
            "doc_volume": "MOD volume * 1020 // 256 (NTP volume_lookup at play volume 255)",
            "steps": timeline}
    return bytes(out), meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="MOD to write (the step list goes beside it as .json)")
    a = ap.parse_args(argv)
    mod, meta = mod_bytes()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(mod)
    a.out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n")
    secs = (meta["lead_rows"] + meta["step_rows"] * (len(meta["steps"]) + 1)) * meta["row_seconds"]
    print(f"{a.out}: {len(meta['steps'])} steps, {secs:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
