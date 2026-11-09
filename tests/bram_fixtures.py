"""The real battery RAM image the emulator tests start from (not collected: no test_ prefix)."""

from pathlib import Path

# config/emu/default-bram.hex: a BRAM image saved by GSSquared after a real IIgs session.
REAL = bytes.fromhex("".join(
    (Path(__file__).resolve().parents[1] / "config" / "emu" / "default-bram.hex").read_text().split()))
