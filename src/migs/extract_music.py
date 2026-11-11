# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract the MT-32 ('RO') music of a SCUMM v4 game to Standard MIDI Files.

For each distinct RO sound, writes:
  <out>/midi/mt32/NNN[-name].mid   faithful transcription for an MT-32
  <out>/midi/gm/NNN[-name].mid     General MIDI conversion (docs/mt32-to-gm.md)
  <out>/patches/NNN[-name].toml    per-channel instrument notes and statistics

Song names come from the game's optional song_names.toml (config/games/<game>/):
  [names]
  160 = "main-title"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import mido

from . import games, mt32, ro
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

    @property
    def stem(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return f"{self.sound_id:03d}" + (f"-{slug}" if slug else "")

    @property
    def title(self) -> str:
        return f"{self.label} sound {self.sound_id}".lstrip() + (f": {self.name}" if self.name else "")

    @property
    def seconds(self) -> float:
        return ro.ticks_to_seconds(self.track.end_tick)


# ---------------------------------------------------------------------------
# MIDI generation

def _gm_transform(ev: ro.Event) -> ro.Event | None:
    """Map one MT-32 event to General MIDI; None drops it."""
    if ev.kind == Kind.PROGRAM:
        return ro.Event(ev.tick, ev.kind, ev.channel, mt32.MT32_TO_GM[ev.a])
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


def _channel_track(events: list[ro.Event], end_tick: int, name: str) -> mido.MidiTrack:
    trk = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    trk += map(_message, events, _deltas(e.tick for e in events))
    trk.append(mido.MetaMessage("end_of_track", time=end_tick - (events[-1].tick if events else 0)))
    return trk


def _conductor(song: Song, gm: bool) -> mido.MidiTrack:
    """Name, tempo, GM reset, loop points (loopStart/loopEnd markers) and RO sync markers (Ax)."""
    t = song.track
    trk = mido.MidiTrack([
        mido.MetaMessage("track_name", name=song.title, time=0),
        mido.MetaMessage("set_tempo", tempo=ro.TEMPO_US, time=0),
    ])
    if gm:
        trk.append(mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01], time=0))  # GM System On
    markers = [(ev.tick, f"A{ev.channel:X}") for ev in t.events if ev.kind == Kind.MARKER]
    if t.loops:
        markers = [(0, "loopStart"), *markers, (t.end_tick, "loopEnd")]
    for (tick, text), dt in zip(markers, _deltas(tick for tick, _ in markers)):
        trk.append(mido.MetaMessage("marker", text=text, time=dt))
    trk.append(mido.MetaMessage("end_of_track", time=t.end_tick - (markers[-1][0] if markers else 0)))
    return trk


def _track_label(ch: int, events: list[ro.Event], gm: bool) -> str:
    """Rhythm, or the channel's first program's name."""
    if ch == mt32.RHYTHM_CHANNEL:
        return "Rhythm"
    program = next((e.a for e in events if e.kind == Kind.PROGRAM), None)
    if program is None:
        return ""
    return (mt32.GM_NAMES if gm else mt32.MT32_PRESET_NAMES)[program]


def build_midi(song: Song, gm: bool) -> mido.MidiFile:
    """A type-1 MIDI file: the conductor track, then one track per channel used."""
    mf = mido.MidiFile(type=1, ticks_per_beat=ro.PPQN)
    mf.tracks.append(_conductor(song, gm))
    by_ch: dict[int, list[ro.Event]] = defaultdict(list)
    for ev in song.track.events:
        if ev.kind != Kind.MARKER:
            by_ch[ev.channel].append(ev)
    for ch, evs in sorted(by_ch.items()):
        if gm:
            evs = _gm_channel_defaults(ch) + [e for e in map(_gm_transform, evs) if e]
        name = f"Ch {ch + 1} {_track_label(ch, evs, gm)}".strip()
        mf.tracks.append(_channel_track(evs, song.track.end_tick, name))
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
            case Kind.PROGRAM if ev.a not in s.programs:
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


def _channel_notes(ch: int, s: ChannelStats) -> list[str]:
    out = ["", "[[channel]]", f"channel = {ch + 1}"]
    if ch == mt32.RHYTHM_CHANNEL:
        out.append('role = "rhythm"')
    else:
        out.append('role = "melodic"' if s.notes else 'role = "unused"')
        if s.programs:
            p = s.programs[0]
            gm = mt32.MT32_TO_GM[p]
            out += [f"mt32_program = {p}", f"mt32_name = {_toml_str(mt32.MT32_PRESET_NAMES[p])}",
                    f"gm_program = {gm}", f"gm_name = {_toml_str(mt32.GM_NAMES[gm])}"]
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


def patch_notes(song: Song) -> str:
    t = song.track
    source = f"DISK{song.resource_disk:02d}.LEC room {song.resource_room} offset {song.resource_offset}"
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
        f"ticks_per_beat = {ro.PPQN}",
        f"tempo_us_per_beat = {ro.TEMPO_US}",
        f"length_ticks = {t.end_tick}",
        f"length_seconds = {song.seconds:.1f}",
        f"loops = {_toml_bool(t.loops)}",
        f"terminated = {_toml_bool(t.terminated)}",
        f"markers = {sum(e.kind == Kind.MARKER for e in t.events)}",
        f"max_polyphony = {total_polyphony(t.events)}",
    ]
    for ch, s in sorted(channel_stats(t.events).items()):
        out += _channel_notes(ch, s)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------

def load_songs(game: Game, names: dict[str, str], warn: Callable[[str], None] | None = None, *,
               label: str) -> list[Song]:
    """`label` is the game's (game.toml), for titles: "MI1 sound 150"."""
    """Every distinct RO sound in the game, by sound id; byte-identical copies are listed as
    the first one's `duplicates`."""
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


def _read_names(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {str(k): v for k, v in tomllib.loads(path.read_text()).get("names", {}).items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    games.add_argument(ap)
    ap.add_argument("game_dir", type=Path, nargs="?",
                    help="SCUMM v4 game directory (000.LFL, DISKnn.LEC); default the game's `data`")
    ap.add_argument("out_dir", type=Path, nargs="?", help="output directory (default build/<game>/music)")
    ap.add_argument("--names", type=Path,
                    help="song names TOML (optional; default the game's song_names.toml)")
    args = ap.parse_args(argv)
    g = games.load(args.game)
    game_dir = args.game_dir or g.data
    args.out_dir = args.out_dir or g.build_dir / "music"
    warn = lambda m: print("warning:", m, file=sys.stderr)
    if game_dir.resolve() == g.data.resolve():          # the configured release: check it
        for m in g.md5_mismatches():
            warn(f"{m} (not the release {g.config('game.toml')} was tuned on)")

    songs = load_songs(Game(game_dir), _read_names(args.names or g.config("song_names.toml")),
                       warn=warn, label=g.label)
    if not songs:
        print("no RO (MT-32) sounds found", file=sys.stderr)
        return 1

    mt32_dir, gm_dir, patch_dir = (args.out_dir / p for p in ("midi/mt32", "midi/gm", "patches"))
    for d in (mt32_dir, gm_dir, patch_dir):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("[0-9][0-9][0-9]*.*"):   # drop stale outputs (e.g. after renames)
            old.unlink()

    print(f"{'id':>4} {'dups':<12} {'len':>7} {'loop':<4} {'poly':>4}  file")
    for s in songs:
        build_midi(s, gm=False).save(mt32_dir / f"{s.stem}.mid")
        build_midi(s, gm=True).save(gm_dir / f"{s.stem}.mid")
        (patch_dir / f"{s.stem}.toml").write_text(patch_notes(s))
        print(f"{s.sound_id:>4} {','.join(map(str, s.duplicates)) or '-':<12} {s.seconds:6.1f}s "
              f"{'yes' if s.track.loops else 'no':<4} {total_polyphony(s.track.events):>4}  {s.stem}")
    print(f"{len(songs)} songs → {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
