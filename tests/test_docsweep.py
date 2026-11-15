# SPDX-License-Identifier: GPL-3.0-or-later
"""DOC volume sweep: the MOD it writes, and the analysis on a synthetic recording."""

from __future__ import annotations

import math
import struct

import numpy as np
from scipy.io import wavfile

from migs import docsweep, docsweep_analyse


def test_tables_have_no_halting_bytes():
    # NTP stores samples unsigned (signed ^ 0x80): a signed -128 would become the DOC's 0 stopper
    for name, data in docsweep.sample_data().items():
        assert len(data) == docsweep.TABLE
        assert all((b ^ 0x80) != 0 for b in data), name


def test_mod_layout_and_plan():
    mod, meta = docsweep.mod_bytes()
    assert mod[1080:1084] == b"10CH"
    patterns = mod[950]
    assert len(mod) == 1084 + patterns * 64 * 10 * 4 + docsweep.TABLE * len(docsweep.SAMPLES)
    for i in range(len(docsweep.SAMPLES)):           # each sample: one 256-byte table, looped whole
        words, _ft, vol, loop_start, loop_len = struct.unpack(">HBBHH", mod[42 + 30 * i:50 + 30 * i])
        assert (words, vol, loop_start, loop_len) == (128, 64, 0, 128)
    a = [s["test"] for s in meta["steps"] if s["section"] == "A"]
    assert a == list(range(64, -1, -1))              # every NTP volume, down to silence
    rows = [s["row"] for s in meta["steps"]]
    assert rows == sorted(rows) and rows[0] == docsweep.LEAD_ROWS


def _synth(meta: dict, rate: int, curve) -> np.ndarray:
    """A recording of the sweep through a DOC whose level is curve(DOC volume) (0..1)."""
    sr = docsweep_analyse.PAL_CLOCK / (2 * meta["period"])
    f = {k: c * sr / meta["table"] for k, c in meta["cycles"].items()}
    row = meta["row_seconds"]
    steps = meta["steps"]
    n = int((meta["lead_rows"] + meta["step_rows"] * (len(steps) + 2)) * row * rate)
    t = np.arange(n) / rate
    x = np.zeros(n)
    end = (meta["lead_rows"] + meta["step_rows"] * len(steps)) * row
    lvl = lambda v: curve(docsweep_analyse.doc_volume(v))
    x += np.where(t < end, lvl(meta["pilot_volume"]) * np.sin(2 * math.pi * f["pilot"] * t), 0)
    for i, st in enumerate(steps):
        t0 = st["row"] * row
        m = (t >= t0) & (t < t0 + meta["step_rows"] * row)
        amp = lvl(st.get("test", 0)) + lvl(st.get("test_b", 0)) + 0.5 * lvl(st.get("test_half", 0))
        x[m] += amp * np.sin(2 * math.pi * f["test"] * t[m])
    return 0.2 * x


def test_analysis_recovers_a_threshold_curve(tmp_path):
    _, meta = docsweep.mod_bytes()
    rate = 22050
    curve = lambda d: 0.0 if d < 17 else d / 255
    wav = tmp_path / "rec.wav"
    wavfile.write(wav, rate, (_synth(meta, rate, curve) * 32000).astype(np.int16))
    res = docsweep_analyse.analyse(wav, meta)
    a = {r["test"]: r["test_db"] for r in res["results"] if r["section"] == "A"}
    assert abs(res["row_s"] - meta["row_seconds"]) < 0.002
    assert abs((a[64] - a[32]) - 20 * math.log10(255 / 127)) < 0.3    # linear above the threshold
    assert a[4] < a[64] - 60                                              # DOC 15: silent


def test_report_is_one_self_contained_page(tmp_path):
    from migs import docsweep_report
    _, meta = docsweep.mod_bytes()
    rate = 22050
    wav = tmp_path / "rec.wav"
    wavfile.write(wav, rate, (_synth(meta, rate, lambda d: 0.0 if d < 17 else d / 255) * 32000).astype(np.int16))
    res = docsweep_analyse.analyse(wav, meta)
    csv_path = tmp_path / "m.csv"
    docsweep_analyse.report(res, csv_path)
    page = docsweep_report.render(docsweep_report.load(csv_path))
    assert "{{" not in page and "<script src" not in page and "http://" not in page.replace("http://www.w3.org/2000/svg", "")
    assert page.count("<tr><td>") == 64 and "−0.0<" not in page


def test_curve_file_is_what_docvolume_reads(tmp_path):
    import tomllib
    from migs import docvolume
    _, meta = docsweep.mod_bytes()
    rate = 22050
    flac = tmp_path / "rec.flac"
    import soundfile
    soundfile.write(flac, _synth(meta, rate, lambda d: d / 255), rate)       # FLAC, as kept in measurements/
    res = docsweep_analyse.analyse(flac, meta)
    path = tmp_path / "curve.toml"
    path.write_text(docsweep_analyse.curve_toml(res, flac.name))
    levels = docvolume.load_curve(path)
    assert levels[63] == 0.0 and abs(levels[31] - 20 * math.log10(127 / 255)) < 0.3
    vmap = docvolume.volume_map(levels)                                  # a linear DOC maps to itself, within
    assert vmap[0] == 0 and vmap[64] == 64 and all(abs(v - m) <= 1 for m, v in enumerate(vmap))   # NTP's rounding
