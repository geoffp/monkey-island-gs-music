# SPDX-License-Identifier: GPL-3.0-or-later
"""MI1 MT-32 MIDI -> 15-channel ProTracker MOD, built to NinjaTrackerPlus' constraints.

The MOD is the primary artefact: it renders on the desktop (ffmpeg/libopenmpt) and converts
to .NTP for the IIGS. Samples come from Munt renders of the sounds in gs/mt32bank.toml and
are packed as NTP will pack them into the 64 KB DOC RAM. See docs/ninjatrackerplus.md.

Melodic sample = raw MT-32 attack (`attack_bytes`, loop header) + a 512-byte loop holding
k identical sustain cycles (one averaged cycle, tiled: the loop is exactly periodic), at a rate
that makes 512 bytes = k periods; note base + finetune make it play in tune. The attack's end
is crossfaded into the loop's periodic continuation, so neither seam clicks or buzzes.
Loudness after the attack follows the MT-32 envelope via per-row Cxx.
Levels are measured in Munt, not guessed: each sample's peak, its velocity response, and the
MT-32's channel-volume (CC7) curve; each song is normalised so its loudest note is volume 64.
Velocity: Cxx in the note's cell; if the note needs an EDx micro-delay, Cxx goes in the
preceding cell when that's silent-safe (one-shot track), else the note uses the instrument's
default volume and the next row corrects it.
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import mido
import numpy as np
from scipy.signal import resample

from . import mt32bank as mb

PAL = 7093789.2 / 2                       # Amiga PAL clock / 2: rate = PAL / period (NTP)
ROWS = 64
STOPPER = 8
NOTES = 72                                # NTP/ProTracker 6-octave table
MI1_TICK = 0.5 / 120                      # seconds per MI1 tick (120 PPQN, 120 BPM)
RHYTHM = 9


# ---------------------------------------------------------------------------
# Period tables (ProTracker, extended to 6 octaves, all finetunes) from NTP's make_tables.php

def period_tables(make_tables: Path) -> dict[int, list[int]]:
    text = make_tables.read_text().replace("\r", "")
    body = text[text.index("$pt_durations_finetune"):text.index("];\n\n/* find the highest")]
    tables = {}
    for m in re.finditer(r"//\s*tuning\s*(-?\d+)(.*?)\]", body, re.S):
        nums = [int(x) for x in re.findall(r"\b\d+\b", re.sub(r"/\*.*?\*/", "", m.group(2)))]
        tables[int(m.group(1))] = nums[:NOTES]
    if 0 not in tables or any(len(v) < NOTES for v in tables.values()):
        raise ValueError(f"{make_tables}: could not read the 16 finetune period tables")
    return tables


# ---------------------------------------------------------------------------
# Samples

@dataclass
class ModSample:
    name: str
    data: np.ndarray            # float -1..1
    loop_start: int = 0         # bytes
    loop_len: int = 0           # 0 = one-shot
    finetune: int = 0
    base_note: int = 0          # NTP note index (0-71) that plays MT-32 key `key0`
    key0: int = 60
    kind: str = "melodic"
    level: float = 1.0          # peak amplitude of the source (Munt, velocity 100, CC7 100) / data peak
    vel_v: np.ndarray = field(default_factory=lambda: np.array([0.0, 127.0]))  # velocity response:
    vel_g: np.ndarray = field(default_factory=lambda: np.array([0.0, 1.27]))   # gain vs velocity 100
    key_v: np.ndarray = field(default_factory=lambda: np.array([0.0]))         # key response:
    key_g: np.ndarray = field(default_factory=lambda: np.array([1.0]))         # gain vs key0
    env_t: np.ndarray = field(default_factory=lambda: np.zeros(1))   # seconds after note-on
    env_g: np.ndarray = field(default_factory=lambda: np.ones(1))    # loudness vs note-on (may exceed 1: swells)
    rel_g: np.ndarray = field(default_factory=lambda: np.zeros(1))   # release, per 10 ms
    seconds: float = 0.0        # one-shot length (drums)
    default_volume: int = 48
    tune_cents: float = 0.0

    def mod_bytes(self) -> bytes:
        return bytes(np.clip(np.round(self.data * 127), -128, 127).astype(np.int8).tobytes())

    def ntp_blocks(self) -> list[int]:
        """DOC bytes this sample takes once NTP prepares it (power-of-two blocks)."""
        def block(n):
            return 1 << max(8, math.ceil(math.log2(n)))
        def with_stoppers(n):
            return n if n in (256, 512, 1024, 2048, 4096, 8192, 16384, 32768) else n + STOPPER
        if self.loop_len:
            return [block(with_stoppers(self.loop_start)), block(with_stoppers(self.loop_len))]
        return [block(with_stoppers(len(self.data)))]


def _best_tuning(freq: float, tables, min_rate, max_rate, loop_bytes, key0, key_lo, key_hi):
    """(err_cents, k, rate, base_note, finetune): 512 B = k periods at `rate`, played by
    note base_note with finetune so that period gives exactly that rate."""
    best = None
    for k in range(1, 200):
        rate = loop_bytes * freq / k
        if rate > max_rate:
            continue
        if rate < min_rate:
            break
        for ft, per in tables.items():
            for n in range(NOTES):
                lo, hi = n + key_lo - key0, n + key_hi - key0
                if lo < 0 or hi >= NOTES:
                    continue
                err = 1200 * math.log2((PAL / per[n]) / rate)
                if best is None or abs(err) < abs(best[0]):
                    best = (err, k, rate, n, ft)
    if best is None:
        raise ValueError(f"no tuning for {freq:.1f} Hz over keys {key_lo}-{key_hi}")
    return best


def periodic_loop(x: np.ndarray, start: float, period: float, k: int, out: int, cycles: int = 4) -> np.ndarray:
    """`out` points holding k identical cycles: the average of `cycles` source periods from
    `start`, tiled k times and band-limited. Exactly periodic, so the loop seam is continuous;
    the source's own cycle-to-cycle drift (chorus, vibrato) would otherwise jump at the seam
    and buzz at the loop rate."""
    fine = 1024
    acc = np.zeros(fine)
    for c in range(cycles):
        acc += np.interp(start + period * (c + np.arange(fine) / fine), np.arange(len(x)), x)
    cyc = acc / cycles
    loop = resample(np.tile(cyc, k), out)
    # averaging detuned partials loses level: match the source's RMS over the same span
    src = np.interp(start + np.arange(fine * cycles) * (period / fine), np.arange(len(x)), x)
    e_src, e_loop = float(np.sqrt(np.mean(src ** 2))), float(np.sqrt(np.mean(loop ** 2)))
    return loop * (e_src / e_loop) if e_loop > 0 else loop


def attack_and_loop(x: np.ndarray, a: int, period: float, rate: float, k: int, attack_bytes: int,
                    loop_bytes: int, xfade_cycles: float = 2.0) -> tuple[np.ndarray, np.ndarray, float]:
    """(attack, loop, source index where the attack ends). The attack is resampled with a
    margin (no FFT edge ringing at the seam), and its last `xfade_cycles` periods crossfade
    into the loop's periodic continuation, so attack -> loop is continuous in value and phase."""
    att_end = a + attack_bytes / rate * mb.SR
    loop = periodic_loop(x, att_end, period, k, loop_bytes)
    margin = int(4 * period)
    hi = min(len(x), int(round(att_end)) + margin)
    seg = x[a:hi]
    attack = resample(seg, int(round(len(seg) * rate / mb.SR)))[:attack_bytes]
    if len(attack) < attack_bytes:
        attack = np.pad(attack, (0, attack_bytes - len(attack)))
    n = min(attack_bytes, max(4, int(round(xfade_cycles * loop_bytes / k))))
    ext = loop[np.arange(-n, 0) % loop_bytes]           # what the loop would have been before its start
    w = np.linspace(0, 1, n + 1)[1:]
    attack[-n:] = attack[-n:] * (1 - w) + ext * w
    return attack, loop, att_end


def drum_rate(seg: np.ndarray, nbytes: int, lo: float, hi: float) -> float:
    """Sample rate for a one-shot of `nbytes`: fast enough for the sound's bandwidth (2.2 x the
    frequency below which 95% of its energy lies), then lowered, by at most half, if the bytes
    can't hold 95% of its energy at that rate. Filling the bytes with the whole -46 dB tail
    instead put hats and shakers (energy ~14 kHz) at 6 kHz, which made them all but silent."""
    SR = mb.SR
    P = np.abs(np.fft.rfft(seg)) ** 2
    c = np.cumsum(P) / (P.sum() or 1.0)
    f95 = float(np.fft.rfftfreq(len(seg), 1 / SR)[min(np.searchsorted(c, 0.95), len(c) - 1)])
    bw = min(hi, max(lo, 2.2 * f95))
    e = np.cumsum(seg ** 2) / (float(np.sum(seg ** 2)) or 1.0)
    d95 = max(int(np.searchsorted(e, 0.95)), 1) / SR
    fits = nbytes / d95                                  # rate at which the bytes hold 95%
    return float(min(hi, max(lo, bw if fits >= bw else max(fits, bw / 2))))


VEL_POINTS = (20, 40, 64, 80, 100, 127)


def _peak(x: np.ndarray) -> float:
    return float(np.max(mb.rms_envelope(x, 0.005)))


def velocity_response(render_at) -> tuple[np.ndarray, np.ndarray]:
    """Peak level vs velocity, relative to velocity 100, measured from renders (the MT-32's
    velocity sensitivity differs per timbre: closed hat x2.5 at 127, bass x1.4)."""
    ref = _peak(render_at(100)) or 1.0
    return (np.array([0.0, *VEL_POINTS], float),
            np.array([0.0, *(_peak(render_at(v)) / ref for v in VEL_POINTS)]))


def key_response(render_at, key0: int, lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """Peak level vs key, relative to the sampled key `key0`: MT-32 timbres change level with
    key (TVA key follow, partial balance), so one sample's level doesn't fit its whole range."""
    keys = sorted({lo, hi, key0, *range(lo, hi, 12)})
    ref = _peak(render_at(key0)) or 1.0
    return np.array(keys, float), np.array([_peak(render_at(k)) / ref for k in keys])


CC7_POINTS = (0, 10, 20, 32, 40, 52, 64, 77, 90, 100, 110, 120, 127)


def cc7_curve(smf2wav, roms, cache, program: int = 50, key: int = 67) -> tuple[np.ndarray, np.ndarray]:
    """The MT-32 part-volume (CC7) curve, gain relative to CC7 100, measured in Munt. It is
    steep (about 0.35 at 64, 0.11 at 40) and the same on melodic and rhythm parts."""
    lv = [_peak(mb.render(smf2wav, roms, cache, program=program, key=key, hold=0.5, volume=v)) for v in CC7_POINTS]
    ref = lv[CC7_POINTS.index(100)] or 1.0
    return np.array(CC7_POINTS, float), np.array(lv) / ref


def build_samples(spec: dict, cfg: dict, key_ranges: dict[int, tuple[int, int]], smf2wav, roms, cache,
                  tables, log=print) -> tuple[list[ModSample], dict, dict]:
    """Samples per gs/mt32bank.toml; returns (samples, program->index, rhythm key->(index, note, level gain))."""
    SR = mb.SR
    hold = spec.get("hold_seconds", 2.0)
    samples, programs, drums = [], {}, {}
    for m in spec["melodic"]:
        x = mb.render(smf2wav, roms, cache, program=m["program"], key=m["key"], hold=hold)
        a = mb.onset(x)
        off = a + int(hold * SR)
        env = mb.rms_envelope(x)
        f0 = 440 * 2 ** ((m["key"] - 69) / 12)
        probe = a + int(0.25 * SR)
        period = mb.refine_period(x, probe, f0)
        fm = SR / period
        lo, hi = key_ranges.get(m["slot"], (m["key"], m["key"]))
        err, k, rate, n0, ft = _best_tuning(fm, tables, cfg["min_rate"], cfg["max_rate"],
                                            cfg["loop_bytes"], m["key"], min(lo, m["key"]), max(hi, m["key"]))
        # attack = source [a, a + attack_bytes/rate); loop = the next k periods
        attack, loop, att_end = attack_and_loop(x, a, period, rate, k, cfg["attack_bytes"], cfg["loop_bytes"])
        e_loop = float(np.sqrt(np.mean(loop ** 2))) or 1e-9
        # envelope after the attack, relative to the loop's own level
        t = np.arange(int(att_end), off, int(0.01 * SR))
        g = env[t] / e_loop
        g_max = float(g.max()) if len(g) else 1.0
        rel = env[off: off + int(cfg["release_max"] * SR): int(0.01 * SR)]
        rel_g = rel / max(env[off], 1e-9)
        data = np.concatenate([attack, loop])
        peak = float(np.max(np.abs(data))) or 1.0
        s = ModSample(m["name"], data / peak, loop_start=cfg["attack_bytes"], loop_len=cfg["loop_bytes"],
                      finetune=ft, base_note=n0, key0=m["key"], kind="melodic",
                      env_t=(t - a) / SR, env_g=g, rel_g=np.clip(rel_g, 0, 1),
                      tune_cents=err, level=peak)
        s.vel_v, s.vel_g = velocity_response(lambda v: mb.render(smf2wav, roms, cache, program=m["program"],
                                                                 key=m["key"], hold=0.5, velocity=v))
        s.key_v, s.key_g = key_response(lambda kk: mb.render(smf2wav, roms, cache, program=m["program"],
                                                             key=kk, hold=0.5), m["key"], min(lo, m["key"]),
                                        max(hi, m["key"]))
        idx = len(samples)
        samples.append(s)
        for p in m["covers"]:
            programs[p] = idx
        log(f"  {m['name']:<12} k={k:2d} rate {rate:7.0f} Hz note {n0:2d} ft {ft:+d} "
            f"err {err:+5.1f} cents, swell x{g_max:.2f}, keys {lo}-{hi}")

    for d in spec["drum"]:
        x = mb.render(smf2wav, roms, cache, program=None, key=d["key"], hold=0.5)
        a = mb.onset(x)
        env = mb.rms_envelope(x, 0.005)
        tail = np.nonzero(env[a:] > 0.005 * np.max(env))[0]
        dur = (tail[-1] + 1) / SR if len(tail) else 0.1
        want = drum_rate(x[a: a + int(dur * SR)], d["bytes"] - STOPPER,
                         cfg.get("drum_min_rate", 6000), cfg.get("drum_max_rate", 26000))
        # pick the note whose ft-0 period gives a rate close to `want`, leaving room for offsets
        offs = {c: 0 if c == d["key"] else mb.pitch_offset(x, mb.render(smf2wav, roms, cache,
                                                                         program=None, key=c, hold=0.5))
                for c in d["covers"]}
        lo_off, hi_off = min(offs.values()), max(offs.values())
        n0 = min((n for n in range(-lo_off, NOTES - hi_off)), key=lambda n: abs(PAL / tables[0][n] - want))
        rate = PAL / tables[0][n0]
        seg = x[a: a + int(min(dur, (d["bytes"] - STOPPER) / rate) * SR)]
        fade = np.ones(len(seg))
        kk = max(1, len(seg) // 10)
        fade[-kk:] = np.linspace(1, 0, kk)
        data = resample(seg * fade, max(2, int(len(seg) / SR * rate) // 2 * 2))
        peak = float(np.max(np.abs(data))) or 1.0
        s = ModSample(d["name"], data / peak, kind="drum", base_note=n0, key0=d["key"],
                      seconds=len(data) / rate, level=peak)
        s.vel_v, s.vel_g = velocity_response(lambda v: mb.render(smf2wav, roms, cache, program=None,
                                                                 key=d["key"], hold=0.5, velocity=v))
        idx = len(samples)
        samples.append(s)
        ref = _peak(x) or 1.0                            # each rhythm key has its own MT-32 level
        for c, o in offs.items():
            gain = 1.0 if c == d["key"] else _peak(mb.render(smf2wav, roms, cache, program=None, key=c, hold=0.5)) / ref
            drums[c] = (idx, n0 + o, gain)
        log(f"  {d['name']:<12} {len(data)} B @ {rate:6.0f} Hz ({len(data) / rate * 1000:4.0f} ms) note {n0:2d}, "
            f"covers {offs}")

    if len(samples) > 31:
        raise ValueError(f"{len(samples)} samples > 31 (MOD limit)")
    return samples, programs, drums


def ntp_doc_pages(samples: list[ModSample]) -> int:
    """DOC pages NTP needs for these samples (aligned blocks, packed largest first)."""
    blocks = sorted((b for s in samples for b in s.ntp_blocks()), reverse=True)
    waves = [mb.Wave(f"b{i}", bytes(b)) for i, b in enumerate(blocks)]
    pages = mb.allocate(waves, 0, 255)                   # raises if it can't fit
    return sum(b // 256 for b in blocks)


# ---------------------------------------------------------------------------
# Song conversion

@dataclass
class Note:
    t: float            # seconds
    off: float
    key: int
    vel: int
    channel: int
    volume: int = 100   # channel CC7 at note-on (MT-32 power-on part volume is 100)


def read_notes(path: Path) -> tuple[list[Note], dict[int, int], float]:
    mf = mido.MidiFile(path)
    tempo = 500_000
    events = []
    for trk in mf.tracks:
        t = 0
        for msg in trk:
            t += msg.time
            if msg.type == "set_tempo":
                tempo = msg.tempo
            events.append((t, msg))
    events.sort(key=lambda e: e[0])
    sec = lambda tick: tick * tempo / 1e6 / mf.ticks_per_beat
    programs, notes, open_, volume = {}, [], {}, {}
    end = 0.0
    for tick, m in events:
        if m.type == "program_change":
            programs.setdefault(m.channel, m.program)
        elif m.type == "control_change" and m.control == 7:
            volume[m.channel] = m.value
        elif m.type == "note_on" and m.velocity > 0:
            open_.setdefault((m.channel, m.note), []).append(
                Note(sec(tick), sec(tick), m.note, m.velocity, m.channel, volume.get(m.channel, 100)))
        elif m.type in ("note_on", "note_off") and open_.get((m.channel, m.note)):
            n = open_[(m.channel, m.note)].pop(0)
            n.off = sec(tick)
            notes.append(n)
        elif m.type == "end_of_track":
            end = max(end, sec(tick))
    for lst in open_.values():
        for n in lst:
            n.off = end
            notes.append(n)
    notes.sort(key=lambda n: (n.t, n.channel, n.key))
    return notes, programs, max(end, max((n.off for n in notes), default=0))


def detect_grid(onsets: list[float], unit_range=(14, 80)) -> tuple[float, float]:
    """(unit, phase) in MI1 ticks maximising circular concentration of the onsets."""
    on = np.array(sorted(set(onsets))) / MI1_TICK
    best = (0.0, 30.0, 0.0)
    for P in np.arange(unit_range[0], unit_range[1], 0.05):
        z = np.mean(np.exp(2j * np.pi * on / P))
        if abs(z) > best[0]:
            best = (abs(z), P, (np.angle(z) / (2 * np.pi) * P) % P)
    return best[1], best[2]


@dataclass
class Report:
    notes: int = 0
    dropped: int = 0
    delayed: int = 0
    vel_in_cell: int = 0
    vel_preceding: int = 0
    vel_default: int = 0
    level_ref: float = 1.0      # note loudness that maps to MOD volume 64
    nudged: int = 0
    tracks_used: int = 0
    oscillators: int = 0
    stolen: int = 0
    rows: int = 0
    patterns: int = 0
    tempo: int = 0
    speed: int = 0
    row_ms: float = 0.0
    timing_err_ms: float = 0.0


class Grid:
    """cells[track][row] = [note(0-72), instrument(0-31), effect, param]; effect -1 = empty."""

    def __init__(self, tracks: int):
        self.cells: list[dict[int, list[int]]] = [dict() for _ in range(tracks)]

    def get(self, tr, row):
        return self.cells[tr].setdefault(row, [0, 0, -1, 0])

    def free(self, tr, row) -> bool:
        c = self.cells[tr].get(row)
        return c is None or (c[0] == 0 and c[2] < 0)

    def effect_free(self, tr, row) -> bool:
        c = self.cells[tr].get(row)
        return c is None or c[2] < 0


def convert_song(path: Path, samples, programs, drums, cfg: dict, song_cfg: dict,
                 cc7: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[Grid, int, Report]:
    """`cc7`: (CC7 values, gains vs 100) from cc7_curve(); None ignores channel volume."""
    notes, chan_prog, end = read_notes(path)
    rep = Report()
    unit, phase = song_cfg.get("unit_ticks"), song_cfg.get("phase_ticks")
    if unit is None:
        unit, phase = detect_grid([n.t for n in notes])
    phase = phase or 0.0
    rpu, speed = song_cfg.get("rows_per_unit", cfg["rows_per_unit"]), song_cfg.get("speed", cfg["speed"])
    q = song_cfg.get("quantize", cfg["quantize"])
    row_s = unit * MI1_TICK / rpu
    tempo = round(2.5 * speed / row_s)
    while tempo > 255:
        speed -= 1
        tempo = round(2.5 * speed / row_s)
    while tempo < 32:
        speed += 1
        tempo = round(2.5 * speed / row_s)
    tick_s = 2.5 / tempo
    row_s = tick_s * speed
    shift = (row_s - (phase * MI1_TICK) % row_s) % row_s + row_s      # grid aligned, >= 1 row lead-in
    rep.tempo, rep.speed, rep.row_ms = tempo, speed, row_s * 1000

    def place(t: float) -> tuple[int, int]:
        n = (t + shift) / tick_s
        row_start = round(n / speed) * speed
        n = round(n + q * (row_start - n))
        r, d = divmod(n, speed)
        return int(r), int(d)

    # Tracks are created on demand within NTP's oscillator budget: one-shot (drum) tracks
    # need 1 oscillator, looped melodic tracks 2 (loop header + loop); 31 are usable.
    budget = song_cfg.get("oscillators", cfg.get("oscillators", 31))
    grid = Grid(0)
    trk_inst: list[int] = []        # current sample (1-based) per track
    trk_busy: list[int] = []        # last row with cells for the current note
    trk_off: list[int] = []         # note-off row of the current melodic note (-1: drum)
    trk_oneshot_end: list[float] = []
    trk_kind: list[str] = []
    osc_used = 0
    err_sum = 0.0

    def new_track(kind: str) -> int:
        nonlocal osc_used
        grid.cells.append({})
        for lst, val in ((trk_inst, 0), (trk_busy, -1), (trk_off, -1), (trk_oneshot_end, -1.0), (trk_kind, kind)):
            lst.append(val)
        osc_used += 1 if kind == "drum" else 2
        return len(grid.cells) - 1

    # Loudness of a note as the MT-32 plays it: sample peak x velocity response x CC7 curve.
    # The song is normalised so its loudest moment (99.5th percentile over notes, including
    # sounds that swell after their attack) is MOD volume 64.
    def sample_of(n: Note) -> int | None:
        if n.channel == RHYTHM:
            return drums[n.key][0] if n.key in drums else None
        return programs.get(chan_prog.get(n.channel, -1))

    def amp(s: ModSample, n: Note) -> float:
        g7 = float(np.interp(n.volume, *cc7)) if cc7 is not None else 1.0
        gk = drums[n.key][2] if n.channel == RHYTHM else float(np.interp(n.key, s.key_v, s.key_g))
        return s.level * float(np.interp(n.vel, s.vel_v, s.vel_g)) * g7 * gk

    swell = lambda s: max(1.0, float(np.max(s.env_g))) if s.kind == "melodic" else 1.0
    amps = [amp(samples[si], n) * swell(samples[si]) for n in notes if (si := sample_of(n)) is not None]
    ref = song_cfg.get("level_ref") or (float(np.percentile(amps, 99.5)) if amps else 1.0)
    rep.level_ref = ref

    def vol_for(s: ModSample, n: Note) -> int:
        return max(1, min(64, round(64 * amp(s, n) / ref)))

    # default volume (used for one row by delayed notes): the sample's median in this song
    by_sample: dict[int, list[int]] = {}
    for n in notes:
        if (si := sample_of(n)) is not None:
            by_sample.setdefault(si, []).append(vol_for(samples[si], n))
    for si, s in enumerate(samples):
        s.default_volume = int(np.median(by_sample[si])) if si in by_sample else 32

    for n in notes:
        rep.notes += 1
        if n.channel == RHYTHM:
            hit = drums.get(n.key)
            if hit is None:
                rep.dropped += 1
                continue
            si, note_idx = hit[:2]
        else:
            si = programs.get(chan_prog.get(n.channel, -1))
            if si is None:
                rep.dropped += 1
                continue
            s0 = samples[si]
            note_idx = s0.base_note + (n.key - s0.key0)
            if not 0 <= note_idx < NOTES:
                note_idx += 12 * ((0 - note_idx) // 12 + 1) if note_idx < 0 else -12 * ((note_idx - NOTES) // 12 + 1)
        s = samples[si]
        r, d = place(n.t)
        err_sum += abs((r * speed + d) * tick_s - (n.t + shift))
        kind = "drum" if s.kind == "drum" else "melodic"
        # choose a track: free & same sample, else free of the same kind, else a new track,
        # else steal a same-kind track that is only releasing (cut its release)
        tracks = range(len(grid.cells))
        cands = [tr for tr in tracks if trk_kind[tr] == kind and trk_busy[tr] < r and grid.free(tr, r)]
        if cands:
            tr = min(cands, key=lambda t: (trk_inst[t] != si + 1, t))
        elif osc_used + (1 if kind == "drum" else 2) <= budget:
            tr = new_track(kind)
        else:
            releasing = [t for t in tracks if trk_kind[t] == kind and 0 <= trk_off[t] <= r]
            if not releasing:
                rep.dropped += 1
                continue
            tr = min(releasing, key=lambda t: trk_off[t])
            for row in [x for x in grid.cells[tr] if x >= r]:
                del grid.cells[tr][row]
            rep.stolen += 1
        trk_kind[tr] = kind
        same = trk_inst[tr] == si + 1
        vf = 64 * amp(s, n) / ref                    # unrounded, for the envelope rows
        v = vol_for(s, n)
        silent_before = False
        cell = grid.get(tr, r)
        cell[0] = note_idx + 1
        if d == 0:
            cell[1], cell[2], cell[3] = si + 1, 0xC, v
            rep.vel_in_cell += 1
        else:
            rep.delayed += 1
            silent_before = (kind == "drum" and same and trk_oneshot_end[tr] <= (r - 1) * row_s
                             and r - 1 > trk_busy[tr] and grid.effect_free(tr, r - 1))
            if silent_before:
                pc = grid.get(tr, r - 1)
                pc[2], pc[3] = 0xC, v
                cell[1] = 0                          # keep the track's volume
                rep.vel_preceding += 1
            else:
                cell[1] = si + 1                     # default volume; corrected next row
                rep.vel_default += 1
            cell[2], cell[3] = 0xE, 0xD0 | d
        trk_inst[tr] = si + 1
        last = r
        trk_off[tr] = -1
        if kind == "drum":
            trk_oneshot_end[tr] = (r * speed + d) * tick_s + s.seconds
            if d and not silent_before and grid.effect_free(tr, r + 1) and v != s.default_volume:
                c = grid.get(tr, r + 1)
                c[2], c[3] = 0xC, v
                last = r + 1
        else:
            # envelope rows until note-off, then release
            r_off, _ = place(n.off)
            r_off = max(r_off, r + 1)
            trk_off[tr] = r_off
            prev = None
            for rr in range(r + 1, r_off):
                dt = (rr - r) * row_s
                g = float(np.interp(dt, s.env_t, s.env_g, right=s.env_g[-1]))
                vv = max(0, min(64, round(vf * g)))
                if vv != prev and grid.effect_free(tr, rr):
                    c = grid.get(tr, rr)
                    c[2], c[3] = 0xC, vv
                    prev = vv
                last = rr
            g_off = float(np.interp((r_off - r) * row_s, s.env_t, s.env_g, right=s.env_g[-1]))
            v_off = vf * g_off
            steps = max(1, int(round(len(s.rel_g) * 0.01 / row_s)))
            for i in range(steps + 1):
                rr = r_off + i
                gg = float(np.interp(i * row_s, np.arange(len(s.rel_g)) * 0.01, s.rel_g, right=0.0)) if i < steps else 0.0
                vv = max(0, min(64, round(v_off * gg)))
                if grid.effect_free(tr, rr) and grid.cells[tr].get(rr, [0])[0] == 0:
                    c = grid.get(tr, rr)
                    c[2], c[3] = 0xC, vv
                last = rr
                if vv == 0:
                    break
        trk_busy[tr] = last

    rep.timing_err_ms = 1000 * err_sum / max(1, rep.notes - rep.dropped)
    rows_needed = max([max(c, default=0) for c in grid.cells]) + 1
    rows_needed = max(rows_needed, int((end + shift) / row_s) + 1)
    patterns = math.ceil(rows_needed / ROWS)
    if patterns > 128:
        raise ValueError(f"{patterns} patterns > 128 (MOD order list); raise row length")
    rep.rows, rep.patterns = rows_needed, patterns
    rep.tracks_used, rep.oscillators = len(grid.cells), osc_used
    if len(grid.cells) < 2:
        new_track("drum")
    # speed + tempo on row 0 (the lead-in row is always free), loop jump at the very end
    for tr, (eff, par) in enumerate(((0xF, speed), (0xF, tempo))):
        c = grid.get(tr, 0)
        if c[0] or c[2] >= 0:
            raise AssertionError("row 0 must be free (lead-in)")
        c[2], c[3] = eff, par
    last_row = patterns * ROWS - 1
    tr = next((t for t in range(len(grid.cells)) if grid.effect_free(t, last_row)), None)
    if tr is not None:
        c = grid.get(tr, last_row)
        c[2], c[3] = 0xB, 0                     # loop to the start (like the game's FF)
    return grid, patterns, rep


def mod_file(title: str, samples: list[ModSample], grid: Grid, patterns: int, tables) -> bytes:
    tracks = len(grid.cells)
    out = bytearray(title.encode("latin-1")[:20].ljust(20, b"\0"))
    for i in range(31):
        if i < len(samples):
            s = samples[i]
            raw = s.mod_bytes()
            if len(raw) % 2:
                raw += b"\0"
            ls, ll = (s.loop_start // 2, s.loop_len // 2) if s.loop_len else (0, 1)
            out += s.name.encode("latin-1")[:22].ljust(22, b"\0")
            out += struct.pack(">HBBHH", len(raw) // 2, s.finetune & 0x0F, s.default_volume, ls, ll)
        else:
            out += bytes(22) + struct.pack(">HBBHH", 0, 0, 0, 0, 1)
    out += bytes([patterns, 127]) + bytes(range(patterns)) + bytes(128 - patterns)
    # channel tag: "nCHN" below 10 channels, "nnCH" from 10 (what trackers and NTP's converter expect)
    out += (f"{tracks}CHN" if tracks < 10 else f"{tracks:02d}CH").encode()
    per0 = tables[0]
    for p in range(patterns):
        for row in range(p * ROWS, (p + 1) * ROWS):
            for tr in range(tracks):
                note, inst, eff, par = grid.cells[tr].get(row, [0, 0, -1, 0])
                period = per0[note - 1] if note else 0
                eff, par = (0, 0) if eff < 0 else (eff, par)
                out += struct.pack(">HH", (inst & 0xF0) << 8 | period, (inst & 0x0F) << 12 | eff << 8 | par)
    for s in samples:
        raw = s.mod_bytes()
        out += raw + (b"\0" if len(raw) % 2 else b"")
    return bytes(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="MT-32 MIDI files (music/midi/mt32)")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--config", type=Path, default=Path("gs/ntp.toml"))
    ap.add_argument("--spec", type=Path, default=Path("gs/mt32bank.toml"))
    ap.add_argument("--make-tables", type=Path, default=Path("vendor/ntp/src/make_tables.php"))
    ap.add_argument("--smf2wav", type=Path, default=Path("vendor/munt/bin/mt32emu-smf2wav"))
    ap.add_argument("--roms", type=Path, default=Path("original files/mt-32 roms"))
    ap.add_argument("--cache", type=Path, default=Path("gs/build/mt32samples"))
    args = ap.parse_args(argv)
    conf = tomllib.loads(args.config.read_text())
    cfg = conf["defaults"]
    spec = tomllib.loads(args.spec.read_text())
    tables = period_tables(args.make_tables)
    cc7 = cc7_curve(args.smf2wav, args.roms, args.cache)
    print("MT-32 CC7 curve: " + " ".join(f"{int(v)}:{g:.2f}" for v, g in zip(*cc7)))

    # key ranges per melodic slot across all songs (so one sample mapping fits every song)
    prog_slot = {p: m["slot"] for m in spec["melodic"] for p in m["covers"]}
    ranges: dict[int, tuple[int, int]] = {}
    for p in args.inputs:
        notes, chan_prog, _ = read_notes(p)
        for n in notes:
            slot = prog_slot.get(chan_prog.get(n.channel, -1))
            if n.channel != RHYTHM and slot is not None:
                lo, hi = ranges.get(slot, (n.key, n.key))
                ranges[slot] = (min(lo, n.key), max(hi, n.key))

    print("samples (shared by every song):")
    samples, programs, drums = build_samples(spec, cfg, ranges, args.smf2wav, args.roms, args.cache, tables)
    pages = ntp_doc_pages(samples)
    print(f"  NTP DOC RAM: {pages} of 256 pages ({pages * 256} bytes), {len(samples)} samples")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{'song':<6} {'tempo':>5} {'spd':>3} {'row ms':>6} {'pat':>3} {'trk':>3} {'osc':>3} {'notes':>5} "
          f"{'drop':>4} {'steal':>5} {'delay':>5} {'v:cell':>6} {'v:prev':>6} {'v:dflt':>6} {'err ms':>6}")
    for p in args.inputs:
        sid = p.stem.split("-")[0]
        song_cfg = {**cfg, **conf.get("songs", {}).get(sid, {})}
        grid, patterns, rep = convert_song(p, samples, programs, drums, cfg, song_cfg, cc7)
        (args.out_dir / f"MI{sid}.mod").write_bytes(mod_file(f"MI1 {sid}", samples, grid, patterns, tables))
        print(f"{sid:<6} {rep.tempo:5d} {rep.speed:3d} {rep.row_ms:6.1f} {rep.patterns:3d} {rep.tracks_used:3d} "
              f"{rep.oscillators:3d} {rep.notes:5d} {rep.dropped:4d} {rep.stolen:5d} {rep.delayed:5d} "
              f"{rep.vel_in_cell:6d} {rep.vel_preceding:6d} "
              f"{rep.vel_default:6d} {rep.timing_err_ms:6.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
