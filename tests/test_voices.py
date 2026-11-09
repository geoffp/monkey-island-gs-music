"""MIDI Synth voice-occupancy estimate (voices): envelope tails, unreleased notes."""

import pytest

from migs.midisynth import Bank, Instrument, SeqItem, Sequence, envelope, wavelist
from migs.voices import envelope_end, report, spans


def test_voice_estimate_counts_envelope_tails_not_note_length():
    held = envelope(sustain=127, release1=(0, 16))                     # 0.43 s full release
    assert envelope_end(held, 2.0) == pytest.approx(2.43, abs=0.01)
    decays = envelope(sustain=0, decay3_rate=16)                       # falls to 0 by itself
    assert envelope_end(decays, 5.0) == pytest.approx(0.43, abs=0.01)


def test_a_note_never_released_holds_its_voice_to_the_end():
    inst = Instrument.build("I", (envelope(release1=(0, 31)), [wavelist(127, 0, (0, 0, 255, 4, 0, 0))]))
    bank = Bank("W", 1, [inst] * 16)
    on = lambda t, k: SeqItem(0, t, 0x90, bytes([k, 100]))
    off = lambda t, k: SeqItem(0, t, 0x80, bytes([k, 64]))
    seq = Sequence("B", [on(0, 60), on(96, 60), off(192, 60), on(960, 62), off(1920, 62)], tempo=60)
    r = report(seq, bank)
    assert r.notes == 3 and r.peak == 2          # at tick 960: key 62 and the unreleased 60
    assert max(s.free for s in spans(seq, bank)) >= seq.seconds()
