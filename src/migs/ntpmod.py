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

from . import audio, docram, pitchplan
from .docsim import NoteError, key_hz
from .modfile import mod_file
from .munt import Munt
from .munt import SR
from .ntpsamples import (MAX_SAMPLES, ModSample, best_tuning, build_samples, cached_render, cc7_curve, floor_time,
                         drum_steps, is_steady, loop_problems, loop_summary, ntp_block, ntp_doc_pages, period_tables,
                         plan_loops)
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


def key_weights(w: dict[int, float], cfg: dict) -> dict[int, float]:
    """What each played key counts for in the pitch plan. "weighted" (default): its note
    seconds, so samples gather where notes are held longest (MI153 Pipe Organ: key 52 from a
    sample at 74, two samples 2 semitones apart). "hybrid": a mix, `pitch_key_share` (0.5) of
    an equal share per played key plus the rest by note seconds, so every played key pulls
    on the zones but long notes still pull harder. Unplayed keys never count."""
    if cfg.get("pitch_zones", "weighted") != "hybrid":
        return w
    share, total = cfg.get("pitch_key_share", 0.5), sum(w.values())
    return {k: share / len(w) + (1 - share) * v / total for k, v in w.items()}


def _pitch_plans(spec: dict, usage_notes: list[tuple[int, Note]], cfg: dict, tables, munt: Munt
                 ) -> dict[str, tuple[list[pitchplan.Plan], float, float]]:
    """Per melodic entry (by name): (plans for 1..pitch_max_zones zones, note seconds, the error
    of the configured key at its usual rate, whether it is planned as a one-shot)."""
    weights: dict[str, dict[int, float]] = {}
    for prog, n in usage_notes:
        m = melodic_entry(spec, prog, n.key)
        if m is not None:
            w = weights.setdefault(m["name"], {})
            w[n.key] = w.get(n.key, 0.0) + (n.off - n.t)
    loop, min_rate = cfg["loop_bytes"], cfg["min_rate"]
    max_rate = cfg.get("pitch_max_rate", cfg["max_rate"])
    hold = spec.get("hold_seconds", 2.0)

    def base_note(k: int, r: float, whole: bool) -> int | None:
        try:
            return best_tuning(key_hz(k), tables, r * 0.97, r * 1.03, loop, k, k, k, whole_cycles=whole)[3]
        except ValueError:
            return None

    out = {}
    for m in spec["melodic"]:
        w = weights.get(m["name"])
        if not w:
            continue

        def render(key: int, prog=m["program"]) -> np.ndarray:
            x = cached_render(munt, prog, key, hold)
            return x[audio.onset(x):]

        def shot_rate(key: int, m=m) -> float | None:
            """The rate this sound would be a one-shot at, sampled at `key` (build_melodic's
            rule: decaying, and the whole sound to the 8-bit floor fits in one block no larger
            than attack + loop), else None."""
            if not cfg.get("one_shots", True):
                return None
            x = cached_render(munt, m["program"], key, hold)
            a = audio.onset(x)
            off = a + int(hold * SR)
            if is_steady(audio.rms_envelope(x), a, off, cfg.get("sustain_s", 0.4)):
                return None
            pages = (ntp_block(m.get("ntp_attack_bytes", cfg["attack_bytes"])) + ntp_block(loop)) // 256
            block = 1 << int(math.log2(pages * 256))
            r = min(max_rate, (block - 8) / max(floor_time(x, a, off, cfg.get("one_shot_floor_lsb", 1.5)), 1e-6))
            return r if r >= min_rate else None

        def rates(k: int, shots: bool) -> list[float]:
            if shots:                                    # one-shot: only where it stays one
                r = shot_rate(k)
                return [r] if r else []
            return pitchplan.loop_rates(k, loop, min_rate, max_rate)
        # A one-shot frees an oscillator per note (polyphony, which this measure doesn't see):
        # a sound that is one at its configured key stays one.
        shots = shot_rate(m["key"]) is not None
        err = NoteError(m["program"], render, munt.cache.parent / "docsim.json")
        cands = pitchplan.candidates(min(w), max(w), lambda k: rates(k, shots),
                                     lambda k, r: base_note(k, r, whole=not shots))
        now_rates = [shot_rate(m["key"])] if shots else pitchplan.loop_rates(m["key"], loop, min_rate, cfg["max_rate"])
        wp = key_weights(w, cfg)
        plans = pitchplan.plan(wp, cands, err, cfg.get("pitch_max_zones", 1))
        now = (sum(wt * err(m["key"], now_rates[0], k) for k, wt in wp.items()) / sum(wp.values())
               if now_rates else float("nan"))
        err.save()
        # the measure sees the first 0.6 s, not the loop the new key and rate get: move only
        # for a real gain (Panpipes in MI154: key 72 -> 74 for 0.0 dB, its raw loop 16384 ->
        # 8192 B and rougher)
        if plans and now_rates and now - plans[0].error_db < cfg.get("pitch_move_gain_db", 0.5):
            plans[0] = pitchplan.Plan((pitchplan.Zone(min(w), max(w), m["key"], now_rates[0], now),), now)
        if plans:
            out[m["name"]] = (plans, sum(w.values()), now, shots)
    return out


def _zone_entries(m: dict, p: pitchplan.Plan, shot: bool) -> list[dict]:
    """The spec entries that play entry m's keys under plan p: one per zone, each sampled at
    its key and rate (a one-shot's rate goes to one_shot_tuning: `ntp_shot_rate`). Several
    zones become a key split (`keys`, like config/ntp.toml's)."""
    rate = "ntp_shot_rate" if shot else "ntp_rate"
    if p.count == 1:
        z = p.zones[0]
        return [{**m, "key": z.key, rate: z.rate}]
    lo, hi = m.get("keys", (0, 127))
    return [{**m, "name": f"{m['name']} {z.key}", "slot": f"{m['slot']}/{z.key}", "key": z.key, rate: z.rate,
             "keys": list(r), "zone_keys": (z.lo, z.hi)}
            for z, r in zip(p.zones, pitchplan.zone_ranges(p.zones, lo, hi))]


def _extra_zones(plans: dict, samples: list[ModSample], spec: dict, cfg: dict, last_page: int) -> dict[str, int]:
    """Zones per entry name: 1, plus extra zones while each still gains `pitch_zone_gain_db` on
    its sound's mean error and fits (DOC pages up to last_page, the MOD's sample slots), the
    largest gain first. Each extra zone costs an attack + short loop."""
    count = {name: 1 for name in plans}
    blocks = [b for s in samples for b in s.ntp_blocks()]
    slots = MAX_SAMPLES - len(samples)
    entry = {m["name"]: m for m in spec["melodic"]}
    min_gain = cfg.get("pitch_zone_gain_db", 1.0)
    while slots > 0:
        best = None
        for name, (ps, *_) in plans.items():
            z = count[name]
            if z < len(ps):
                g = pitchplan.gain_db(ps[z - 1], ps[z])
                if g >= min_gain and (best is None or g > best[0]):
                    best = (g, name)
        if best is None:
            break
        name = best[1]
        m = entry[name]
        extra = [ntp_block(m.get("ntp_attack_bytes", cfg["attack_bytes"])), ntp_block(cfg["loop_bytes"])]
        try:
            docram.allocate([docram.Wave(f"b{i}", bytes(b)) for i, b in enumerate(sorted(blocks + extra, reverse=True))],
                            0, last_page)
        except ValueError:
            plans = {k: v for k, v in plans.items() if k != name}     # no room for this one: try the others
            continue
        blocks += extra
        slots -= 1
        count[name] += 1
    return count


def _budget(plans, samples, song_spec, unplanned, songs, usage_notes, cfg, last_page, tables, munt, log
            ) -> tuple[dict[str, int], dict[str, int]]:
    """Exposures and drum steps for _value_budget, which it runs; logs its choices."""
    cap = lambda d: min(d, 0.6)
    exposure: dict[str, float] = {}
    for prog, n in usage_notes:
        m = melodic_entry(unplanned, prog, n.key)
        if m is not None:
            exposure[m["name"]] = exposure.get(m["name"], 0.0) + cap(n.off - n.t)
    hits: dict[int, float] = {}
    for notes, _ in songs:
        for n in notes:
            if n.channel == RHYTHM:
                hits[n.key] = hits.get(n.key, 0.0) + 1
    steps = {}
    for d in song_spec["drum"]:
        st = drum_steps(d, cfg, munt, tables, hits)
        steps[d["name"]] = st
        exposure[d["name"]] = sum(hits.get(c, 0.0) for c in d["covers"]) * cap(st[0][2])
    count, drum_bytes, lines = _value_budget(plans, samples, song_spec, cfg, last_page, exposure, steps)
    for line in lines:
        log(line)
    return count, drum_bytes


def _value_budget(plans: dict, samples: list[ModSample], spec: dict, cfg: dict, last_page: int,
                  exposure: dict[str, float], steps: dict[str, list[tuple[int, float, float]]]
                  ) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """sample_budget = "value": extra key zones and bigger drum blocks compete for the same DOC
    pages (and zones for MOD slots), the best value per page first, while it is at least
    `upgrade_min_db_per_page`. Value = dB gained (the sound's mean docsim error) x the sound's
    share of the set's exposure (each note counted up to 0.6 s: the window both measures see).
    A drum may jump straight to any larger block (Tom gains 0.2 dB at 16 pages, 3.8 more at
    32). What stays free goes to loop growth, which the measure can't price. Returns (zones
    per planned sound, drum bytes per drum, log lines)."""
    count = {name: 1 for name in plans}
    level = {name: 0 for name in steps}
    entry = {m["name"]: m for m in spec["melodic"]}
    blocks = [b for s in samples if s.kind != "drum" for b in s.ntp_blocks()]
    drum_blocks = {name: st[0][0] for name, st in steps.items()}
    slots = MAX_SAMPLES - len(samples)
    total = sum(exposure.values()) or 1.0
    floor = cfg.get("upgrade_min_db_per_page", 0.01)
    zone_cost = lambda m: [ntp_block(m.get("ntp_attack_bytes", cfg["attack_bytes"])), ntp_block(cfg["loop_bytes"])]
    lines = []

    def fits(extra: list[int], drums: dict[str, int]) -> bool:
        bl = sorted(blocks + extra + list(drums.values()), reverse=True)
        try:
            docram.allocate([docram.Wave(f"b{i}", bytes(b)) for i, b in enumerate(bl)], 0, last_page)
            return True
        except ValueError:
            return False
    banned: set = set()
    while True:
        opts = []
        for name, (ps, *_) in plans.items():
            z = count[name]
            if z < len(ps) and slots > 0 and ("zone", name) not in banned:
                g = pitchplan.gain_db(ps[z - 1], ps[z])
                pages = sum(zone_cost(entry[name])) // 256
                opts.append((g * exposure.get(name, 0.0) / total / pages, "zone", name, z + 1, g, pages))
        for name, st in steps.items():
            i = level[name]
            for j in range(i + 1, len(st)):
                if ("drum", name, j) in banned:
                    continue
                g, pages = st[i][1] - st[j][1], (st[j][0] - st[i][0]) // 256
                opts.append((g * exposure.get(name, 0.0) / total / pages, "drum", name, j, g, pages))
        opts = [o for o in opts if o[0] >= floor]
        if not opts:
            break
        v, kind, name, to, g, pages = max(opts)
        if kind == "zone":
            extra = zone_cost(entry[name])
            if not fits(extra, drum_blocks):
                banned.add(("zone", name))
                continue
            blocks += extra
            slots -= 1
            count[name] = to
            lines.append(f"  budget zone {name:<13} -> {to} zones: {g:+.1f} dB, {pages} pages ({v:.3f}/page)")
        else:
            trial = {**drum_blocks, name: steps[name][to][0]}
            if not fits([], trial):
                banned.add(("drum", name, to))
                continue
            st = steps[name]
            lines.append(f"  budget drum {name:<13} {st[level[name]][0] // 256:3d} -> {st[to][0] // 256:3d} pages: "
                         f"{st[level[name]][1]:+.1f} -> {st[to][1]:+.1f} dB ({v:.3f}/page)")
            drum_blocks, level[name] = trial, to
    return count, drum_blocks, lines


def sample_set(spec: dict, cfg: dict, songs: list[tuple[list[Note], dict]], last_page: int, tables,
               munt: Munt, log=lambda *a: None) -> tuple[list[ModSample], dict, dict]:
    """(samples, programs, drums) for the sounds these songs play, with loop sizes planned into
    the DOC RAM they leave free (pages up to `last_page`). One song: a per-song set; all songs:
    the shared set. With `pitch_plan`, each sound's sampled key(s), zones and rate are chosen
    from the notes the songs play (pitchplan; extra zones before loop growth)."""
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
    plans = _pitch_plans(song_spec, usage_notes, cfg, tables, munt) if cfg.get("pitch_plan") else {}
    unplanned = song_spec

    def planned(count: dict[str, int]) -> dict:
        """song_spec with each planned sound replaced by its zones under its count-zone plan."""
        melodic = []
        for m in unplanned["melodic"]:
            if m["name"] not in plans:
                melodic.append(m)
                continue
            ps, _, _, shot = plans[m["name"]]
            for z in _zone_entries(m, ps[count[m["name"]] - 1], shot):
                if "zone_keys" in z:
                    ranges[z["slot"]] = z["zone_keys"]        # tune each zone for its own keys
                melodic.append(z)
        return {**unplanned, "melodic": melodic}

    # Loops are planned with every melodic sound looped; one-shots are decided afterwards, so a
    # one-shot never takes more than its attack + loop and what it saves stays free (reserve)
    # instead of growing some other loop into a short raw loop.
    if plans:
        song_spec = planned({name: 1 for name in plans})
    samples, programs, drums = build(song_spec, {**cfg, "one_shots": False})
    if cfg.get("sample_budget", "zones") == "value":
        count, drum_bytes = _budget(plans, samples, song_spec, unplanned, songs, usage_notes, cfg, last_page,
                                    tables, munt, log)
        song_spec = {**planned(count), "drum": [{**d, "ntp_bytes": drum_bytes[d["name"]]} if d["name"] in drum_bytes
                                                else d for d in song_spec["drum"]]}
        for name, (ps, secs, now, _) in plans.items():
            p = ps[count[name] - 1]
            log(f"  pitch {name:<13} {secs:6.1f} s of notes: {now:+5.1f} -> {p.error_db:+5.1f} dB  "
                f"{pitchplan.describe(p)}")
        samples, programs, drums = build(song_spec, {**cfg, "one_shots": False})
    elif plans:
        count = _extra_zones(plans, samples, song_spec, cfg, last_page)
        for name, (ps, secs, now, _) in plans.items():
            p = ps[count[name] - 1]
            log(f"  pitch {name:<13} {secs:6.1f} s of notes: {now:+5.1f} -> {p.error_db:+5.1f} dB  "
                f"{pitchplan.describe(p)}")
        if any(c > 1 for c in count.values()):
            song_spec = planned(count)
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
        shared = sample_set(spec, cfg, [(s.notes, s.programs) for s in songs], last_page, tables, munt, log=print)
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
            samples, programs, drums = sample_set(spec, cfg, [(song.notes, song.programs)], last_page, tables, munt,
                                                  log=lambda line, sid=song.sid: print(f"MI{sid}{line}"))
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
