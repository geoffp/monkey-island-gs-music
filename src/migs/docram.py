# SPDX-License-Identifier: GPL-3.0-or-later
"""Ensoniq DOC sound RAM: 8-bit sample conversion and wave placement.

The DOC plays unsigned 8-bit samples from 64 KB of RAM in 256-byte pages; a wave's size is
a power of two from 256 B and it must start on a multiple of its own size. A sample value of
0 halts the oscillator, so 0 never appears in sound data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PAGE = 256
PAGES = 256
SILENCE = 0x80
# Output rate with all 32 oscillators enabled: 7.159 MHz / 8 / (32 + 2) = 894886 / 34 = 26320.2 Hz,
# used rounded (docsim resamples Munt's 32 kHz to it exactly as 329/400).
DOC_RATE = 26320


@dataclass
class Wave:
    name: str
    data: bytes             # a power-of-two number of pages

    @property
    def pages(self) -> int:
        return len(self.data) // PAGE

    @property
    def size_code(self) -> int:
        """DOC wave-size code: 0 for 256 B ... 7 for 32 KB."""
        return int(math.log2(self.pages))


def to_doc(y: np.ndarray, peak: float | None = None) -> bytes:
    """Signed float -> DOC unsigned 8-bit, `peak` (default: the signal's) at full scale;
    0 is never emitted."""
    peak = peak or (float(np.max(np.abs(y))) or 1.0)
    return bytes(np.clip(np.round(128 + 126 * y / peak), 1, 255).astype(np.uint8))


def allocate(waves: list[Wave], first: int, last: int) -> dict[str, int]:
    """Largest-first, size-aligned placement in DOC pages [first, last]. Returns name -> page;
    raises ValueError if a wave doesn't fit."""
    used = [not first <= p <= last for p in range(PAGES)]
    out = {}
    for w in sorted(waves, key=lambda w: -len(w.data)):
        n = w.pages
        p = next((p for p in range(0, PAGES, n) if not any(used[p:p + n])), None)
        if p is None:
            raise ValueError(f"DOC RAM full placing {w.name} ({len(w.data)} B); "
                             f"{used.count(False)} pages free")
        used[p:p + n] = [True] * n
        out[w.name] = p
    return out


def ram_image(waves: list[Wave], pages: dict[str, int]) -> bytes:
    """The 64 KB DOC RAM with each wave at its page, silence elsewhere."""
    ram = bytearray([SILENCE]) * (PAGES * PAGE)
    for w in waves:
        at = pages[w.name] * PAGE
        ram[at:at + len(w.data)] = w.data
    return bytes(ram)
