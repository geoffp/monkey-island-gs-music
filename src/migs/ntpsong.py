# SPDX-License-Identifier: GPL-3.0-or-later
"""Arrange an MI1 MT-32 MIDI song onto NinjaTrackerPlus tracks: a MOD pattern grid.

- Timing: rows sit on the song's detected grid unit / `rows_per_unit`; `EDx` keeps
  micro-timing, and `Fxx` alternates the two integer tempos around the ideal one so rows stay
  on the music (tempo_commands).
- Tracks: a looped melodic note needs 2 oscillators (loop header + loop), a one-shot 1; NTP
  has 31. Tracks are created on demand within that budget, notes reuse idle tracks, and when
  none is free a releasing note is stolen: the quietest, sparing small parts (bass, solos).
  plan_tracks picks the 2-/1-oscillator split for the whole song.
- Levels: each note's MT-32 loudness (sample peak x velocity x key x CC7 responses, measured
  in Munt); the song's loudest moment is volume 64. Per-row `Cxx` follow each sound's
  envelope and release.

Rules and measurements: docs/ninjatrackerplus.md "Track allocation" and "Build options".
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import mido
import numpy as np

from .ntpsamples import NOTES, ModSample

ROWS = 64                                 # rows per pattern
MI1_TICK = 0.5 / 120                      # seconds per MI1 tick (120 PPQN, 120 BPM)
RHYTHM = 9
CELL_EMPTY = (0, 0, -1, 0)                # note, instrument, effect (-1: none), parameter
FX_JUMP, FX_VOLUME, FX_EXTENDED, FX_SPEED = 0xB, 0xC, 0xE, 0xF
NOTE_DELAY = 0xD0                         # EDx: delay the note by x ticks


# ---------------------------------------------------------------------------
# Notes

@dataclass
class Note:
    t: float            # seconds
    off: float
    key: int
    vel: int
    channel: int
    volume: int = 100   # channel CC7 at note-on (MT-32 power-on part volume is 100)


def read_notes(path: Path) -> tuple[list[Note], dict[int, int], float]:
    """(notes, first program per channel, end s). Cached per file (path + mtime): a build reads
    each song several times (sample planning, level passes, conversion). Callers must not
    modify the returned notes."""
    st = Path(path).stat()
    return _read_notes(str(Path(path).resolve()), st.st_mtime_ns, st.st_size)


@functools.lru_cache(maxsize=None)
def _read_notes(path: str, _mtime: int, _size: int) -> tuple[list[Note], dict[int, int], float]:
    mf = mido.MidiFile(path)
    tempos = {msg.tempo for trk in mf.tracks for msg in trk if msg.type == "set_tempo"}
    if len(tempos) > 1:          # MI1 songs have one tempo; times below assume a single one
        raise ValueError(f"{path}: tempo changes are not supported")
    tempo = next(iter(tempos), 500_000)
    events = []
    for trk in mf.tracks:
        t = 0
        for msg in trk:
            t += msg.time
            events.append((t, msg))
    events.sort(key=lambda e: e[0])
    sec = lambda tick: tick * tempo / 1e6 / mf.ticks_per_beat
    programs: dict[int, int] = {}
    notes: list[Note] = []
    sounding: dict[tuple[int, int], list[Note]] = {}
    volume: dict[int, int] = {}
    end = 0.0
    for tick, m in events:
        if m.type == "program_change":
            programs.setdefault(m.channel, m.program)
        elif m.type == "control_change" and m.control == 7:
            volume[m.channel] = m.value
        elif m.type == "note_on" and m.velocity > 0:
            sounding.setdefault((m.channel, m.note), []).append(
                Note(sec(tick), sec(tick), m.note, m.velocity, m.channel, volume.get(m.channel, 100)))
        elif m.type in ("note_on", "note_off") and sounding.get((m.channel, m.note)):
            n = sounding[(m.channel, m.note)].pop(0)
            n.off = sec(tick)
            notes.append(n)
        elif m.type == "end_of_track":
            end = max(end, sec(tick))
    for lst in sounding.values():            # never released: held to the end
        for n in lst:
            n.off = end
            notes.append(n)
    notes.sort(key=lambda n: (n.t, n.channel, n.key))
    return notes, programs, max(end, max((n.off for n in notes), default=0))


def detect_grid(onsets: list[float], unit_range=(14, 80)) -> tuple[float, float]:
    """(unit, phase) in MI1 ticks maximising circular concentration of the onsets."""
    return _detect_grid(tuple(onsets), tuple(unit_range))


@functools.lru_cache(maxsize=None)
def _detect_grid(onsets: tuple[float, ...], unit_range: tuple[int, int]) -> tuple[float, float]:
    on = np.array(sorted(set(onsets))) / MI1_TICK
    best = (0.0, 30.0, 0.0)
    for P in np.arange(unit_range[0], unit_range[1], 0.05):
        z = np.mean(np.exp(2j * np.pi * on / P))
        if abs(z) > best[0]:
            best = (abs(z), P, (np.angle(z) / (2 * np.pi) * P) % P)
    return best[1], best[2]


def sample_for(programs: dict, prog: int, key: int) -> int | None:
    """Sample index for a program and key: programs[prog] is an index, or a list of
    (lo, hi, index) key ranges (NTP-only key splits, e.g. a low-register organ sample)."""
    v = programs.get(prog)
    if isinstance(v, list):                      # in range, else the nearest range
        return min(v, key=lambda r: max(r[0] - key, key - r[1], 0))[2]
    return v


# ---------------------------------------------------------------------------
# Pieces of a conversion

@dataclass
class Report:
    notes: int = 0
    dropped: int = 0
    delayed: int = 0
    vel_in_cell: int = 0
    vel_preceding: int = 0
    vel_default: int = 0
    vel_snapped: int = 0       # delayed notes moved to a row so their velocity fits in-cell
    level_ref: float = 1.0      # note loudness that maps to MOD volume 64
    tempo_changes: int = 0
    track_kinds: list = field(default_factory=list)   # "melodic" (2 oscillators) / "drum" (1), in MOD order
    sample_volumes: dict = field(default_factory=dict)  # sample index -> note-on volumes in this song
    dropped_by: dict = field(default_factory=dict)      # sound name -> notes dropped for lack of oscillators
    halted: int = 0             # releases ended with the silent "rest" one-shot
    tracks_used: int = 0
    oscillators: int = 0
    stolen: int = 0
    stolen_levels: list = field(default_factory=list)   # MOD volume each stolen release had at the cut
    stolen_parts: list = field(default_factory=list)    # notes active in the stolen note's part (channel)
    rows: int = 0
    patterns: int = 0
    tempo: int = 0
    speed: int = 0
    row_ms: float = 0.0
    timing_err_ms: float = 0.0
    melodic_cap: int | None = None   # planned 2-oscillator track cap (plan_tracks); None: first come
    borrow: str = ""                 # drum-path borrowing order plan_tracks chose ("last" / "first")


def tempo_commands(ideal: float, speed: int, rows: int, can_set: Callable[[int], bool],
                   tol: float) -> list[tuple[int, int]]:
    """[(row, tempo)] keeping the playback clock within ~`tol` s of a grid of rows lasting
    2.5 * speed / `ideal` s each, using the two integer tempos around `ideal`. A change is
    only written where `can_set(row)` (a free effect cell); row 0 always gets the start tempo.
    Hysteresis (`tol`) keeps the changes sparse: every ~tol / (per-row error) rows."""
    lo, hi = math.floor(ideal), math.ceil(ideal)
    row_ideal = 2.5 * speed / ideal
    if lo == hi:
        return [(0, lo)]
    cur = lo if ideal - lo >= 0.5 else hi
    out = [(0, cur)]
    err = 0.0                                            # actual - ideal time at the start of row r
    for r in range(1, rows):
        err += 2.5 * speed / cur - row_ideal
        want = cur
        if err > tol:
            want = hi                                    # running late: shorter rows
        elif err < -tol:
            want = lo
        if want != cur and can_set(r):
            cur = want
            out.append((r, cur))
    return out


def ntp_oscillators(track_osc: list[int]) -> list[int] | None:
    """First oscillator per track as Ninjaforce's converter assigns them (determine_osc_per_track):
    first-fit in track order over oscillators 0-30, pairs starting on even oscillators. None if
    some track gets no oscillator (the converter then silently writes 0)."""
    used = [False] * 31
    out = []
    for need in track_osc:
        for i in range(0, 31, need):
            if all(i + j < 31 and not used[i + j] for j in range(need)):
                for j in range(need):
                    used[i + j] = True
                out.append(i)
                break
        else:
            return None
    return out


class Grid:
    """cells[track][row] = [note(0-72), instrument(0-31), effect, param]; effect -1 = empty."""

    def __init__(self, tracks: int):
        self.cells: list[dict[int, list[int]]] = [dict() for _ in range(tracks)]

    def get(self, tr: int, row: int) -> list[int]:
        """The cell, created empty if needed."""
        return self.cells[tr].setdefault(row, list(CELL_EMPTY))

    def free(self, tr: int, row: int) -> bool:
        """No note and no effect."""
        c = self.cells[tr].get(row)
        return c is None or (c[0] == 0 and c[2] < 0)

    def effect_free(self, tr: int, row: int) -> bool:
        c = self.cells[tr].get(row)
        return c is None or c[2] < 0

    def set_effect(self, tr: int, row: int, effect: int, param: int) -> list[int]:
        c = self.get(tr, row)
        c[2], c[3] = effect, param
        return c


def env_at(s: ModSample, dt: float, width: float, t_loop: float | None = None) -> float:
    """Mean envelope gain over [dt - width/2, dt + width/2]: one Cxx per row stands for the
    whole row, so averaging (not point sampling) keeps tremolo from aliasing into jumps.
    Before `t_loop` (when this note's attack hands over to the loop) the gain is 1: the
    attack is raw source audio and already carries its own contour; env_g is relative to
    the loop, which is taken later in the sustain."""
    ts = np.linspace(dt - width / 2, dt + width / 2, 9)
    g = np.interp(ts, s.env_t, s.env_g, left=s.env_g[0], right=s.env_g[-1])
    if t_loop is not None:
        g = np.where(ts < t_loop, 1.0, g)
    return float(np.mean(g))


@dataclass(frozen=True)
class _Timing:
    """Rows on the song's grid: `row_s` seconds each, `speed` ticks per row, MOD `tempo`
    (2.5 * speed / row_s, rounded) and a lead-in `shift` that aligns the grid (>= 1 row)."""
    row_s: float
    speed: int
    tempo: int
    shift: float
    quantize: float             # 0 = keep micro-timing (EDx), 1 = snap to rows

    @property
    def tick_s(self) -> float:
        return self.row_s / self.speed

    @property
    def tempo_ideal(self) -> float:
        return 2.5 * self.speed / self.row_s

    def place(self, t: float) -> tuple[int, int]:
        """(row, tick within the row) for a time in the song."""
        n = (t + self.shift) / self.tick_s
        row_start = round(n / self.speed) * self.speed
        n = round(n + self.quantize * (row_start - n))
        r, d = divmod(n, self.speed)
        return int(r), int(d)

    def error(self, r: int, d: int, t: float) -> float:
        """How far (s) row r, tick d is from time t."""
        return abs((r * self.speed + d) * self.tick_s - (t + self.shift))


def _timing(notes: list[Note], cfg: dict, song_cfg: dict) -> _Timing:
    unit, phase = song_cfg.get("unit_ticks"), song_cfg.get("phase_ticks")
    if unit is None:
        unit, phase = detect_grid([n.t for n in notes])
    phase = phase or 0.0
    rpu, speed = song_cfg.get("rows_per_unit", cfg["rows_per_unit"]), song_cfg.get("speed", cfg["speed"])
    row_s = unit * MI1_TICK / rpu
    # MOD tempo 32-255: change the speed (ticks per row) until the tempo fits
    tempo = round(2.5 * speed / row_s)
    while tempo > 255:
        speed -= 1
        tempo = round(2.5 * speed / row_s)
    while tempo < 32:
        speed += 1
        tempo = round(2.5 * speed / row_s)
    shift = (row_s - (phase * MI1_TICK) % row_s) % row_s + row_s      # grid aligned, >= 1 row lead-in
    return _Timing(row_s, speed, tempo, shift, song_cfg.get("quantize", cfg["quantize"]))


@dataclass
class _Track:
    kind: str                   # "melodic" (2 oscillators) or "drum" (1, any one-shot)
    inst: int = 0               # current sample, 1-based
    busy: int = -1              # last row with cells for the current note
    off: int = -1               # note-off row of the current melodic note (-1: drum)
    oneshot_end: float = -1.0   # s when the current one-shot's sample ends
    chan: int = -1              # MIDI channel of the current note


class _Arranger:
    """One conversion's state: the grid, its tracks (grid.cells[i] <-> tracks[i]) and the
    options, which come from the song's config, then the defaults."""

    def __init__(self, samples: list[ModSample], cfg: dict, song_cfg: dict, rep: Report):
        self.samples, self.rep = samples, rep
        self.opt = lambda k, default=None: song_cfg.get(k, cfg.get(k, default))
        self.budget = self.opt("oscillators", 31)
        self.mel_cap = song_cfg.get("melodic_tracks")    # None: first come; else 2-osc tracks <= this,
        self.grid = Grid(0)                              # 1-osc tracks <= budget - 2 x this
        self.tracks: list[_Track] = []
        self.osc_used = 0
        self.rest = next((i for i, x in enumerate(samples) if x.kind == "rest"), None)

    # --- tracks

    def level_at(self, tr: int, r: int) -> int:
        """MOD volume track `tr` plays at row r: its latest Cxx (or a delayed note's default)."""
        cells = self.grid.cells[tr]
        for rr in range(r, -1, -1):
            c = cells.get(rr)
            if c is None:
                continue
            if c[2] == FX_VOLUME:
                return c[3]
            if c[0] and c[1]:
                return self.samples[c[1] - 1].default_volume
        return 0

    def new_track(self, kind: str) -> int:
        self.grid.cells.append({})
        self.tracks.append(_Track(kind))
        self.osc_used += 1 if kind == "drum" else 2
        return len(self.tracks) - 1

    def melodic_count(self) -> int:
        return sum(t.kind == "melodic" for t in self.tracks)

    def choose_track(self, s: ModSample, si: int, kind: str, r: int) -> int | None:
        """A track for a note starting at row r (docs/ninjatrackerplus.md "Track allocation"):
        an idle track of its kind (the one that last played this sample first), else a new
        track within the budget (and the planned split), else a steal; None if nothing is
        releasing either (the note is dropped)."""
        tracks = range(len(self.tracks))
        free = lambda k: [t for t in tracks if self.tracks[t].kind == k and self.tracks[t].busy < r
                          and self.grid.free(t, r)]
        cands = free(kind)
        borrow = self.opt("drum_borrow", "last")
        fits_new = self.osc_used + (1 if kind == "drum" else 2) <= self.budget
        if fits_new and self.mel_cap is not None:         # planned split (plan_tracks)
            n_mel = self.melodic_count()
            fits_new = (n_mel < self.mel_cap if kind == "melodic"
                        else len(self.tracks) - n_mel < self.budget - 2 * self.mel_cap)
        if not cands and kind == "drum" and (borrow == "first" or (borrow == "last" and not fits_new)):
            cands = free("melodic")                       # a 2-oscillator track can play a one-shot
        if cands:
            return min(cands, key=lambda t: (self.tracks[t].inst != si + 1, t))
        if fits_new:
            return self.new_track(kind)
        if (kind == "drum" and self.mel_cap is not None and borrow != "never"
                and self.melodic_count() < self.mel_cap and self.osc_used + 2 <= self.budget):
            return self.new_track("melodic")              # a planned melodic track, opened early by a drum
        return self.steal(s, kind, r, tracks)

    def steal(self, s: ModSample, kind: str, r: int, tracks: range) -> int | None:
        """Cut a releasing note of the same kind. A release already at volume 0 (only its
        final silence row left) costs nothing. Then prefer a part playing more than
        `steal_protect` notes (a chord loses one voice of many; a bass line or a solo/duet
        keeps its tail); then the quietest release, then the one nearest its end. (The earliest
        note-off cut audible tails instead.)"""
        releasing = [t for t in tracks if self.tracks[t].kind == kind and 0 <= self.tracks[t].off <= r]
        if not releasing:
            self.rep.dropped += 1
            self.rep.dropped_by[s.name] = self.rep.dropped_by.get(s.name, 0) + 1
            return None

        def part_size(t: int) -> int:
            return sum(1 for u in tracks if self.tracks[u].chan == self.tracks[t].chan and self.tracks[u].busy >= r)
        protect = self.opt("steal_protect", 0)
        tr = min(releasing, key=lambda t: (self.level_at(t, r) > 0, part_size(t) <= protect,
                                           self.level_at(t, r), self.tracks[t].busy, self.tracks[t].off))
        self.rep.stolen_parts.append(part_size(tr))
        cut = self.level_at(tr, r)
        self.rep.stolen_levels.append(cut)
        for row in [x for x in self.grid.cells[tr] if x >= r]:
            del self.grid.cells[tr][row]
        self.rep.stolen += cut > 0                        # level 0: only the release's final silence row
        return tr

    # --- cells

    def preceding_silent(self, tr: int, s: ModSample, si: int, r: int, row_s: float) -> bool:
        """Can the cell before row r carry this note's velocity? Only on a one-shot track still
        holding this sample, silent by then (sample ended, no cells): a Cxx there changes
        nothing audible."""
        t = self.tracks[tr]
        return (s.kind == "drum" and t.inst == si + 1 and t.oneshot_end <= (r - 1) * row_s
                and r - 1 > t.busy and self.grid.effect_free(tr, r - 1))

    def write_envelope(self, tr: int, s: ModSample, n: Note, r: int, vf: float, timing: _Timing,
                       hyst: float) -> int:
        """Envelope rows until note-off, then the release; returns the last row written. A
        melodic one-shot (no loop) is silent once its sample ends: it holds the track only until
        then (or its note-off, if earlier), carries its own decay (no envelope rows), and needs
        a release only when the note-off comes first."""
        row_s = timing.row_s
        # a lower note plays the sample slower: it lasts longer than at key0, and its attack
        # hands over to the loop later
        slower = 2 ** (-(n.key - s.key0) / 12)
        oneshot_end = None if s.loop_len else n.t + s.seconds * slower
        cut = oneshot_end is not None and n.off < oneshot_end
        r_off, _ = timing.place(n.off if oneshot_end is None else min(n.off, oneshot_end))
        r_off = max(r_off, r + 1)
        self.tracks[tr].off = r_off
        t_loop = s.env_t[0] * slower
        last, prev = r, None
        for rr in range(r + 1, r_off if oneshot_end is None else r + 1):
            target = vf * env_at(s, (rr - r) * row_s, row_s, t_loop)
            # hysteresis: a slowly moving target near x.5 made the volume flicker between
            # neighbours (4-5-4: ~2 dB steps at low volumes), heard as choppy sustains
            if prev is not None and abs(target - prev) < hyst:
                last = rr
                continue
            vv = max(0, min(64, round(target)))
            if vv != prev and self.grid.effect_free(tr, rr):
                self.grid.set_effect(tr, rr, FX_VOLUME, vv)
                prev = vv
            last = rr
        v_off = vf * env_at(s, (r_off - r) * row_s, row_s, t_loop)
        steps = max(1, int(round(len(s.rel_g) * 0.01 / row_s)))
        for i in range(steps + 1 if oneshot_end is None or cut else 0):
            rr = r_off + i
            gg = float(np.interp(i * row_s, np.arange(len(s.rel_g)) * 0.01, s.rel_g, right=0.0)) if i < steps else 0.0
            vv = max(0, min(64, round(v_off * gg)))
            if self.grid.effect_free(tr, rr) and self.grid.cells[tr].get(rr, CELL_EMPTY)[0] == 0:
                c = self.grid.set_effect(tr, rr, FX_VOLUME, vv)
                if vv == 0 and self.rest is not None:     # stop the silent loop (ntpsamples.rest_sample)
                    c[0], c[1] = self.samples[self.rest].base_note + 1, self.rest + 1
                    self.rep.halted += 1                  # (the track keeps the note's sample: choice unchanged)
            last = rr
            if vv == 0:
                break
        if not s.loop_len:
            last = max(last, r_off - 1)                   # sounding until its sample (or note) ends
        return last

    # --- finishing

    def finish(self, timing: _Timing, end: float) -> int:
        """Pattern count, track order (2-oscillator tracks first), tempo commands, speed and
        tempo on row 0, and the loop jump on the last row."""
        rep, grid = self.rep, self.grid
        rows_needed = max([max(c, default=0) for c in grid.cells]) + 1
        rows_needed = max(rows_needed, int((end + timing.shift) / timing.row_s) + 1)
        patterns = math.ceil(rows_needed / ROWS)
        if patterns > 128:
            raise ValueError(f"{patterns} patterns > 128 (MOD order list); raise row length")
        rep.rows, rep.patterns = rows_needed, patterns
        rep.tracks_used, rep.oscillators = len(grid.cells), self.osc_used
        if len(grid.cells) < 2:
            self.new_track("drum")
        # Two-oscillator (looped) tracks first: Ninjaforce's converter assigns oscillators first-fit
        # in track order with pairs on even oscillators, so interleaved 1- and 2-oscillator tracks
        # strand odd oscillators; tracks that then find no room silently get oscillator 0 (PHP
        # "Undefined array key" at ntpconverter_lib.php:1613), clashing with track 1.
        order = sorted(range(len(grid.cells)), key=lambda t: (self.tracks[t].kind != "melodic", t))
        grid.cells = [grid.cells[t] for t in order]
        self.tracks = [self.tracks[t] for t in order]
        rep.track_kinds = [t.kind for t in self.tracks]
        tracks = range(len(grid.cells))
        changes = tempo_commands(timing.tempo_ideal, timing.speed, patterns * ROWS,
                                 lambda r: any(grid.effect_free(t, r) for t in tracks),
                                 self.opt("tempo_tolerance_ms", 2.0) / 1000)
        tempo = changes[0][1]
        rep.tempo, rep.tempo_changes = tempo, len(changes) - 1
        for r, t in changes[1:]:
            grid.set_effect(next(tr for tr in tracks if grid.effect_free(tr, r)), r, FX_SPEED, t)
        for tr, param in enumerate((timing.speed, tempo)):    # speed and tempo on the lead-in row
            if not grid.free(tr, 0):
                raise AssertionError("row 0 must be free (lead-in)")
            grid.set_effect(tr, 0, FX_SPEED, param)
        last_row = patterns * ROWS - 1
        tr = next((t for t in tracks if grid.effect_free(t, last_row)), None)
        if tr is not None:
            grid.set_effect(tr, last_row, FX_JUMP, 0)          # loop to the start (like the game's FF)
        return patterns


# ---------------------------------------------------------------------------
# Conversion

def convert_song(path: Path, samples: list[ModSample], programs: dict, drums: dict, cfg: dict, song_cfg: dict,
                 cc7: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[Grid, int, Report]:
    """(grid, patterns, report) for one song. `cc7`: (CC7 values, gains vs 100) from
    ntpsamples.cc7_curve(); None ignores channel volume. Sets each sample's default_volume
    for this song (the MOD writer stores it)."""
    notes, chan_prog, end = read_notes(path)
    rep = Report()
    timing = _timing(notes, cfg, song_cfg)
    rep.tempo, rep.speed, rep.row_ms = timing.tempo, timing.speed, timing.row_s * 1000
    arr = _Arranger(samples, cfg, song_cfg, rep)

    # Loudness of a note as the MT-32 plays it: sample peak x velocity response x CC7 curve.
    # The song is normalised so its loudest moment (99.5th percentile over notes, including
    # sounds that swell after their attack) is MOD volume 64.
    def sample_of(n: Note) -> int | None:
        if n.channel == RHYTHM:
            return drums[n.key][0] if n.key in drums else None
        return sample_for(programs, chan_prog.get(n.channel, -1), n.key)

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
    rep.sample_volumes = by_sample
    fixed = song_cfg.get("default_volumes")          # shared mode: the same for every song
    for si, s in enumerate(samples):
        if s.kind == "rest":
            s.default_volume = 0
        elif fixed is not None:
            s.default_volume = fixed.get(si, 32)
        else:
            s.default_volume = int(np.median(by_sample[si])) if si in by_sample else 32

    hyst = arr.opt("volume_hysteresis", 0.75)
    snap_db = arr.opt("delay_snap_db")
    flexible = arr.opt("melodic_one_shots", "any") == "any"
    onsets = {ch: np.array([m.t for m in notes if m.channel == ch]) for ch in {m.channel for m in notes}}
    speed, tick_s, row_s = timing.speed, timing.tick_s, timing.row_s
    err_sum = 0.0
    for n in notes:
        rep.notes += 1
        if n.channel == RHYTHM:
            hit = drums.get(n.key)
            if hit is None:
                rep.dropped += 1
                continue
            si, note_idx = hit[:2]
        else:
            si = sample_for(programs, chan_prog.get(n.channel, -1), n.key)
            if si is None:
                rep.dropped += 1
                continue
            s0 = samples[si]
            note_idx = s0.base_note + (n.key - s0.key0)
            if not 0 <= note_idx < NOTES:            # outside the table: fold by octaves
                note_idx += 12 * ((0 - note_idx) // 12 + 1) if note_idx < 0 else -12 * ((note_idx - NOTES) // 12 + 1)
        s = samples[si]
        r, d = timing.place(n.t)
        err_sum += timing.error(r, d, n.t)
        # Melodic one-shots (Xylophone) free their track when the sample ends (MI162 8 -> 0
        # dropped). `melodic_one_shots = "any"` (default) sends them down the drum path: a free
        # 1-oscillator track first, else a free 2-oscillator one, and the track plan bounds how
        # many 1-oscillator tracks there are. Under first come that starved the percussion
        # (MI160: 16 -> 31-77 dropped), so "melodic" (2-oscillator tracks only) remains.
        # A loop starting at 0 needs 1 oscillator (converter rule): it shares the 1-oscillator tracks.
        one_osc = s.kind == "drum" or (s.loop_len and not s.loop_start) or (not s.loop_len and flexible)
        tr = arr.choose_track(s, si, "drum" if one_osc else "melodic", r)
        if tr is None:
            continue
        track = arr.tracks[tr]
        same = track.inst == si + 1
        vf = 64 * amp(s, n) / ref                    # unrounded, for the envelope rows
        v = vol_for(s, n)
        # A delayed note's EDx takes the effect column, so it starts at the sample's default
        # volume until the next row unless the preceding cell can carry its velocity (a silent
        # one-shot track). When that start would be more than `delay_snap_db` off, move the note
        # to the nearest row instead (at most speed/2 ticks: one tick at speed 3) and put its
        # velocity in the cell: a little timing for a correct onset level.
        if d and snap_db is not None:
            off_db = abs(20 * math.log10(max(s.default_volume, 1) / max(v, 1)))
            move = speed - d if speed - d <= d else -d          # ticks: to the next row or back to this one
            # don't collapse a flam, roll or grace note onto a neighbour of the same part
            gaps = (onsets[n.channel] - n.t) / tick_s
            collapses = bool(np.any((np.abs(gaps) >= 0.5) & (np.abs(gaps - move) < 0.5)))
            if not arr.preceding_silent(tr, s, si, r, row_s) and off_db > snap_db and not collapses:
                err_sum -= timing.error(r, d, n.t)
                if move > 0:                         # nearer the next row: start there
                    r += 1
                d = 0
                err_sum += timing.error(r, 0, n.t)
                rep.vel_snapped += 1
        cell = arr.grid.get(tr, r)
        cell[0] = note_idx + 1
        silent_before = False
        if d == 0:
            cell[1], cell[2], cell[3] = si + 1, FX_VOLUME, v
            rep.vel_in_cell += 1
        else:
            rep.delayed += 1
            silent_before = arr.preceding_silent(tr, s, si, r, row_s)
            if silent_before:
                arr.grid.set_effect(tr, r - 1, FX_VOLUME, v)
                cell[1] = 0                          # keep the track's volume
                rep.vel_preceding += 1
            else:
                cell[1] = si + 1                     # default volume; corrected next row
                rep.vel_default += 1
            cell[2], cell[3] = FX_EXTENDED, NOTE_DELAY | d
        track.inst, track.chan, track.off = si + 1, n.channel, -1
        if not s.loop_len:
            track.oneshot_end = (r * speed + d) * tick_s + s.seconds
        last = r
        if s.kind == "drum":
            if d and not silent_before and arr.grid.effect_free(tr, r + 1) and v != s.default_volume:
                arr.grid.set_effect(tr, r + 1, FX_VOLUME, v)
                last = r + 1
        else:
            last = arr.write_envelope(tr, s, n, r, vf, timing, hyst)
        track.busy = last

    rep.timing_err_ms = 1000 * err_sum / max(1, rep.notes - rep.dropped)
    patterns = arr.finish(timing, end)
    return arr.grid, patterns, rep


def allocation_cost(rep: Report) -> int:
    """Polyphony loss of a conversion: a dropped note counts as a full-volume (64) cut, plus
    the MOD volume each audible steal cut."""
    return 64 * rep.dropped + sum(v for v in rep.stolen_levels if v > 0)


def plan_tracks(path: Path, samples, programs, drums, cfg: dict, song_cfg: dict, cc7=None
                ) -> tuple[Grid, int, Report]:
    """convert_song with the track layout chosen for the whole song: the least polyphony loss
    (allocation_cost), then the fewest tracks (pattern data is 256 B per track per pattern).
    Tried for both drum-path borrowing orders ("last": open a 1-oscillator track while the
    budget allows; "first": reuse an idle 2-oscillator track first, which often needs fewer
    tracks), each with the melodic-track cap search of _plan_split."""
    if not song_cfg.get("track_plan", cfg.get("track_plan", True)):
        return convert_song(path, samples, programs, drums, cfg, song_cfg, cc7)
    borrow = song_cfg.get("drum_borrow", cfg.get("drum_borrow", "last"))
    orders = [borrow] if borrow == "never" else [borrow] + [b for b in ("last", "first") if b != borrow]
    key = lambda res: (allocation_cost(res[2]), res[2].tracks_used)
    best = None
    for b in orders:
        res = _plan_split(path, samples, programs, drums, cfg, {**song_cfg, "drum_borrow": b}, cc7, key)
        res[2].borrow = b
        if best is None or key(res) < key(best):
            best = res
    return best


def _plan_split(path, samples, programs, drums, cfg, song_cfg, cc7, key):
    """The 2-/1-oscillator split for one borrowing order. Tracks are typed when created, so first
    come lets early notes fix the split (loop-only organ notes opened 11 1-oscillator tracks in
    MI160 and left 10 melodic). Try capping the melodic tracks at M (1-oscillator tracks get the
    rest of the budget), walking from the first-come count while the cost falls (it has one
    valley per song); keep first come if nothing beats it. Songs with no loss keep first come."""
    first = convert_song(path, samples, programs, drums, cfg, song_cfg, cc7)
    if allocation_cost(first[2]) == 0:
        return first
    tried = {}

    def at(m: int):
        if m not in tried:
            tried[m] = convert_song(path, samples, programs, drums, cfg, {**song_cfg, "melodic_tracks": m}, cc7)
            tried[m][2].melodic_cap = m
        return tried[m]

    budget = song_cfg.get("oscillators", cfg.get("oscillators", 31))
    m0 = first[2].track_kinds.count("melodic")
    best = min((at(m0),) + tuple(at(m) for m in (m0 - 1, m0 + 1) if 0 <= m <= budget // 2), key=key)
    step = 1 if best[2].melodic_cap > m0 else -1 if best[2].melodic_cap < m0 else 0
    m = best[2].melodic_cap
    while step and 0 <= m + step <= budget // 2 and key(at(m + step)) < key(best):
        m += step
        best = at(m)
    return best if key(best) < key(first) else first
