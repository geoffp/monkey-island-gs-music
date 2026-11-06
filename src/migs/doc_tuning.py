# SPDX-License-Identifier: GPL-3.0-or-later
"""How MIDI Synth plays a wave, as measured in GSSquared (docs/iigs-midisynth.md "Measured").

bytes/s = B0 * 2^((key - 69)/12 + t) * 2^(size - res)
  B0  = 26320 / 512 * 2197  (DOC rate with 32 oscillators; freq register for A4 at octave 4)
  t   = (octave - 4) + (semitone + fine/64) / 12
  res = 0 if size <= 2 (single-cycle mode) else size (sample mode)
"""

from __future__ import annotations

import math

DOC_RATE = 26320                     # Hz with 32 oscillators enabled
FREQ_A4_OCT4 = 2197                  # measured DOC freq register
B0 = DOC_RATE / 512 * FREQ_A4_OCT4   # bytes/s at A4, octave 4, sample mode
ENVELOPE_RATE_SECONDS = [            # ERS p.16: time to ramp the full 0-127 range, per rate
    13.08, 10.53, 8.55, 6.92, 5.59, 4.52, 3.66, 2.96, 2.39, 1.93, 1.56, 1.26, 1.02, 0.82, 0.64, 0.54,
    0.43, 0.32, 0.28, 0.23, 0.18, 0.16, 0.12, 0.09, 0.08, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01, 0.0,
]


def resolution(size: int) -> int:
    return 0 if size <= 2 else size


def bytes_per_second(key: int, t: float, size: int) -> float:
    return B0 * 2 ** ((key - 69) / 12 + t) * 2 ** (size - resolution(size))


def tuning_for(rate: float, key: int, size: int) -> float:
    """t that plays a wave of `size` at `rate` bytes/s when `key` is played."""
    return math.log2(rate / (B0 * 2 ** ((key - 69) / 12) * 2 ** (size - resolution(size))))


def single_cycle_tuning(cycles: float = 1.0) -> float:
    """t for a looped single-cycle-mode wave holding `cycles` periods (in tune at every key)."""
    return math.log2(440 * 256 / (B0 * cycles))


def encode(t: float) -> tuple[int, int, int]:
    """t -> (octave 0-6, semitone 0-11, fine 0-63)."""
    total = round((12 * t + 48) * 64)          # in 1/64 semitones above octave 0
    octave, rem = divmod(total, 12 * 64)
    semitone, fine = divmod(rem, 64)
    if not 0 <= octave <= 6:
        raise ValueError(f"tuning {t:+.3f} octaves is outside MIDI Synth's range (octave {octave})")
    return octave, semitone, fine


def decode(octave: int, semitone: int, fine: int) -> float:
    return octave - 4 + (semitone + fine / 64) / 12


def rate_for(seconds_full_scale: float) -> int:
    """Envelope rate whose full-scale ramp time is closest (in log time) to the target."""
    s = max(seconds_full_scale, 0.005)
    return min(range(32), key=lambda r: abs(math.log(max(ENVELOPE_RATE_SECONDS[r], 0.005) / s)))


def segment_rate(level_from: int, level_to: int, seconds: float) -> int:
    """Rate that moves between two envelope levels in about `seconds`."""
    span = abs(level_to - level_from)
    return 31 if span == 0 else rate_for(seconds * 127 / span)
