"""Extract the MT-32 ('RO') music of a SCUMM v4 game to Standard MIDI Files.

For each distinct RO sound, writes:
  <out>/midi/mt32/NNN[-name].mid   faithful transcription for an MT-32
  <out>/midi/gm/NNN[-name].mid     General MIDI conversion (docs/mt32-to-gm.md)
  <out>/patches/NNN[-name].toml    per-channel instrument notes and statistics

Song names come from an optional TOML file (default <out>/song_names.toml):
  [names]
  160 = "main-title"
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import mido

from . import mt32, ro
from .scumm_v4 import Game


@dataclass
class Song:
    sound_id: int
    duplicates: list[int]
    name: str
    track: ro.Track
    resource_room: int
    resource_disk: int
    resource_offset: int
    size: int

    @property
    def stem(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return f"{self.sound_id:03d}" + (f"-{slug}" if slug else "")

    @property
    def title(self) -> str:
        return f"MI1 sound {self.sound_id}" + (f": {self.name}" if self.name else "")


# ---------------------------------------------------------------------------
# MIDI generation

def _gm_transform(ev: ro.Event) -> ro.Event | None:
    """Map one MT-32 event to General MIDI; None drops it."""
    if ev.kind == "program":
        return ro.Event(ev.tick, ev.kind, ev.channel, mt32.MT32_TO_GM[ev.a])
    if ev.kind == "cc" and ev.a == 10:
        return ro.Event(ev.tick, ev.kind, ev.channel, 10, mt32.flip_pan(ev.b))
    if (ev.kind in ("note_on", "note_off") and ev.channel == mt32.RHYTHM_CHANNEL
            and ev.a not in mt32.MT32_RHYTHM_KEYS):
        return None  # silent on a stock MT-32
    return ev


def _gm_channel_defaults(ch: int) -> list[ro.Event]:
    """MT-32 power-on state expressed for a GM device, at tick 0."""
    evs = [ro.Event(0, "cc", ch, 91, mt32.GM_DEFAULT_REVERB)]
    if 1 <= ch <= 8:
        evs.append(ro.Event(0, "cc", ch, 10, mt32.flip_pan(mt32.MT32_DEFAULT_PANNING[ch - 1])))
    return evs


def _to_messages(events: list[ro.Event], end_tick: int, name: str) -> mido.MidiTrack:
    trk = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    last = 0
    for ev in events:
        dt = ev.tick - last
        last = ev.tick
        if ev.kind == "note_on":
            msg = mido.Message("note_on", channel=ev.channel, note=ev.a, velocity=ev.b, time=dt)
        elif ev.kind == "note_off":
            msg = mido.Message("note_off", channel=ev.channel, note=ev.a, velocity=ev.b, time=dt)
        elif ev.kind == "cc":
            msg = mido.Message("control_change", channel=ev.channel, control=ev.a, value=ev.b, time=dt)
        elif ev.kind == "program":
            msg = mido.Message("program_change", channel=ev.channel, program=ev.a, time=dt)
        else:
            raise ValueError(ev.kind)
        trk.append(msg)
    trk.append(mido.MetaMessage("end_of_track", time=end_tick - last))
    return trk


def build_midi(song: Song, gm: bool) -> mido.MidiFile:
    t = song.track
    mf = mido.MidiFile(type=1, ticks_per_beat=ro.PPQN)

    # Conductor: name, tempo, GM reset, loop points, RO sync markers.
    cond = mido.MidiTrack([
        mido.MetaMessage("track_name", name=song.title, time=0),
        mido.MetaMessage("set_tempo", tempo=ro.TEMPO_US, time=0),
    ])
    if gm:
        cond.append(mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01], time=0))  # GM System On
    meta = []
    if t.loops:
        meta.append((0, "loopStart"))
    meta += [(ev.tick, f"A{ev.channel:X}") for ev in t.events if ev.kind == "marker"]
    if t.loops:
        meta.append((t.end_tick, "loopEnd"))
    last = 0
    for tick, text in meta:
        cond.append(mido.MetaMessage("marker", text=text, time=tick - last))
        last = tick
    cond.append(mido.MetaMessage("end_of_track", time=t.end_tick - last))
    mf.tracks.append(cond)

    by_ch: dict[int, list[ro.Event]] = defaultdict(list)
    for ev in t.events:
        if ev.kind != "marker":
            by_ch[ev.channel].append(ev)
    for ch in sorted(by_ch):
        evs = by_ch[ch]
        if gm:
            evs = _gm_channel_defaults(ch) + [e for e in map(_gm_transform, evs) if e]
        progs = [e.a for e in evs if e.kind == "program"]
        if ch == mt32.RHYTHM_CHANNEL:
            label = "Rhythm"
        elif progs:
            label = mt32.GM_NAMES[progs[0]] if gm else mt32.MT32_PRESET_NAMES[progs[0]]
        else:
            label = ""
        mf.tracks.append(_to_messages(evs, t.end_tick, f"Ch {ch + 1} {label}".strip()))
    return mf


# ---------------------------------------------------------------------------
# Patch notes

def _channel_stats(events: list[ro.Event]) -> dict[int, dict]:
    stats: dict[int, dict] = {}
    active: dict[int, set[int]] = defaultdict(set)
    for ev in events:
        if ev.kind == "marker":
            continue
        s = stats.setdefault(ev.channel, {
            "programs": [], "notes": 0, "keys": defaultdict(int), "poly": 0,
            "volume": None, "pan": None, "controllers": set(),
        })
        if ev.kind == "program":
            if ev.a not in s["programs"]:
                s["programs"].append(ev.a)
        elif ev.kind == "note_on":
            s["notes"] += 1
            s["keys"][ev.a] += 1
            active[ev.channel].add(ev.a)
            s["poly"] = max(s["poly"], len(active[ev.channel]))
        elif ev.kind == "note_off":
            active[ev.channel].discard(ev.a)
        elif ev.kind == "cc":
            s["controllers"].add(ev.a)
            if ev.a == 7 and s["volume"] is None:
                s["volume"] = ev.b
            if ev.a == 10 and s["pan"] is None:
                s["pan"] = ev.b
    return stats


def _total_polyphony(events: list[ro.Event]) -> int:
    active: set[tuple[int, int]] = set()
    peak = 0
    for ev in events:
        if ev.kind == "note_on":
            active.add((ev.channel, ev.a))
            peak = max(peak, len(active))
        elif ev.kind == "note_off":
            active.discard((ev.channel, ev.a))
    return peak


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def patch_notes(song: Song) -> str:
    t = song.track
    seconds = t.end_tick / ro.PPQN * ro.TEMPO_US / 1e6
    out = [
        f"# Patch notes for {song.title}",
        "# Generated by migs.extract_music. Channel numbers are 1-based MIDI channels.",
        "# Programs are 0-based. MT-32 programs are preset timbres (see docs/mt32-to-gm.md).",
        "",
        "[song]",
        f"sound_id = {song.sound_id}",
        f"name = {_toml_str(song.name)}",
        f"duplicates = {song.duplicates}",
        f"source = {_toml_str(f'DISK{song.resource_disk:02d}.LEC room {song.resource_room} offset {song.resource_offset}')}",
        f"ro_bytes = {song.size}",
        f"ticks_per_beat = {ro.PPQN}",
        f"tempo_us_per_beat = {ro.TEMPO_US}",
        f"length_ticks = {t.end_tick}",
        f"length_seconds = {seconds:.1f}",
        f"loops = {str(t.loops).lower()}",
        f"terminated = {str(t.terminated).lower()}",
        f"markers = {sum(1 for e in t.events if e.kind == 'marker')}",
        f"max_polyphony = {_total_polyphony(t.events)}",
    ]
    for ch, s in sorted(_channel_stats(t.events).items()):
        keys = sorted(s["keys"])
        out += ["", "[[channel]]", f"channel = {ch + 1}"]
        if ch == mt32.RHYTHM_CHANNEL:
            out.append('role = "rhythm"')
        else:
            out.append('role = "melodic"' if s["notes"] else 'role = "unused"')
            if s["programs"]:
                p = s["programs"]
                out += [
                    f"mt32_program = {p[0]}",
                    f"mt32_name = {_toml_str(mt32.MT32_PRESET_NAMES[p[0]])}",
                    f"gm_program = {mt32.MT32_TO_GM[p[0]]}",
                    f"gm_name = {_toml_str(mt32.GM_NAMES[mt32.MT32_TO_GM[p[0]]])}",
                ]
                if len(p) > 1:
                    out.append(f"program_changes = {p}")
        out += [f"notes = {s['notes']}", f"max_polyphony = {s['poly']}"]
        if keys:
            out.append(f"key_range = [{keys[0]}, {keys[-1]}]")
        if s["volume"] is not None:
            out.append(f"initial_volume = {s['volume']}")
        if s["pan"] is not None:
            out.append(f"initial_pan_mt32 = {s['pan']}")
        if s["controllers"]:
            out.append(f"controllers = {sorted(s['controllers'])}")
        if ch == mt32.RHYTHM_CHANNEL and keys:
            out.append("[channel.rhythm_keys]  # key = [hits, GM name]; unmapped keys are dropped in GM")
            for k in keys:
                gm_name = mt32.GM_DRUM_NAMES.get(k, "?") if k in mt32.MT32_RHYTHM_KEYS else "UNMAPPED (silent on MT-32)"
                out.append(f"{k} = [{s['keys'][k]}, {_toml_str(gm_name)}]")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------

def load_songs(game: Game, names: dict[str, str], warn=None) -> list[Song]:
    by_hash: dict[str, Song] = {}
    unresolved = []
    for sid, (room, _) in enumerate(game.index.sounds):
        if room == 0:
            continue
        try:
            res = game.sound(sid)
        except ValueError:
            # The MI1 VGA index has stale entries that point mid-block (sounds 72-97);
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
            size=len(res.payload) + 6,
        )
    if unresolved and warn:
        warn(f"skipped {len(unresolved)} index entries that do not point at a sound block: {unresolved}")
    return sorted(by_hash.values(), key=lambda s: s.sound_id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir", type=Path, help="SCUMM v4 game directory (000.LFL, DISKnn.LEC)")
    ap.add_argument("out_dir", type=Path, help="output directory (e.g. music)")
    ap.add_argument("--names", type=Path, help="song names TOML (default: <out_dir>/song_names.toml)")
    args = ap.parse_args(argv)

    names_path = args.names or args.out_dir / "song_names.toml"
    names: dict[str, str] = {}
    if names_path.exists():
        names = {str(k): v for k, v in tomllib.loads(names_path.read_text()).get("names", {}).items()}

    songs = load_songs(Game(args.game_dir), names, warn=lambda m: print("warning:", m, file=sys.stderr))
    if not songs:
        print("no RO (MT-32) sounds found", file=sys.stderr)
        return 1

    dirs = {k: args.out_dir / p for k, p in
            (("mt32", "midi/mt32"), ("gm", "midi/gm"), ("patches", "patches"))}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("[0-9][0-9][0-9]*.*"):   # drop stale outputs (e.g. after renames)
            old.unlink()

    print(f"{'id':>4} {'dups':<12} {'len':>7} {'loop':<4} {'poly':>4}  file")
    for s in songs:
        build_midi(s, gm=False).save(dirs["mt32"] / f"{s.stem}.mid")
        build_midi(s, gm=True).save(dirs["gm"] / f"{s.stem}.mid")
        (dirs["patches"] / f"{s.stem}.toml").write_text(patch_notes(s))
        secs = s.track.end_tick / ro.PPQN * ro.TEMPO_US / 1e6
        print(f"{s.sound_id:>4} {','.join(map(str, s.duplicates)) or '-':<12} {secs:6.1f}s "
              f"{'yes' if s.track.loops else 'no':<4} {_total_polyphony(s.track.events):>4}  {s.stem}")
    print(f"{len(songs)} songs → {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
