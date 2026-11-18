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
    out = list(doc_blocks(mod, tables, gains, tail_s, max_s))
    return np.concatenate(out) if out else np.zeros((0, 2))


def doc_blocks(mod: Mod, tables, gains: np.ndarray, tail_s: float = 1.0, max_s: float = 600.0):
    """render_doc a tick at a time: (n x 2) blocks at DOC_RATE, in order."""
    note_of = {p: i for i, p in enumerate(tables[0])}
    voices = [_Voice() for _ in range(mod.channels)]
    side = np.array([STEREO[c % len(STEREO)] for c in range(mod.channels)])
    speed, tempo, carry = 6, 125, 0.0
    order, row, seen, ending, total = 0, 0, set(), None, 0

    def trigger(v: _Voice, period: int, inst: int) -> None:
        if inst:
            v.inst = inst
        if period and v.inst and mod.samples[v.inst - 1].data.size:
            v.sample, v.pos, v.on = mod.samples[v.inst - 1], 0.0, True
            v.step = _step(mod, v.inst, period, note_of, tables)

    def play(n: int) -> np.ndarray:
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
        return buf

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
            yield play(n)
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
    yield play(int(tail_s * DOC_RATE))


class Output:
    """DOC-rate sides -> `rate`, fed as they come: zero-order hold, the output low-pass, then
    resampled. The same sums as scipy's resample_poly(sosfilt(repeat(doc, HOLD))) of the whole
    song, `block` DOC samples at a time: whole, the held and filtered copies came to 1.1 GB for
    MI2's longest song (4 minutes), with eight renders at once in a build (2026-11-17).
    feed() and finish() return the output samples now complete."""

    def __init__(self, rate: int = 48000, channels: int = 2, block: int = 1 << 15):
        from scipy.signal import butter, firwin
        fs = DOC_RATE * HOLD
        g = math.gcd(int(fs), rate)
        self.up, self.down = rate // g, int(fs) // g
        self.sos = butter(2, FILTER_HZ, fs=fs, output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2, channels))
        # resample_poly's filter and alignment (scipy.signal._signaltools.resample_poly, padtype
        # "constant"): output j is upfirdn's m = j + pre, sum_n x[n] h[m*down - n*up]
        up, down = self.up, self.down
        half = 10 * max(up, down)
        pad = down - half % down
        self.h = np.concatenate([np.zeros(pad), firwin(2 * half + 1, 1.0 / max(up, down), window=("kaiser", 5.0)) * up])
        self.pre = (half + pad) // down
        self.block, self.ch = block, channels
        self.waiting: list[np.ndarray] = []
        self.n_wait = 0
        self.buf, self.b0 = np.zeros((0, channels)), 0     # filtered input from b0 (a multiple of down)
        self.n_in = 0                                       # filtered (held) samples so far
        self.j = 0                                          # next output

    def feed(self, doc: np.ndarray) -> np.ndarray:
        self.waiting.append(doc)
        self.n_wait += doc.shape[0]
        return self._run(False) if self.n_wait >= self.block else np.zeros((0, self.ch))

    def finish(self) -> np.ndarray:
        return self._run(True)

    def _run(self, last: bool) -> np.ndarray:
        from scipy.signal import sosfilt, upfirdn
        up, down, pre, taps = self.up, self.down, self.pre, self.h.size
        if self.waiting:
            doc = np.concatenate(self.waiting)
            self.waiting, self.n_wait = [], 0
            y, self.zi = sosfilt(self.sos, np.repeat(doc, HOLD, axis=0), axis=0, zi=self.zi)
            self.buf = np.concatenate([self.buf, y])
            self.n_in += y.shape[0]
        end = self.b0 + self.buf.shape[0]
        # complete outputs: every input they sum is here, (m*down)/up < end; at the end, all of them
        j_hi = -(-self.n_in * up // down) if last else max(self.j, ((end - 1) * up) // down - pre + 1)
        out = np.zeros((0, self.ch))
        if j_hi > self.j:
            x = self.buf if not last else np.concatenate([self.buf, np.zeros((taps // up + 2, self.ch))])
            m0 = self.b0 * up // down
            out = upfirdn(self.h, x, up, down, axis=0)[self.j + pre - m0:j_hi + pre - m0]
            self.j = j_hi
        # keep what the next output sums: n >= ((j + pre)*down - taps + 1)/up, from a multiple of down
        need = max(0, -(-((self.j + pre) * down - taps + 1) // up))
        cut = max(self.b0, need // down * down)
        self.buf, self.b0 = self.buf[cut - self.b0:], cut
        return out


def to_output(doc: np.ndarray, rate: int = 48000, block: int = 1 << 15) -> np.ndarray:
    """DOC-rate sides -> `rate` (Output, of a whole song)."""
    o = Output(rate, doc.shape[1], block)
    parts = [o.feed(doc[at:at + block]) for at in range(0, doc.shape[0], block)] + [o.finish()]
    return np.concatenate(parts)


def _pcm(y: np.ndarray) -> np.ndarray:
    return np.clip(np.round(y * 32767), -32768, 32767).astype("<i2")


def render_wav(mod: Mod, tables, gains: np.ndarray, rate: int = 48000, gain: float = 0.25,
               scale: float | None = None) -> tuple[np.ndarray, float]:
    """(16-bit frames, peak) of the song at `rate`, block by block (the whole song never held
    as floats): to_output(render_doc(...)) * gain, divided by `scale` if given. A peak over 1
    means it clipped: render again with scale = peak (rare: it costs a second render)."""
    o = Output(rate)
    pcm, peak = [], 0.0

    def take(y: np.ndarray) -> None:
        nonlocal peak
        if y.size:
            y *= gain
            if scale:
                y /= scale
            peak = max(peak, float(np.max(np.abs(y))))
            pcm.append(_pcm(y))

    for b in doc_blocks(mod, tables, gains):
        take(o.feed(b))
    take(o.finish())
    return (np.concatenate(pcm) if pcm else np.zeros((0, 2), "<i2")), peak


def write_wav(path: Path, y: np.ndarray, rate: int) -> None:
    """Floats in -1..1, or 16-bit frames as they are."""
    pcm = y if y.dtype == np.dtype("<i2") else _pcm(y)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(y.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


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
        mod = read_mod(f.read_bytes())
        pcm, peak = render_wav(mod, tables, gains, a.rate, a.gain)
        if peak > 1.0:
            print(f"{f.name}: peak {peak:.2f} at gain {a.gain}: scaled down to fit", file=sys.stderr)
            pcm, _ = render_wav(mod, tables, gains, a.rate, a.gain, scale=peak)
        write_wav(a.out / f"{f.stem}.wav", pcm, a.rate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
