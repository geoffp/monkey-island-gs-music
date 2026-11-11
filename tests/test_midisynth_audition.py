# SPDX-License-Identifier: GPL-3.0-or-later
"""Audition sequences: one hit per beat, and the measure:beat -> key table (audition)."""

from migs.midisynth_audition import audition
from migs.midisynth import Bank, Instrument


def test_audition_sequence_maps_beats_to_keys():
    raw = bytearray(288)
    raw[16] = 48                       # one zone: keys 0-48
    raw[16 + 4] = 0x10                 # wave page $10
    bank = Bank("W.WAV", 1, [Instrument("Kit", bytes(raw))] + [Instrument("", bytes(288))] * 15)
    seq, table = audition("T.BNK", 0, 47, 50, bank, {0x10: ("Kick", 2)})
    ons = [(i.time, i.data[0]) for i in seq.items if i.status == 0x90]
    assert ons == [(384, 47), (480, 48), (576, 49), (672, 50)]     # after a 1-measure lead-in
    assert table[0].split() == ["2:1", "key", "47", "Kick"] and table[2].endswith("silent")
    assert abs(seq.seconds() - (672 + 48) * 30 / (96 * 30)) < 1e-9  # one hit per second
