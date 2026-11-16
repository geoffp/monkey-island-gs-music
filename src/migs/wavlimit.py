# SPDX-License-Identifier: GPL-3.0-or-later
"""Float WAV -> 16-bit WAV, turning down only what is too loud.

Munt's 16-bit output clips a loud song (Atlantis 158 and 161 reach full scale, and the float
render goes to 1.06). `just render` has Munt write 32-bit float and runs this on each file:
a song whose peak is above `--ceiling` is scaled so its peak is the ceiling; quieter songs
are left as they are, so their levels still compare with one another.

    python -m migs.wavlimit IN.wav OUT.wav [--ceiling 0.7]
"""

from __future__ import annotations

import argparse

import numpy as np
from scipy.io import wavfile


def limit(x: np.ndarray, ceiling: float) -> tuple[np.ndarray, float]:
    """(16-bit samples, gain applied). `x` is float in -1..1 (or integer, taken as 16-bit)."""
    if x.dtype.kind in "iu":
        x = x.astype(np.float64) / 32768.0
    x = x.astype(np.float64)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    gain = ceiling / peak if peak > ceiling else 1.0
    return np.round(np.clip(x * gain, -1.0, 32767 / 32768) * 32768).astype(np.int16), gain


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--ceiling", type=float, default=0.7, help="peak above which a song is turned down (default 0.7)")
    a = ap.parse_args(argv)
    rate, x = wavfile.read(a.src)
    y, gain = limit(x, a.ceiling)
    wavfile.write(a.dst, rate, y)
    if gain < 1.0:
        print(f"{a.dst}: turned down {20 * np.log10(gain):.1f} dB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
