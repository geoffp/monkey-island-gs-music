# SPDX-License-Identifier: GPL-3.0-or-later
"""MI1 MT-32 MIDI -> multi-channel ProTracker MOD, built to NinjaTrackerPlus' constraints.

The MOD is the primary artefact: it renders on the desktop (ffmpeg/libopenmpt) and converts
to .NTP for the IIGS. See docs/ninjatrackerplus.md. This module is the build: configuration
(config/ntp.toml: defaults < sample-set mode < trial variant < per-song overrides), the sample
set for each song (ntpsamples: Munt renders packed as NTP packs DOC RAM), the arrangement
(ntpsong: tracks, timing, levels) and the MOD file (modfile).

Sample sets: "per-song" gives each song only the sounds it plays and spends its free DOC RAM
on longer loops; "shared" builds one set for every song, byte-identical in each file, so a
game can keep it resident (like the MIDI Synth bank).
"""

from __future__ import annotations

import argparse
import math
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .modfile import mod_file
from .munt import Munt
from .ntpsamples import (ModSample, build_samples, cc7_curve, loop_problems, loop_summary, ntp_doc_pages,
                         period_tables, plan_loops)
from .ntpsong import RHYTHM, Note, convert_song, ntp_oscillators, plan_tracks, read_notes, sample_for

SAMPLE_SETS = ("per-song", "shared")


def ntp_spec(spec: dict, conf: dict, mode: str | None = None) -> dict:
    """The MI1 bank spec plus config/ntp.toml's NTP-only `[[melodic]]` sounds. An extra sound takes
    its `covers` away from the shared bank's merges (NTP has DOC RAM the 16-slot MIDI Synth
    bank doesn't), e.g. Pipe Org 2, which MI1.BNK plays with Elec Org 2's sample."""
    extra = [m for m in conf.get("melodic", []) if mode is None or mode in m.get("modes", [mode])]
    taken = {p for m in extra for p in m["covers"]}
    melodic = [{**m, "covers": [p for p in m["covers"] if p not in taken]} for m in spec["melodic"]]
    return {**spec, "melodic": [m for m in melodic if m["covers"]] + list(extra)}


def melodic_entry(spec: dict, prog: int, key: int) -> dict | None:
    """The spec's melodic entry that plays (prog, key), honouring `keys` splits."""
    hits = [m for m in spec["melodic"] if prog in m["covers"]]
    split = [m for m in hits if "keys" in m]
    inside = [m for m in split if m["keys"][0] <= key <= m["keys"][1]]
    if inside:
        return inside[0]
    whole = [m for m in hits if "keys" not in m]
    if whole:
        return whole[0]
    return min(split, key=lambda m: max(m["keys"][0] - key, key - m["keys"][1], 0)) if split else None


def with_attack_bytes(spec: dict, cfg: dict) -> dict:
    """`spec` with the mode's per-sound attack lengths (`ntp_attack_bytes`, by name) applied."""
    over = cfg.get("ntp_attack_bytes", {})
    return {**spec, "melodic": [{**m, "ntp_attack_bytes": over[m["name"]]} if m["name"] in over else m
                                for m in spec["melodic"]]}


def sample_set(spec: dict, cfg: dict, songs: list[tuple[list[Note], dict]], last_page: int, tables,
               munt: Munt) -> tuple[list[ModSample], dict, dict]:
    """(samples, programs, drums) for the sounds these songs play, with loop sizes planned into
    the DOC RAM they leave free (pages up to `last_page`). One song: a per-song set; all songs:
    the shared set."""
    used, ranges, keys = {}, {}, set()
    usage_notes = []
    for notes, chan_prog in songs:
        for n in notes:
            if n.channel == RHYTHM:
                keys.add(n.key)
                continue
            m = melodic_entry(spec, chan_prog.get(n.channel, -1), n.key)
            if m is None:
                continue
            used[m["slot"]] = m
            lo, hi = ranges.get(m["slot"], (n.key, n.key))
            ranges[m["slot"]] = (min(lo, n.key), max(hi, n.key))
            usage_notes.append((chan_prog.get(n.channel, -1), n))
    song_spec = {**spec, "melodic": [m for m in spec["melodic"] if m["slot"] in used],
                 "drum": [d for d in spec["drum"] if keys & set(d["covers"])]}
    build = lambda sp, c=cfg: build_samples(sp, c, ranges, munt, tables, log=lambda *a: None)
    # Loops are planned with every melodic sound looped; one-shots are decided afterwards, so a
    # one-shot never takes more than its attack + loop and what it saves stays free (reserve)
    # instead of growing some other loop into a short raw loop.
    samples, programs, drums = build(song_spec, {**cfg, "one_shots": False})
    usage: dict[int, float] = {}
    for prog, n in usage_notes:
        si = sample_for(programs, prog, n.key)
        if si is not None:
            usage[si] = usage.get(si, 0.0) + (n.off - n.t)
    sizes = plan_loops(samples, usage, last_page, cfg.get("max_loop_bytes", 16384))
    if cfg.get("one_shots", True) or any(sizes[i] != samples[i].loop_len for i in sizes):
        by_name = {samples[i].name: sz for i, sz in sizes.items()}
        song_spec = {**song_spec, "melodic": [{**m, "ntp_loop_bytes": by_name.get(m["name"], cfg["loop_bytes"])}
                                              for m in song_spec["melodic"]]}
        samples, programs, drums = build(song_spec)
    return samples, programs, drums


def resolve_mode(conf: dict, variant: str) -> tuple[str, dict]:
    """(sample-set mode, settings) for a build variant: per-song, shared, or a [modes.<name>]
    trial with a `base` mode. Settings layer as defaults < [modes.<base>] < [modes.<variant>];
    a trial's `ntp_attack_bytes` adds to its base's instead of replacing them."""
    modes = conf.get("modes", {})
    mode = variant if variant in SAMPLE_SETS else modes.get(variant, {}).get("base")
    if mode not in SAMPLE_SETS:
        raise SystemExit(f"samples must be per-song, shared or a [modes.<name>] with base = per-song/shared, "
                         f"not {variant!r}")
    cfg = {**conf["defaults"], **modes.get(mode, {}), **(modes.get(variant, {}) if variant != mode else {})}
    if variant != mode and "ntp_attack_bytes" in modes.get(variant, {}):
        cfg["ntp_attack_bytes"] = {**modes.get(mode, {}).get("ntp_attack_bytes", {}), **modes[variant]["ntp_attack_bytes"]}
    return mode, cfg


@dataclass
class _Song:
    path: Path
    notes: list[Note]
    programs: dict[int, int]

    @property
    def sid(self) -> str:
        return self.path.stem.split("-")[0]


def _shared_levels(songs: list[_Song], shared, cfg: dict, conf: dict, cc7, out_dir: Path) -> dict:
    """The song settings a resident bank needs, and levels.toml for the game.

    Every shared .NTP must carry byte-identical instruments and samples, so a game can load
    them once and then only each song's header + patterns. So each sample's default volume
    is its median note-on volume over all songs (also consistent levels between songs). Each
    song is still normalised on its own (loudest moment = volume 64): a common reference left
    quiet songs at MOD volume ~4, where one step is ~2.5 dB (choppy). The MT-32's song-to-song
    balance goes to levels.toml for the game (NTPsetplayvolume)."""
    refs, vols = {}, {}
    for song in songs:
        _, _, r = convert_song(song.path, *shared, cfg, {**cfg, **conf.get("songs", {}).get(song.sid, {})}, cc7)
        refs[song.sid] = r.level_ref
        for si, v in r.sample_volumes.items():
            vols.setdefault(si, []).extend(v)
    top = max(refs.values())
    (out_dir / "levels.toml").write_text(
        "# Song loudness relative to the loudest (MT-32 balance). Each shared .NTP is normalised\n"
        "# to its own loudest moment; a game restores the balance with NTPsetplayvolume.\n"
        + "".join(f'MI{sid} = {{ gain_db = {20 * math.log10(ref / top):.2f}, play_volume_scale = {ref / top:.3f} }}\n'
                  for sid, ref in refs.items()))
    return {"default_volumes": {si: int(np.median(v)) for si, v in vols.items()}}


def _print_reports(reports: list) -> None:
    print(f"{'song':<6} {'tempo':>5} {'spd':>3} {'row ms':>6} {'pat':>3} {'trk':>3} {'osc':>3} {'notes':>5} "
          f"{'drop':>4} {'steal':>5} {'tchg':>4} {'delay':>5} {'v:cell':>6} {'v:prev':>6} {'v:dflt':>6} "
          f"{'err ms':>6} {'halt':>5} {'st.vol':>6} {'plan':>4} {'snap':>4}")
    for sid, rep in reports:
        plan = ("-" if rep.melodic_cap is None else str(rep.melodic_cap)) + ("f" if rep.borrow == "first" else "")
        print(f"{sid:<6} {rep.tempo:5d} {rep.speed:3d} {rep.row_ms:6.1f} {rep.patterns:3d} {rep.tracks_used:3d} "
              f"{rep.oscillators:3d} {rep.notes:5d} {rep.dropped:4d} {rep.stolen:5d} {rep.tempo_changes:4d} "
              f"{rep.delayed:5d} {rep.vel_in_cell:6d} {rep.vel_preceding:6d} {rep.vel_default:6d} "
              f"{rep.timing_err_ms:6.1f} {rep.halted:5d} {max(rep.stolen_levels, default=0):6d} {plan:>4} "
              f"{rep.vel_snapped:4d}")
    for sid, rep in reports:
        if rep.dropped_by:
            print(f"MI{sid} dropped (no oscillator): "
                  + ", ".join(f"{k} {v}" for k, v in sorted(rep.dropped_by.items(), key=lambda kv: -kv[1])))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="MT-32 MIDI files (build/music/midi/mt32)")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--config", type=Path, default=Path("config/ntp.toml"))
    ap.add_argument("--spec", type=Path, default=Path("config/mt32bank.toml"))
    ap.add_argument("--make-tables", type=Path, default=Path("vendor/ntp/src/make_tables.php"))
    ap.add_argument("--smf2wav", type=Path, default=Path("vendor/munt/bin/mt32emu-smf2wav"))
    ap.add_argument("--roms", type=Path, default=Path("original files/mt-32 roms"))
    ap.add_argument("--cache", type=Path, default=Path("build/cache/mt32samples"))
    ap.add_argument("--samples", help="build variant: per-song, shared, or a [modes.<name>] with a `base` "
                                      "(e.g. shared-avg); default config/ntp.toml `samples`")
    args = ap.parse_args(argv)
    conf = tomllib.loads(args.config.read_text())
    mode, cfg = resolve_mode(conf, args.samples or conf["defaults"].get("samples", "per-song"))
    spec = with_attack_bytes(ntp_spec(tomllib.loads(args.spec.read_text()), conf, mode), cfg)
    tables = period_tables(args.make_tables)
    munt = Munt(args.smf2wav, args.roms, args.cache)
    cc7 = cc7_curve(munt)
    print("MT-32 CC7 curve: " + " ".join(f"{int(v)}:{g:.2f}" for v, g in zip(*cc7)))

    last_page = 255 - cfg.get("reserve_pages", 16)
    reserve = 255 - last_page
    args.out_dir.mkdir(parents=True, exist_ok=True)
    songs = [_Song(p, *read_notes(p)[:2]) for p in args.inputs]
    shared, bank = None, {}
    if mode == "shared":
        shared = sample_set(spec, cfg, [(s.notes, s.programs) for s in songs], last_page, tables, munt)
        if bad := loop_problems(shared[0], cfg):
            raise SystemExit("shared loops fail the tremolo/buzz check: " + "; ".join(bad))
        print(f"shared samples ({len(shared[0])}, DOC {ntp_doc_pages(shared[0], last_page)}/256 pages, "
              f"reserve {reserve}): {loop_summary(shared[0], cfg)}")
        bank = _shared_levels(songs, shared, cfg, conf, cc7, args.out_dir)
    reports = []
    for song in songs:
        song_cfg = {**cfg, **conf.get("songs", {}).get(song.sid, {}), **bank}
        if shared:
            samples, programs, drums = shared
        else:
            samples, programs, drums = sample_set(spec, cfg, [(song.notes, song.programs)], last_page, tables, munt)
            if bad := loop_problems(samples, cfg):
                raise SystemExit(f"MI{song.sid}: loops fail the tremolo/buzz check: " + "; ".join(bad))
            print(f"MI{song.sid}: {len(samples)} samples, DOC {ntp_doc_pages(samples, last_page)}/256 pages "
                  f"(reserve {reserve}); loops {loop_summary(samples, cfg)}")
        grid, patterns, rep = plan_tracks(song.path, samples, programs, drums, cfg, song_cfg, cc7)
        if ntp_oscillators([2 if k == "melodic" else 1 for k in rep.track_kinds]) is None:
            raise SystemExit(f"MI{song.sid}: NTP's converter can't assign oscillators to these tracks")
        (args.out_dir / f"MI{song.sid}.mod").write_bytes(mod_file(f"MI1 {song.sid}", samples, grid, patterns, tables))
        reports.append((song.sid, rep))
    _print_reports(reports)
    return 0


if __name__ == "__main__":
    sys.exit(main())
