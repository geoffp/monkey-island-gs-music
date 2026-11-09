"""The ProTracker MOD writer (modfile)."""

import struct

import numpy as np

from migs import modfile
from migs import ntpsamples as smp
from migs import ntpsong as sg


def test_mod_bytes_layout():
    s = smp.ModSample("Sine", np.sin(np.linspace(0, 6.28, 100)), loop_start=40, loop_len=60, finetune=-3)
    s.default_volume = 40
    g = sg.Grid(12)
    c = g.get(3, 5)
    c[:] = [25, 1, 0xE, 0xD2]            # note index 24 (C-2), sample 1, ED2
    tables = {0: [1712 - i for i in range(72)]}
    tables[0][24] = 428
    mod = modfile.mod_file("Test", [s], g, 1, tables)
    assert mod[:4] == b"Test" and mod[1080:1084] == b"12CH"
    name, words, ft, vol, ls, ll = struct.unpack(">22sHBBHH", mod[20:50])
    assert (words, ft, vol, ls, ll) == (50, 0x0D, 40, 20, 30)   # lengths/loops in words; ft -3 = 0xD
    assert mod[950] == 1 and mod[952] == 0                      # song length 1, order [0]
    o = 1084 + (5 * 12 + 3) * 4
    w0, w1 = struct.unpack(">HH", mod[o:o + 4])
    assert w0 & 0xFFF == 428 and (w0 >> 12) == 0 and (w1 >> 12) == 1 and w1 & 0xFFF == 0xED2
    assert len(mod) == 1084 + 64 * 12 * 4 + 100
    assert modfile.mod_file("T", [s], sg.Grid(9), 1, tables)[1080:1084] == b"9CHN"
