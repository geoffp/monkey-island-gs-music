"""IIgs battery RAM checksum and patching."""

from migs.bram import STARTUP_SLOT, SLOT_INTERNAL, checksum, is_valid, patch

from pathlib import Path

# config/emu/default-bram.hex: a BRAM image saved by GSSquared after a real IIgs session.
REAL = bytes.fromhex("".join(
    (Path(__file__).resolve().parents[1] / "config" / "emu" / "default-bram.hex").read_text().split()))


def test_checksum_matches_bram_saved_by_a_real_rom_session():
    assert is_valid(REAL)
    assert checksum(REAL) == REAL[0xFC] | REAL[0xFD] << 8


def test_patch_sets_startup_slot_and_disables_slot6_with_a_valid_checksum():
    new = patch(REAL, {STARTUP_SLOT: 5, SLOT_INTERNAL[6]: 1})
    assert new[0x28] == 5 and new[0x26] == 1
    assert is_valid(new) and new[:0x26] == REAL[:0x26]
    assert not is_valid(new[:0x28] + bytes([4]) + new[0x29:])     # any edit needs a new checksum

