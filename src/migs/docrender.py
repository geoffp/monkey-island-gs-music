# SPDX-License-Identifier: GPL-3.0-or-later
"""Render our NinjaTrackerPlus MODs as the IIgs's DOC plays them, for listening away from it.

Desktop players (libopenmpt, behind ffmpeg's previews) smooth a MOD in ways the DOC doesn't:
they interpolate between sample points, ramp volume changes, and mix with linear volume at full
precision. This renders what the real machine does instead (docs/es5503.md):
- each track is one oscillator stepping through its 8-bit sample with no interpolation (a phase
  accumulator at the DOC's output rate, DOC_RATE: 26320 Hz with NTP's 32 oscillators), at
  PAL / period (the sample's finetune table, as NTP's converter does);
- volume changes take effect at once, at the tick they are set, through the real DOC's measured
  volume curve (config/doc-volume.toml; `linear` for an emulator's);
- the oscillators sum in analog: each output sample is held for its scan (zero-order hold) and
  low-passed, as by a stereo card's 17.7 kHz active filter (csa2 FAQ), then resampled to `rate`;
- NTP pans hard: track t goes to one side by `$doc_stereo_defaults` (1 0 0 1 ...: CA0 = 1 is
  left, TN #19).
Only what our MODs use is played: notes, instruments, Cxx, Bxx, Dxx, EDx, Fxx (speed below 32,
tempo from 32, F00 ends the song). A song that loops plays its pass once, as libopenmpt does,
then `tail_s` of what still sounds.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import tomllib
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .docram import DOC_RATE
from .ntpsamples import PAL, period_tables

STEREO = (1, 0, 0, 1)            # ntpconverter_lib.php $doc_stereo_defaults, repeating; 1 = left
FILTER_HZ = 17700.0              # a stereo card's output low-pass (csa2 FAQ 4-channel decoder)
HOLD = 4                         # zero-order hold: output samples per DOC sample before filtering


@dataclass
class Sample:
    data: np.ndarray             # int8
    finetune: int                # -8..7
    volume: int
    loop_start: int              # bytes
    loop_len: int                # bytes; 0 = one-shot


@dataclass
class Mod:
    title: str
    samples: list[Sample]
    orders: list[int]
    channels: int
    patterns: list[list[list[tuple[int, int, int, int]]]]    # [pattern][row][channel] -> (period, inst, eff, par)


def read_mod(data: bytes) -> Mod:
    """A 31-sample MOD with any channel tag our writer uses ('nCHN', 'nnCH', 'M.K.')."""
    title = data[:20].rstrip(b"\0").decode("latin-1")
    heads = []
    for i in range(31):
        h = data[20 + 30 * i:50 + 30 * i]
        words, ft, vol, ls, ll = struct.unpack(">HBBHH", h[22:30])
        heads.append((words * 2, ft & 0x0F, vol, ls * 2, ll * 2 if ll > 1 else 0))
    length = data[950]
    orders = list(data[952:952 + 128])[:length]
    tag = data[1080:1084].decode("latin-1")
    if tag in ("M.K.", "M!K!", "FLT4", "4CHN"):
        channels = 4
    elif tag.endswith("CHN"):
        channels = int(tag[0])
    elif tag.endswith("CH"):
        channels = int(tag[:2])
    else:
        raise ValueError(f"unknown MOD channel tag {tag!r}")
    n_pat = max(list(data[952:952 + 128])) + 1
    pos = 1084
    patterns = []
    for _ in range(n_pat):
        rows = []
        for _r in range(64):
            row = []
            for _c in range(channels):
                a, b = struct.unpack(">HH", data[pos:pos + 4])
                pos += 4
                row.append((a & 0x0FFF, (a >> 8 & 0xF0) | (b >> 12), b >> 8 & 0x0F, b & 0xFF))
            rows.append(row)
        patterns.append(rows)
    samples = []
    for n, ft, vol, ls, ll in heads:
        raw = np.frombuffer(data[pos:pos + n], dtype=np.int8).copy()
        pos += n
        samples.append(Sample(raw, ft - 16 if ft > 7 else ft, vol, ls, ll if ls + ll <= n else 0))
    return Mod(title, samples, orders, channels, patterns)


def volume_gains(curve: str | Path | None) -> np.ndarray:
    """Output gain per MOD volume 0..64: the measured curve (doc-volume.toml level_db) or linear."""
    g = np.arange(65) / 64.0
    if curve is not None:
        levels = tomllib.loads(Path(curve).read_text())["level_db"]
        g = np.concatenate([[0.0], 10 ** (np.asarray(levels, dtype=np.float64) / 20)])
    return g


@dataclass
class _Voice:
    sample: Sample | None = None
    inst: int = 0
    pos: float = 0.0
    step: float = 0.0
    volume: int = 0
    on: bool = False
    pending: tuple | None = field(default=None)     # (tick, period, inst) of a delayed note


def _step(mod: Mod, inst: int, period: int, note_of: dict[int, int], tables) -> float:
    s = mod.samples[inst - 1]
    idx = note_of.get(period)
    p = tables[s.finetune][idx] if idx is not None else period
    return PAL / p / DOC_RATE


def render_doc(mod: Mod, tables, gains: np.ndarray, tail_s: float = 1.0, max_s: float = 600.0) -> np.ndarray:
    """(DOC samples x 2) at DOC_RATE: the song as the DOC's two summed sides."""
    note_of = {p: i for i, p in enumerate(tables[0])}
    voices = [_Voice() for _ in range(mod.channels)]
    side = np.array([STEREO[c % len(STEREO)] for c in range(mod.channels)])
    out: list[np.ndarray] = []
    speed, tempo, carry = 6, 125, 0.0
    order, row, seen, ending, total = 0, 0, set(), None, 0

    def trigger(v: _Voice, period: int, inst: int) -> None:
        if inst:
            v.inst = inst
        if period and v.inst and mod.samples[v.inst - 1].data.size:
            v.sample, v.pos, v.on = mod.samples[v.inst - 1], 0.0, True
            v.step = _step(mod, v.inst, period, note_of, tables)

    def play(n: int) -> None:
        buf = np.zeros((n, 2))
        for c, v in enumerate(voices):
            if not v.on or v.volume == 0 or v.sample is None:
                if v.on and v.sample is not None:           # silent but moving on
                    v.pos += v.step * n
                continue
            s = v.sample
            p = v.pos + v.step * np.arange(n)
            i = p.astype(np.int64)
            if s.loop_len:
                end = s.loop_start + s.loop_len
                i = np.where(i < end, i, s.loop_start + (i - s.loop_start) % s.loop_len)
                y = s.data[i].astype(np.float64)
            else:
                ok = i < s.data.size
                y = np.where(ok, s.data[np.minimum(i, s.data.size - 1)], 0).astype(np.float64)
                if not ok[-1]:
                    v.on = False
            buf[:, 1 - side[c]] += y / 128.0 * gains[v.volume]
            v.pos += v.step * n
            if s.loop_len and v.pos >= s.loop_start + s.loop_len:
                v.pos = s.loop_start + (v.pos - s.loop_start) % s.loop_len
        out.append(buf)

    while total < max_s * DOC_RATE:
        if order >= len(mod.orders):
            break
        if (order, row) == (0, 0) and seen:
            break
        cells = mod.patterns[mod.orders[order]][row]
        jump = brk = None
        stop = False
        for c, (period, inst, eff, par) in enumerate(cells):
            v = voices[c]
            delay = par & 0x0F if eff == 0xE and par >> 4 == 0xD else 0
            if inst and not delay:
                v.inst = inst
                v.volume = mod.samples[inst - 1].volume
            if delay:
                v.pending = (delay, period, inst)
            elif period:
                trigger(v, period, inst)
            if eff == 0xC:
                v.volume = min(par, 64)
            elif eff == 0xF:
                if par == 0:
                    stop = True
                elif par < 32:
                    speed = par
                else:
                    tempo = par
            elif eff == 0xB:
                jump = par
            elif eff == 0xD:
                brk = (par >> 4) * 10 + (par & 0x0F)
        for tick in range(speed):
            for v in voices:
                if v.pending and v.pending[0] == tick:
                    _, period, inst = v.pending
                    if inst:
                        v.inst, v.volume = inst, mod.samples[inst - 1].volume
                    trigger(v, period, inst)
                    v.pending = None
            carry += 2.5 / tempo * DOC_RATE
            n = int(carry)
            carry -= n
            play(n)
            total += n
        for v in voices:
            v.pending = None
        seen.add((order, row))
        if stop:
            break
        if jump is not None or brk is not None:
            nxt = (jump if jump is not None else order + 1, brk or 0)
            if nxt in seen:                         # the song loops: one pass, as libopenmpt plays it
                break
            order, row = nxt
        else:
            row += 1
            if row == 64:
                order, row = order + 1, 0
    play(int(tail_s * DOC_RATE))
    return np.concatenate(out) if out else np.zeros((0, 2))


def to_output(doc: np.ndarray, rate: int = 48000, block: int = 1 << 15) -> np.ndarray:
    """DOC-rate sides -> `rate`: zero-order hold, the output low-pass, then resampled: what
    scipy's resample_poly(sosfilt(repeat(doc, HOLD))) gives, the same sums, a `block` of DOC
    samples at a time. Whole, the held and filtered copies came to 1.1 GB for MI2's longest
    song (4 minutes), with eight renders at once in a build (2026-11-17)."""
    from scipy.signal import butter, firwin, sosfilt, upfirdn
    fs = DOC_RATE * HOLD
    g = math.gcd(int(fs), rate)
    up, down = rate // g, int(fs) // g
    n_in = doc.shape[0] * HOLD
    n_out = -(-n_in * up // down)
    ch = doc.shape[1]
    out = np.zeros((n_out, ch))
    if n_in == 0:
        return out
    sos = butter(2, FILTER_HZ, fs=fs, output="sos")
    zi = np.zeros((sos.shape[0], 2, ch))
    # resample_poly's filter and alignment (scipy.signal._signaltools.resample_poly, padtype
    # "constant"): output j is upfirdn output j + pre, upfirdn's m = sum_n x[n] h[m*down - n*up]
    half = 10 * max(up, down)
    pad = down - half % down
    h = np.concatenate([np.zeros(pad), firwin(2 * half + 1, 1.0 / max(up, down), window=("kaiser", 5.0)) * up])
    pre = (half + pad) // down
    taps = h.size
    buf, b0 = np.zeros((0, ch)), 0          # filtered input from b0 (a multiple of down: same phases)
    j = 0                                   # next output
    for at in range(0, doc.shape[0], block):
        y, zi = sosfilt(sos, np.repeat(doc[at:at + block], HOLD, axis=0), axis=0, zi=zi)
        buf = np.concatenate([buf, y])
        end = b0 + buf.shape[0]
        last = end >= n_in
        # outputs whose inputs are all here: m*down/up < end (or every input is)
        j_hi = n_out if last else min(n_out, max(j, ((end - 1) * up) // down - pre + 1))
        if j_hi > j:
            x = buf if not last else np.concatenate([buf, np.zeros((taps // up + 2, ch))])
            m0 = b0 * up // down
            ys = upfirdn(h, x, up, down, axis=0)
            out[j:j_hi] = ys[j + pre - m0:j_hi + pre - m0]
            j = j_hi
        # keep what the next output needs: n >= ((j + pre)*down - taps + 1)/up, from a multiple of down
        need = max(0, -(-((j + pre) * down - taps + 1) // up))
        cut = max(b0, need // down * down)
        buf, b0 = buf[cut - b0:], cut
    return out


def write_wav(path: Path, y: np.ndarray, rate: int, block: int = 1 << 18) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(y.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        for at in range(0, y.shape[0], block):         # a block at a time: three temporaries each
            w.writeframes(np.clip(np.round(y[at:at + block] * 32767), -32768, 32767).astype("<i2").tobytes())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mods", type=Path, nargs="+")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output folder for the WAVs")
    ap.add_argument("--make-tables", type=Path, default=Path("vendor/ntp/src/make_tables.php"))
    ap.add_argument("--curve", type=Path, default=Path("config/doc-volume.toml"),
                    help="the real DOC's volume curve (default); --linear for an emulator's")
    ap.add_argument("--linear", action="store_true")
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--gain", type=float, default=0.25, help="output gain (a track at full volume = this)")
    a = ap.parse_args(argv)
    tables = period_tables(a.make_tables)
    gains = volume_gains(None if a.linear else a.curve)
    a.out.mkdir(parents=True, exist_ok=True)
    for f in a.mods:
        y = to_output(render_doc(read_mod(f.read_bytes()), tables, gains), a.rate)
        y *= a.gain
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        if peak > 1.0:
            print(f"{f.name}: peak {peak:.2f} at gain {a.gain}: scaled down to fit", file=sys.stderr)
            y /= peak
        write_wav(a.out / f"{f.stem}.wav", y, a.rate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
