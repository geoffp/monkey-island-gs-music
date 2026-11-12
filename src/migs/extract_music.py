# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract the MT-32 music of a SCUMM game to Standard MIDI Files.

SCUMM v4 (`engine = "scumm-v4"`): every `RO` sound. SCUMM v5 (`"scumm-v5"`): the `ROL ` part
of each sound the game's `[music]` settings select, played through iMUSE's rules (imuse.py);
its custom MT-32 timbres are listed in <out>/timbres.toml (id >= 128 per timbre, the ids the
MIDI files and the NTP build use).

For each distinct sound, writes:
  <out>/midi/mt32/NNN[-name].mid   faithful transcription for an MT-32
  <out>/midi/gm/NNN[-name].mid     General MIDI conversion (docs/mt32-to-gm.md)
  <out>/patches/NNN[-name].toml    per-channel instrument notes and statistics

Song names come from the game's optional song_names.toml (config/games/<game>/; at most 20
ASCII characters, since the NTP build makes them the MOD's title):
  [names]
  160 = "Opening Theme"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import mido

from . import games, imuse, mt32, ro, scumm_v5
from .imuse import read_timbres  # noqa: F401  (re-exported: the NTP build reads what this writes)
from .ro import Kind
from .scumm_v4 import HEADER, Game

PAN, VOLUME, REVERB = 10, 7, 91              # controller numbers


@dataclass
class Song:
    sound_id: int
    duplicates: list[int]        # later sound ids with byte-identical RO data
    name: str
    track: ro.Track
    resource_room: int
    resource_disk: int
    resource_offset: int
    size: int                    # RO block size, header included
    label: str = ""              # the game's label (game.toml), for titles: "MI1 sound 160"
    source: str = ""             # where the data is, for the patch notes (default: a v4 disk)
    ignored: dict[str, int] = field(default_factory=dict)   # iMUSE: what was not followed

    @property
    def stem(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’]", "", self.name.lower())).strip("-")
        return f"{self.sound_id:03d}" + (f"-{slug}" if slug else "")

    @property
    def title(self) -> str:
        return f"{self.label} sound {self.sound_id}".lstrip() + (f": {self.name}" if self.name else "")

    @property
    def seconds(self) -> float:
        return track_seconds(self.track, self.track.end_tick)


def track_seconds(t: ro.Track, tick: int) -> float:
    """Seconds at `tick`, through the track's tempo changes."""
    s, last, us = 0.0, 0, t.tempo_us
    for at, new in t.tempos:
        if at >= tick:
            break
        s += (at - last) * us / 1e6 / t.ppqn
        last, us = at, new
    return s + (tick - last) * us / 1e6 / t.ppqn


@dataclass
class Context:
    """What a game's songs share: its custom timbres (id -> timbre), the SysEx its MT-32
    driver sends at start-up, and GM programs for its custom timbres (by name)."""
    timbres: dict[int, imuse.Timbre] = field(default_factory=dict)
    init: list[bytes] = field(default_factory=list)
    timbre_gm: dict[str, int] = field(default_factory=dict)

    def name(self, program: int, gm: bool = False) -> str:
        if program >= 128:
            tb = self.timbres.get(program)
            return tb.name if tb else f"timbre {program}"
        return (mt32.GM_NAMES if gm else mt32.MT32_PRESET_NAMES)[program]

    def gm_program(self, program: int) -> int:
        if program >= 128:
            tb = self.timbres.get(program)
            return self.timbre_gm.get(tb.name if tb else "", 0)
        return mt32.MT32_TO_GM[program]


def roland_dt1(addr: int, data: bytes) -> bytes:
    """A Roland DT1 SysEx (from the 0x41 byte) writing `data` at 7-bit address `addr`."""
    a = bytes([(addr >> 14) & 0x7F, (addr >> 7) & 0x7F, addr & 0x7F])
    return bytes([0x41, 0x10, 0x16, 0x12]) + a + data + bytes([(128 - sum(a + data) % 128) % 128])


def timbre_temp_addr(part: int) -> int:
    """MT-32 timbre temporary area of part 1-8 (04 00 00, 246 bytes each), as a 7-bit address."""
    linear = (0x04 << 14) + (part - 1) * imuse.TIMBRE_BYTES
    return ((linear >> 14) << 14) | (((linear >> 7) & 0x7F) << 7) | (linear & 0x7F)


def timbre_text(channel: int, program: int, name: str) -> str:
    """The text event that tells readers (ntpsong.read_notes) a channel now plays custom timbre
    `program`; the SysEx beside it is what an MT-32 needs."""
    return f"timbre ch{channel} {program} {name}"


# ---------------------------------------------------------------------------
# MIDI generation

def _gm_transform(ev: ro.Event, ctx: Context | None = None) -> ro.Event | None:
    """Map one MT-32 event to General MIDI; None drops it. A custom timbre becomes a program
    change to its GM equivalent (game.toml [mt32] timbre_gm)."""
    ctx = ctx or Context()
    if ev.kind == Kind.PROGRAM:
        return ro.Event(ev.tick, ev.kind, ev.channel, ctx.gm_program(ev.a))
    if ev.kind == Kind.TIMBRE:
        return ro.Event(ev.tick, Kind.PROGRAM, ev.channel, ctx.gm_program(ev.a))
    if ev.kind == Kind.CC and ev.a == PAN:
        return ro.Event(ev.tick, ev.kind, ev.channel, PAN, mt32.flip_pan(ev.b))
    if (ev.kind in (Kind.NOTE_ON, Kind.NOTE_OFF) and ev.channel == mt32.RHYTHM_CHANNEL
            and ev.a not in mt32.MT32_RHYTHM_KEYS):
        return None  # silent on a stock MT-32
    return ev


def _gm_channel_defaults(ch: int) -> list[ro.Event]:
    """MT-32 power-on state expressed for a GM device, at tick 0."""
    evs = [ro.Event(0, Kind.CC, ch, REVERB, mt32.GM_DEFAULT_REVERB)]
    if 1 <= ch <= 8:
        evs.append(ro.Event(0, Kind.CC, ch, PAN, mt32.flip_pan(mt32.default_pan(ch))))
    return evs


def _deltas(ticks: Iterable[int]) -> Iterator[int]:
    """Absolute ticks -> MIDI delta times."""
    last = 0
    for t in ticks:
        yield t - last
        last = t


def _message(ev: ro.Event, time: int) -> mido.Message:
    match ev.kind:
        case Kind.NOTE_ON | Kind.NOTE_OFF:
            return mido.Message(str(ev.kind), channel=ev.channel, note=ev.a, velocity=ev.b, time=time)
        case Kind.CC:
            return mido.Message("control_change", channel=ev.channel, control=ev.a, value=ev.b, time=time)
        case Kind.PROGRAM:
            return mido.Message("program_change", channel=ev.channel, program=ev.a, time=time)
    raise ValueError(f"no MIDI message for {ev.kind}")


def _channel_track(events: list[ro.Event], end_tick: int, name: str, ctx: Context | None = None) -> mido.MidiTrack:
    trk = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    last = 0
    for ev in events:
        if ev.kind == Kind.TIMBRE:                 # MT-32 rendition: tell readers, then upload it
            tb = (ctx or Context()).timbres.get(ev.a)
            trk.append(mido.MetaMessage("text", text=timbre_text(ev.channel, ev.a, tb.name if tb else ""),
                                        time=ev.tick - last))
            if tb and ev.channel in imuse.MT32_PARTS:
                trk.append(mido.Message("sysex", data=roland_dt1(timbre_temp_addr(ev.channel), tb.data), time=0))
        else:
            trk.append(_message(ev, ev.tick - last))
        last = ev.tick
    trk.append(mido.MetaMessage("end_of_track", time=end_tick - last))
    return trk


def _conductor(song: Song, gm: bool, ctx: Context | None = None) -> mido.MidiTrack:
    """Name, tempo (and its changes), the game's MT-32 driver set-up or a GM reset, loop
    points (loopStart/loopEnd markers) and RO sync markers (Ax)."""
    t = song.track
    trk = mido.MidiTrack([
        mido.MetaMessage("track_name", name=song.title, time=0),
        mido.MetaMessage("set_tempo", tempo=t.tempo_us, time=0),
    ])
    if gm:
        trk.append(mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01], time=0))  # GM System On
    else:
        trk += [mido.Message("sysex", data=s, time=0) for s in (ctx.init if ctx else [])]
    events = [(ev.tick, mido.MetaMessage("marker", text=f"A{ev.channel:X}")) for ev in t.events if ev.kind == Kind.MARKER]
    if t.loops:
        events = [(t.loop_start, mido.MetaMessage("marker", text="loopStart")), *events,
                  (t.end_tick, mido.MetaMessage("marker", text="loopEnd"))]
    events += [(at, mido.MetaMessage("set_tempo", tempo=us)) for at, us in t.tempos]
    events.sort(key=lambda e: e[0])               # stable: loopStart before an A marker at its tick
    last = 0
    for tick, msg in events:
        trk.append(msg.copy(time=tick - last))
        last = tick
    trk.append(mido.MetaMessage("end_of_track", time=t.end_tick - last))
    return trk


def _track_label(ch: int, events: list[ro.Event], gm: bool, ctx: Context | None = None) -> str:
    """Rhythm, or the channel's first program's name."""
    if ch == mt32.RHYTHM_CHANNEL:
        return "Rhythm"
    program = next((e.a for e in events if e.kind in (Kind.PROGRAM, Kind.TIMBRE)), None)
    if program is None:
        return ""
    return (ctx or Context()).name(program, gm and program < 128)


def build_midi(song: Song, gm: bool, ctx: Context | None = None) -> mido.MidiFile:
    """A type-1 MIDI file: the conductor track, then one track per channel used."""
    mf = mido.MidiFile(type=1, ticks_per_beat=song.track.ppqn)
    mf.tracks.append(_conductor(song, gm, ctx))
    by_ch: dict[int, list[ro.Event]] = defaultdict(list)
    for ev in song.track.events:
        if ev.kind != Kind.MARKER:
            by_ch[ev.channel].append(ev)
    for ch, evs in sorted(by_ch.items()):
        if gm:
            evs = _gm_channel_defaults(ch) + [e for e in (_gm_transform(e, ctx) for e in evs) if e]
        name = f"Ch {ch + 1} {_track_label(ch, evs, gm, ctx)}".strip()
        mf.tracks.append(_channel_track(evs, song.track.end_tick, name, ctx))
    return mf


# ---------------------------------------------------------------------------
# Patch notes

@dataclass
class ChannelStats:
    programs: list[int] = field(default_factory=list)     # distinct, in order of first use
    notes: int = 0
    keys: Counter[int] = field(default_factory=Counter)    # key -> note-ons
    poly: int = 0                                          # most notes sounding at once
    volume: int | None = None                              # first CC7
    pan: int | None = None                                 # first CC10 (MT-32 pan sense)
    controllers: set[int] = field(default_factory=set)


def _peak_polyphony(events: Iterable[ro.Event], voice: Callable[[ro.Event], object]) -> int:
    """Most notes sounding at once, a note being identified by `voice(event)`."""
    active: set = set()
    peak = 0
    for ev in events:
        if ev.kind == Kind.NOTE_ON:
            active.add(voice(ev))
            peak = max(peak, len(active))
        elif ev.kind == Kind.NOTE_OFF:
            active.discard(voice(ev))
    return peak


def total_polyphony(events: Iterable[ro.Event]) -> int:
    return _peak_polyphony(events, lambda ev: (ev.channel, ev.a))


def channel_stats(events: list[ro.Event]) -> dict[int, ChannelStats]:
    stats: dict[int, ChannelStats] = defaultdict(ChannelStats)
    for ev in events:
        if ev.kind == Kind.MARKER:
            continue
        s = stats[ev.channel]
        match ev.kind:
            case Kind.PROGRAM | Kind.TIMBRE if ev.a not in s.programs:
                s.programs.append(ev.a)
            case Kind.NOTE_ON:
                s.notes += 1
                s.keys[ev.a] += 1
            case Kind.CC:
                s.controllers.add(ev.a)
                if ev.a == VOLUME and s.volume is None:
                    s.volume = ev.b
                if ev.a == PAN and s.pan is None:
                    s.pan = ev.b
    for ch, s in stats.items():
        s.poly = _peak_polyphony((e for e in events if e.channel == ch and e.kind != Kind.MARKER),
                                 lambda ev: ev.a)
    return dict(stats)


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)    # JSON strings are valid TOML basic strings


def _toml_bool(b: bool) -> str:
    return "true" if b else "false"


def _channel_notes(ch: int, s: ChannelStats, ctx: Context | None = None) -> list[str]:
    out = ["", "[[channel]]", f"channel = {ch + 1}"]
    if ch == mt32.RHYTHM_CHANNEL:
        out.append('role = "rhythm"')
    else:
        out.append('role = "melodic"' if s.notes else 'role = "unused"')
        if s.programs:
            ctx = ctx or Context()
            p = s.programs[0]
            gm = ctx.gm_program(p)
            out += [f"mt32_program = {p}", f"mt32_name = {_toml_str(ctx.name(p))}",
                    f"gm_program = {gm}", f"gm_name = {_toml_str(mt32.GM_NAMES[gm])}"]
            if p >= 128:
                out.append("custom_timbre = true")
            if len(s.programs) > 1:
                out.append(f"program_changes = {s.programs}")
    out += [f"notes = {s.notes}", f"max_polyphony = {s.poly}"]
    keys = sorted(s.keys)
    if keys:
        out.append(f"key_range = [{keys[0]}, {keys[-1]}]")
    if s.volume is not None:
        out.append(f"initial_volume = {s.volume}")
    if s.pan is not None:
        out.append(f"initial_pan_mt32 = {s.pan}")
    if s.controllers:
        out.append(f"controllers = {sorted(s.controllers)}")
    if ch == mt32.RHYTHM_CHANNEL and keys:
        out.append("[channel.rhythm_keys]  # key = [hits, GM name]; unmapped keys are dropped in GM")
        for k in keys:
            name = (mt32.GM_DRUM_NAMES.get(k, "?") if k in mt32.MT32_RHYTHM_KEYS
                    else "UNMAPPED (silent on MT-32)")
            out.append(f"{k} = [{s.keys[k]}, {_toml_str(name)}]")
    return out


def patch_notes(song: Song, ctx: Context | None = None) -> str:
    t = song.track
    source = song.source or f"DISK{song.resource_disk:02d}.LEC room {song.resource_room} offset {song.resource_offset}"
    out = [
        f"# Patch notes for {song.title}",
        "# Generated by migs.extract_music. Channel numbers are 1-based MIDI channels.",
        "# Programs are 0-based. MT-32 programs are preset timbres (see docs/mt32-to-gm.md).",
        "",
        "[song]",
        f"sound_id = {song.sound_id}",
        f"name = {_toml_str(song.name)}",
        f"duplicates = {song.duplicates}",
        f"source = {_toml_str(source)}",
        f"ro_bytes = {song.size}",
        f"ticks_per_beat = {t.ppqn}",
        f"tempo_us_per_beat = {t.tempo_us}",
        *([f"tempo_changes = {len(t.tempos)}"] if t.tempos else []),
        f"length_ticks = {t.end_tick}",
        f"length_seconds = {song.seconds:.1f}",
        f"loops = {_toml_bool(t.loops)}",
        *([f"loop_start_ticks = {t.loop_start}"] if t.loops and t.loop_start else []),
        f"terminated = {_toml_bool(t.terminated)}",
        f"markers = {sum(e.kind == Kind.MARKER for e in t.events)}",
        f"max_polyphony = {total_polyphony(t.events)}",
    ]
    if song.ignored:
        out.append(f"not_followed = {{ {', '.join(f'{_toml_str(k)} = {v}' for k, v in sorted(song.ignored.items()))} }}")
    for ch, s in sorted(channel_stats(t.events).items()):
        out += _channel_notes(ch, s, ctx)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------

def load_songs(game: Game, names: dict[str, str], warn: Callable[[str], None] | None = None, *,
               label: str) -> list[Song]:
    """Every distinct RO sound in the game, by sound id; byte-identical copies are listed as
    the first one's `duplicates`. `label` is the game's (game.toml), for titles: "MI1 sound 150"."""
    by_hash: dict[str, Song] = {}
    unresolved = []
    for sid, (room, _) in enumerate(game.index.sounds):
        if room == 0:
            continue
        try:
            res = game.sound(sid)
        except ValueError:
            # Indexes can hold stale entries that point mid-block (MI1 VGA: sounds 72-97);
            # see docs/scumm-v4-resources.md.
            unresolved.append(sid)
            continue
        if res.tag != b"RO":
            continue
        h = hashlib.sha256(res.payload).hexdigest()
        if h in by_hash:
            by_hash[h].duplicates.append(sid)
            continue
        by_hash[h] = Song(
            sound_id=sid, duplicates=[], name=names.get(str(sid), ""), track=ro.parse(res.payload),
            resource_room=res.room, resource_disk=res.disk, resource_offset=res.offset,
            size=len(res.payload) + HEADER, label=label,
        )
    if unresolved and warn:
        warn(f"skipped {len(unresolved)} index entries that do not point at a sound block: {unresolved}")
    return sorted(by_hash.values(), key=lambda s: s.sound_id)


def game_timbres(game: scumm_v5.Game) -> list[imuse.Timbre]:
    """Every custom timbre in the game's ROL sounds (music and effects alike, so the ids don't
    depend on which sounds are music), by name then content: id = 128 + position."""
    found: dict[str, imuse.Timbre] = {}
    for sid, (room, _) in enumerate(game.sounds):
        if room and (rol := game.sound(sid).parts.get(b"ROL ")):
            for tb in imuse.timbres(imuse.mdhd_smf(rol)):
                found.setdefault(tb.key, tb)
    return sorted(found.values(), key=lambda tb: (tb.name.lower(), tb.key))


def load_songs_v5(game: scumm_v5.Game, names: dict[str, str], music: dict, *, label: str,
                  warn: Callable[[str], None] | None = None) -> tuple[list[Song], list[imuse.Timbre]]:
    """The ROL sounds that are music: ids `music["first_id"]` (default 0) to `last_id`
    (default all), with at least `min_notes` notes and `min_seconds` of playing (game.toml
    [music]). Byte-identical copies are listed as the first one's `duplicates`, as for v4."""
    timbres = game_timbres(game)
    ids = {tb.key: 128 + i for i, tb in enumerate(timbres)}
    first, last = music.get("first_id", 0), music.get("last_id", len(game.sounds))
    min_notes, min_s = music.get("min_notes", 0), music.get("min_seconds", 0.0)
    by_hash: dict[str, Song] = {}
    for sid, (room, _) in enumerate(game.sounds):
        if not room or not first <= sid <= last:
            continue
        res = game.sound(sid)
        rol = res.parts.get(b"ROL ")
        if rol is None:
            continue
        h = hashlib.sha256(rol).hexdigest()
        if h in by_hash:
            by_hash[h].duplicates.append(sid)
            continue
        try:
            track, ignored = imuse.read(rol, ids)
        except ValueError as e:
            if warn:
                warn(f"sound {sid}: {e}; skipped")
            continue
        song = Song(sound_id=sid, duplicates=[], name=names.get(str(sid), ""), track=track,
                    resource_room=res.room, resource_disk=1, resource_offset=res.offset, size=len(rol),
                    label=label, source=f"room {res.room} offset {res.offset} (SOUN, ROL part)", ignored=ignored)
        if sum(e.kind == Kind.NOTE_ON for e in track.events) < min_notes or song.seconds < min_s:
            continue
        by_hash[h] = song
    return sorted(by_hash.values(), key=lambda s: s.sound_id), timbres


def timbres_toml(timbres: list[imuse.Timbre], gm: dict[str, int]) -> str:
    """The game's custom MT-32 timbres (build output: the NTP build renders them through Munt)."""
    out = ["# Custom MT-32 timbres the game's songs upload (iMUSE Roland SysEx), generated by",
           "# migs.extract_music. `program` is the id the MIDI files and the NTP build use for it;",
           "# `data` is the 246-byte timbre (MT-32 timbre temporary area layout).", ""]
    for i, tb in enumerate(timbres):
        out += ["[[timbre]]", f"program = {128 + i}", f"name = {_toml_str(tb.name)}", f'key = "{tb.key}"',
                *([f"gm_program = {gm[tb.name]}"] if tb.name in gm else []), f'data = "{tb.data.hex()}"', ""]
    return "\n".join(out)


def _read_names(path: Path) -> dict[str, str]:
    return games.parse_names(path.parent.name, path.read_text()) if path.exists() else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    games.add_argument(ap)
    ap.add_argument("game_dir", type=Path, nargs="?",
                    help="game directory (v4: 000.LFL, DISKnn.LEC; v5: *.000, *.001); default the game's `data`")
    ap.add_argument("out_dir", type=Path, nargs="?", help="output directory (default build/<game>/extracted)")
    ap.add_argument("--names", type=Path,
                    help="song names TOML (optional; default the game's song_names.toml)")
    args = ap.parse_args(argv)
    g = games.load(args.game)
    game_dir = args.game_dir or g.data
    args.out_dir = args.out_dir or g.build_dir / "extracted"
    warn = lambda m: print("warning:", m, file=sys.stderr)
    if game_dir.resolve() == g.data.resolve():          # the configured release: check it
        for m in g.md5_mismatches():
            warn(f"{m} (not the release {g.config('game.toml')} was tuned on)")

    names = _read_names(args.names) if args.names else g.song_names
    timbres: list[imuse.Timbre] = []
    if g.engine == "scumm-v5":
        songs, timbres = load_songs_v5(scumm_v5.Game(game_dir), names, g.music, label=g.label, warn=warn)
    else:
        songs = load_songs(Game(game_dir), names, warn=warn, label=g.label)
    if not songs:
        print("no MT-32 music found", file=sys.stderr)
        return 1
    ctx = Context({128 + i: tb for i, tb in enumerate(timbres)}, g.mt32_init, g.timbre_gm)
    used = {e.a for s in songs for e in s.track.events if e.kind == Kind.TIMBRE}
    missing_gm = sorted({ctx.timbres[p].name for p in used} - set(g.timbre_gm))
    if missing_gm:
        warn(f"no GM program for custom timbre(s) {missing_gm} (game.toml [mt32] timbre_gm): GM program 0")

    mt32_dir, gm_dir, patch_dir = (args.out_dir / p for p in ("midi/mt32", "midi/gm", "patches"))
    for d in (mt32_dir, gm_dir, patch_dir):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("[0-9][0-9][0-9]*.*"):   # drop stale outputs (e.g. after renames)
            old.unlink()

    print(f"{'id':>4} {'dups':<12} {'len':>7} {'loop':<4} {'poly':>4}  file")
    timbre_file = args.out_dir / "timbres.toml"
    if timbres:
        timbre_file.write_text(timbres_toml(timbres, g.timbre_gm))
    elif timbre_file.exists():
        timbre_file.unlink()
    for s in songs:
        build_midi(s, gm=False, ctx=ctx).save(mt32_dir / f"{s.stem}.mid")
        build_midi(s, gm=True, ctx=ctx).save(gm_dir / f"{s.stem}.mid")
        (patch_dir / f"{s.stem}.toml").write_text(patch_notes(s, ctx))
        print(f"{s.sound_id:>4} {','.join(map(str, s.duplicates)) or '-':<12} {s.seconds:6.1f}s "
              f"{'yes' if s.track.loops else 'no':<4} {total_polyphony(s.track.events):>4}  {s.stem}")
    print(f"{len(songs)} songs → {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
