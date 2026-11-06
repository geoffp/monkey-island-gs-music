"""MIDI Synth file formats: byte layout, round trips, TopKey zones."""

import struct
from pathlib import Path

import pytest

from migs.midisynth import END_OF_SEQ, SEQ_HEADER_SIZE, Bank, Instrument, SeqItem, Sequence

STOCK = Path(__file__).resolve().parents[1] / "vendor" / "synthlab"


def test_ers_example_items_pack_exactly():
    # MIDI Synth ERS p.25: middle C on instrument 6 at beat 1, E on instrument 12 at beat 2,
    # both off on beat 4. Time stamp bytes are high, low, mid.
    items = [SeqItem(8, 0, 0x95, bytes([0x3C, 0x7F])), SeqItem(0, 96, 0x98, bytes([0x40, 0x7F])),
             SeqItem(8, 288, 0x85, bytes([0x3C, 0x7F])), SeqItem(0, 288, 0x88, bytes([0x40, 0x7F]))]
    assert [i.pack().hex(" ") for i in items] == [
        "08 00 00 00 02 95 3c 7f", "00 00 60 00 02 98 40 7f",
        "08 00 20 01 02 85 3c 7f", "00 00 20 01 02 88 40 7f",
    ]
    assert SeqItem.unpack(bytes.fromhex("0812345602903c7f")).time == 0x125634


def test_sequence_header_layout_and_end_marker():
    s = Sequence(bank_name="COMBO.BNK", tempo=75, items=[SeqItem(0, 0, 0xC3, bytes([5]))])
    s.channel_volume[3] = 100
    b = s.to_bytes()
    assert b[:4] == b"MSEQ" and struct.unpack_from("<HH", b, 4) == (0x0100, SEQ_HEADER_SIZE)
    # bank name: Pascal string followed by the bank's file type/aux type, as synthLAB writes it
    assert b[0x18:0x28] == b"\x09COMBO.BNK" + bytes([0xD6, 0, 1, 0]) + bytes(2)
    assert struct.unpack_from("<H", b, 0x128 + 3 * 2)[0] == 100     # c_volume[3]
    assert struct.unpack_from("<H", b, 0x18A)[0] == 75              # tempo (half-note BPM)
    assert struct.unpack_from("<H", b, 0x190)[0] == 96              # ticks per beat
    assert b[SEQ_HEADER_SIZE:SEQ_HEADER_SIZE + 8] == bytes.fromhex("0000000001c30500")
    assert b.endswith(END_OF_SEQ) and len(b) == SEQ_HEADER_SIZE + 8 + 2


def test_sequence_round_trip():
    s = Sequence(bank_name="ORCH.BNK", tempo=67, track_names=["Lead"] + [""] * 15,
                 items=[SeqItem(1, 1000, 0x92, b"\x40\x50"), SeqItem(1, 70000, 0x82, b"\x40\x40")])
    back = Sequence.from_bytes(s.to_bytes())
    assert back.items == s.items and back.bank_name == "ORCH.BNK" and back.tempo == 67
    assert back.track_names[0] == "Lead"
    assert abs(back.seconds() - 70000 * 30 / (96 * 67)) < 1e-9


def test_writer_rejects_out_of_order_items_and_bad_tempo():
    with pytest.raises(ValueError):
        Sequence("X.BNK", items=[SeqItem(0, 10, 0x90, b"\x3c\x40"), SeqItem(0, 5, 0x80, b"\x3c\x40")]).to_bytes()
    with pytest.raises(ValueError):
        Sequence("X.BNK", tempo=200).to_bytes()


def test_topkey_zones_follow_first_match_scan():
    # ERS p.18 example: TopKeys 59, 70, 62, 84, 72, 127 -> wavelists 3 and 5 are never used.
    raw = bytearray(288)
    for i, top in enumerate([59, 70, 62, 84, 72, 127, 0, 0]):
        raw[16 + i * 16] = top
    zones = [(lo, hi) for lo, hi, _ in Instrument("t", bytes(raw)).zones()]
    assert zones == [(0, 59), (60, 70), (71, 84), (85, 127)]


@pytest.mark.skipif(not (STOCK / "BEE.SEQ").exists(), reason="run `just stock-banks`")
def test_stock_sequences_round_trip_semantically():
    for f in sorted(STOCK.glob("*.SEQ")):
        s = Sequence.from_bytes(f.read_bytes())
        again = Sequence.from_bytes(s.to_bytes())
        assert again.items == s.items and again.tempo == s.tempo, f.name
        times = [i.time for i in s.items]
        assert times == sorted(times), f.name


@pytest.mark.skipif(not (STOCK / "COMBO.BNK").exists(), reason="run `just stock-banks`")
def test_stock_combo_bank():
    bank = Bank.from_bytes((STOCK / "COMBO.BNK").read_bytes())
    assert bank.wave_name == "Combo.wav"
    assert [i.name for i in bank.instruments[:3]] == ["Drum Kit 1", "Slap Bass", "Synth Lead"]
    kit = [(lo, hi) for lo, hi, _ in bank.instruments[0].zones()]
    assert kit == [(0, 48), (49, 56), (57, 72), (73, 78), (79, 84), (85, 127)]


def test_audition_sequence_maps_beats_to_keys():
    from migs.audition import audition
    raw = bytearray(288)
    raw[16] = 48                       # one zone: keys 0-48
    raw[16 + 4] = 0x10                 # wave page $10
    bank = Bank("W.WAV", 1, [Instrument("Kit", bytes(raw))] + [Instrument("", bytes(288))] * 15)
    seq, table = audition("T.BNK", 0, 47, 50, bank, {0x10: ("Kick", 2)})
    ons = [(i.time, i.data[0]) for i in seq.items if i.status == 0x90]
    assert ons == [(384, 47), (480, 48), (576, 49), (672, 50)]     # after a 1-measure lead-in
    assert table[0].split() == ["2:1", "key", "47", "Kick"] and table[2].endswith("silent")
    assert abs(seq.seconds() - (672 + 48) * 30 / (96 * 30)) < 1e-9  # one hit per second


def test_bank_and_wave_writers_round_trip():
    from migs.midisynth import WaveBank, envelope, wavelist
    env = envelope(sustain=100)
    inst = Instrument.build("Sine", (env, [wavelist(127, 0, (0x10, 0, 120, 3, 0, 0))]))
    bank = Bank("T.WAV", 1, [inst] + [Instrument("", bytes(288))] * 15)
    raw = bank.to_bytes()
    assert len(raw) == 5632 and raw[:4] == b"INST"
    back = Bank.from_bytes(raw)
    assert back.wave_name == "T.WAV" and back.instruments[0].name == "Sine"
    assert back.instruments[0].raw == inst.raw and inst.raw[:16] == env
    (lo, hi, wl), = back.instruments[0].zones()
    assert (lo, hi, wl.addr_a, wl.vol_a, wl.vol_b) == (0, 127, 0x10, 120, 0)   # gen 2 silent copy
    ram = bytearray(0x10000)
    ram[0x1000:0x1100] = bytes(range(1, 256)) + b"\x01"
    wraw = WaveBank({0x10: ("Sine", 0)}, bytes(ram)).to_bytes()
    assert len(wraw) == 67840
    wb = WaveBank.from_bytes(wraw)
    assert wb.waves == {0x10: ("Sine", 0)} and wb.ram == bytes(ram)
