# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the DOC volume curve from a recording of the DOC volume sweep (`doc-sweep`).

Input: the recording (WAV, any rate, mono or stereo; channels are summed) and the step list
written beside the MOD. The pilot tone runs from the lead-in to the end, so its onset and
offset give the song's start and its real row length (players don't keep exact time). Each
step is measured from 25 % to 90 % of its length: the level of each tone (a Hann-windowed DFT
at the strongest bin within ±3 % of its expected frequency), in dB relative to the pilot, so
gain changes after the DOC cancel.

Output: a table per section, and a CSV (step, section, DOC volume, levels) for the curve.
Section A's levels are also given relative to a linear DOC (level at volume 64 scaled by
volume / 64): 0 dB everywhere means linear, as in the emulators.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.io import wavfile

PAL_CLOCK = 7093789.2


def doc_volume(mod_volume: int) -> int:
    return mod_volume * 1020 // 256


def load(path: Path) -> tuple[np.ndarray, int]:
    rate, data = wavfile.read(path)
    x = data.astype(np.float64)
    if data.dtype.kind == "i":
        x /= float(np.iinfo(data.dtype).max)
    elif data.dtype.kind == "u":
        x = (x - 128.0) / 128.0
    if x.ndim > 1:
        x = x.sum(axis=1)
    return x, rate


def tone_level(seg: np.ndarray, rate: int, freq: float, search: float = 0.03) -> tuple[float, float]:
    """(amplitude, frequency) of the strongest DFT bin within ±search of freq."""
    n = len(seg)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(seg * win)) * 2 / win.sum()
    lo = max(1, int(freq * (1 - search) * n / rate))
    hi = min(len(spec) - 1, int(math.ceil(freq * (1 + search) * n / rate)))
    k = lo + int(np.argmax(spec[lo:hi + 1]))
    return float(spec[k]), k * rate / n


def envelope(x: np.ndarray, rate: int, freq: float, search: float = 0.03,
             hop_s: float = 0.02, win_s: float = 0.08):
    """Level of a tone over time: (times, amplitudes)."""
    hop, win = int(hop_s * rate), int(win_s * rate)
    t, a = [], []
    for start in range(0, len(x) - win, hop):
        t.append((start + win / 2) / rate)
        a.append(tone_level(x[start:start + win], rate, freq, search)[0])
    return np.array(t), np.array(a)


def db(a: float) -> float:
    return 20 * math.log10(a) if a > 0 else -math.inf


def analyse(wav: Path, meta: dict) -> dict:
    x, rate = load(wav)
    sample_rate = PAL_CLOCK / (2 * meta["period"])
    f = {k: c * sample_rate / meta["table"] for k, c in meta["cycles"].items()}
    # find the pilot's real frequency from the loudest second, then its on and off times
    t, a = envelope(x, rate, f["pilot"], search=0.06)
    peak = int(np.argmax(a))
    s0 = max(0, int((t[peak] - 0.5) * rate))
    _, f_pilot = tone_level(x[s0:s0 + rate], rate, f["pilot"], search=0.06)
    scale = f_pilot / f["pilot"]
    f = {k: v * scale for k, v in f.items()}
    t, a = envelope(x, rate, f["pilot"], search=0.01)
    on = a > 0.5 * np.median(a[a > 0.1 * a.max()])
    t_on, t_off = t[np.argmax(on)], t[len(on) - 1 - np.argmax(on[::-1])]
    steps = meta["steps"]
    rows = meta["lead_rows"] + meta["step_rows"] * len(steps)
    row_s = (t_off - t_on) / rows                      # measured row length
    results = []
    for i, st in enumerate(steps):
        t0 = t_on + st["row"] * row_s
        dur = meta["step_rows"] * row_s
        seg = x[int((t0 + 0.25 * dur) * rate):int((t0 + 0.9 * dur) * rate)]
        pilot = tone_level(seg, rate, f["pilot"], 0.01)[0]
        lv = {k: db(tone_level(seg, rate, f[c], 0.01)[0] / pilot) if pilot > 0 else -math.inf
              for k, c in (("test", "test"), ("neighbour", "neighbour"))}
        results.append({**st, "pilot_db": db(pilot), **{f"{k}_db": v for k, v in lv.items()}})
    return {"rate": rate, "freqs": f, "row_s": row_s, "start_s": t_on, "results": results,
            "nominal_row_s": meta["row_seconds"]}


def report(res: dict, out_csv: Path | None) -> str:
    lines = [f"rows {res['row_s'] * 1000:.2f} ms (nominal {res['nominal_row_s'] * 1000:.2f}); "
             f"song starts {res['start_s']:.2f} s; tones "
             + ", ".join(f"{k} {v:.1f} Hz" for k, v in res["freqs"].items())]
    rs = res["results"]
    a = [r for r in rs if r["section"] == "A"]
    floor = next((r["test_db"] for r in a if r["test"] == 0), -math.inf)
    top = next(r["test_db"] for r in a if r["test"] == 64)
    lines.append(f"\nA: test tone vs pilot (noise floor at volume 0: {floor:.1f} dB)")
    lines.append(" MOD  DOC   level dB  vs linear dB")
    for r in a:
        if r["test"] == 0:
            continue
        lin = top + db(r["test"] / 64)
        lines.append(f" {r['test']:3d}  {doc_volume(r['test']):3d}  {r['test_db']:8.1f}  {r['test_db'] - lin:+8.1f}")
    lines.append("\nB: test tone on oscillator 2 (vs pilot, dB) with a neighbour on oscillator 1 (before) or 3 (after)")
    for r in rs:
        if r["section"] != "B":
            continue
        side = "before" if "neighbour_before" in r else "after"
        nv = r.get(f"neighbour_{side}", 0)
        lines.append(f" test {r['test_b']:2d} (DOC {doc_volume(r['test_b']):3d})  neighbour {side:6s} {nv:2d} (DOC {doc_volume(nv):3d})"
                     f"  test {r['test_db']:7.1f}  neighbour {r['neighbour_db']:7.1f}")
    lines.append("\nC: half-amplitude test table (vs pilot, dB)")
    for r in rs:
        if r["section"] == "C":
            lines.append(f" MOD {r['test_half']:2d} (DOC {doc_volume(r['test_half']):3d})  {r['test_db']:7.1f}")
    if out_csv:
        with out_csv.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["step", "section", "test", "test_b", "test_half", "neighbour_before",
                        "neighbour_after", "doc_test", "test_db", "neighbour_db", "pilot_db"])
            for i, r in enumerate(rs):
                v = r.get("test") or r.get("test_b") or r.get("test_half") or 0
                w.writerow([i, r["section"], r.get("test", ""), r.get("test_b", ""), r.get("test_half", ""),
                            r.get("neighbour_before", ""), r.get("neighbour_after", ""), doc_volume(v),
                            f"{r['test_db']:.2f}", f"{r['neighbour_db']:.2f}", f"{r['pilot_db']:.2f}"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("wav", type=Path, help="recording of the sweep (WAV)")
    ap.add_argument("steps", type=Path, help="the step list doc-sweep wrote (DOCSWEEP.json)")
    ap.add_argument("--csv", type=Path, help="write the measurements here")
    a = ap.parse_args(argv)
    res = analyse(a.wav, json.loads(a.steps.read_text()))
    print(report(res, a.csv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
