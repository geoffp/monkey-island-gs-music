# SPDX-License-Identifier: GPL-3.0-or-later
"""A game's MT-32 MIDI -> multi-channel ProTracker MOD, built to NinjaTrackerPlus' constraints.

The MOD is the primary artefact: it renders on the desktop (ffmpeg/libopenmpt) and converts
to .NTP for the IIGS. See docs/ninjatrackerplus.md. This module is the build: configuration
(the game's ntp.toml: defaults < sample-set mode < trial variant < per-song overrides), the sample
set for each song (ntpsamples: Munt renders packed as NTP packs DOC RAM), the arrangement
(ntpsong: tracks, timing, levels) and the MOD file (modfile).

Sample sets: "per-song" gives each song only the sounds it plays and spends its free DOC RAM
on longer loops; "shared" builds one set for every song, byte-identical in each file, so a
game can keep it resident (like the MIDI Synth bank).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import math
import os
import sys
import tomllib
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio, docram, docsim2, games, pitchplan, sounds
from .docsim import NoteError, key_hz
from .imuse import read_timbres
from .modfile import mod_file
from .mt32 import GM_DRUM_NAMES, rhythm_keys
from .munt import SR, Munt
from .ntpsamples import (MAX_SAMPLES, ModSample, best_tuning, build_samples, cached_render, cc7_curve, forget_renders, floor_time,
                         drum_steps, is_steady, loop_problems, loop_summary, ntp_block, ntp_doc_pages, period_tables,
                         build_melodic, plan_loops, plan_loops_valued)
from .ntpsong import RHYTHM, Note, convert_song, ntp_oscillators, plan_tracks, program_of, read_notes, sample_for

SAMPLE_SETS = ("per-song", "shared")


def ntp_spec(spec: dict, conf: dict, mode: str | None = None, variant: str | None = None) -> dict:
    """The bank spec (mt32sounds.toml) plus ntp.toml's NTP-only `[[melodic]]` sounds. An extra sound takes
    its `covers` away from the shared bank's merges (NTP has DOC RAM the 16-slot MIDI Synth
    bank doesn't), e.g. Pipe Org 2, which MI1.BNK plays with Elec Org 2's sample. A sound's
    `modes` names sample-set modes or build variants (a trial's own sound)."""
    extra = [m for m in conf.get("melodic", [])
             if mode is None or "modes" not in m or {mode, variant} & set(m["modes"])]
    taken = {p for m in extra for p in m["covers"]}
    melodic = [{**m, "covers": [p for p in m["covers"] if p not in taken]} for m in spec["melodic"]]
    return {**spec, "melodic": [m for m in melodic if m["covers"]] + list(extra)}


def unmerged(spec: dict) -> dict:
    """`spec` without its merges (mode option `merges = false`): each sound keeps only its own
    program or rhythm key. A melodic program it covered is left uncovered, so sounds.complete
    gives it a default sound (its preset, at its median key); a rhythm key it covered becomes a
    drum of its own, the same length, named by its GM name. The 16-slot MIDI Synth bank needs
    the merges; NTP's per-song sets may not (docs/ninjatrackerplus.md)."""
    melodic = [{**m, "covers": [m["program"]] if m["program"] in m["covers"] else m["covers"][:1]}
               for m in spec["melodic"]]
    names = {e["name"] for e in [*spec["melodic"], *spec["drum"]]}
    drum = []
    for d in spec["drum"]:
        drum.append({**d, "covers": [d["key"]]})
        for k in d["covers"]:
            if k != d["key"]:
                name = GM_DRUM_NAMES.get(k, f"Rhythm {k}")
                name = name if name not in names else f"{name} (key {k})"
                names.add(name)
                drum.append({**d, "name": name, "key": k, "covers": [k]})
    return {**spec, "melodic": melodic, "drum": drum}


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
        if cfg.get("pitch_measure", "docsim") == "docsim2":           # the whole note, as the ear hears it
            err = docsim2.NoteError(doc_key(munt, m["program"]), render, SR, munt.cache.parent / "docsim2.json")
        else:
            err = NoteError(doc_key(munt, m["program"]), render, munt.cache.parent / "docsim.json")
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
    zones become a key split (`keys`, like ntp.toml's)."""
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
    # sounds by their own names (unplanned): a sound too wide for one sample starts zoned already
    count, drum_bytes, lines = _value_budget(plans, samples, unplanned, cfg, last_page, exposure, steps)
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


def docsim2_scorer(munt: Munt, seconds: float = 3.0) -> docsim2.Scorer:
    """Scores built samples against Munt's notes held `seconds`, cached in
    build/cache/docsim2.json (keyed by what is played, like docsim.json)."""
    def render(prog, key):
        x = cached_render(munt, prog, key, seconds)
        return x[audio.onset(x):]
    return docsim2.Scorer(render, SR, seconds, munt.cache.parent / "docsim2.json", name=lambda p: doc_key(munt, p))


def _valued_loops(samples: list[ModSample], song_spec: dict, ranges: dict, usage: dict[int, float],
                  usage_notes: list[tuple[int, Note]], programs: dict, cfg: dict, last_page: int, tables, munt: Munt,
                  log) -> dict[int, int]:
    """Loop sizes by measured value (ntpsamples.plan_loops_valued): every raw size a steady
    sound allows is built as it would ship and scored with docsim2 against Munt, on up to 5
    of its keys (weighted by held note-seconds)."""
    hold = song_spec.get("hold_seconds", 2.0)
    melodic = song_spec["melodic"]                  # build_samples puts melodic samples first, in order
    weights: dict[int, dict[int, float]] = {}
    for prog, n in usage_notes:
        si = sample_for(programs, prog, n.key)
        if si is not None:
            w = weights.setdefault(si, {})
            w[n.key] = w.get(n.key, 0.0) + (n.off - n.t)
    scorer = docsim2_scorer(munt)
    built: dict[tuple[int, int], ModSample] = {}

    def score(i: int, z: int) -> float:
        m = melodic[i]
        if (i, z) not in built:
            built[(i, z)] = samples[i] if z == samples[i].loop_len else build_melodic(
                {**m, "ntp_loop_bytes": z}, {**cfg, "one_shots": False},
                ranges.get(m["slot"], (m["key"], m["key"])), munt, tables, hold)[0]
        return scorer.sample_db(built[(i, z)], m["program"], docsim2.pick_keys(weights.get(i, {m["key"]: 1.0})))

    sizes, lines = plan_loops_valued(samples, usage, last_page, cfg.get("max_loop_bytes", 16384), score,
                                     cfg.get("loop_min_db_per_page", 0.0))
    scorer.cache.save()
    for line in lines:
        log(line)
    return sizes


def drop_least_used(spec: dict, songs: list[tuple[list[Note], dict]], room: int, log=lambda *a: None) -> dict:
    """`spec` with its least-played sounds (fewest notes; ties: the later entry) left out until
    at most `room` remain: a MOD holds 31 samples, one of them the rest. Their notes go
    unplayed (MI2's sound 222 plays 37 sounds). Returns `spec` itself when everything fits."""
    entries = [("melodic", m) for m in spec["melodic"]] + [("drum", d) for d in spec["drum"]]
    if len(entries) <= room:
        return spec
    count = {id(e): 0 for _, e in entries}
    for notes, chan_prog in songs:
        for n in notes:
            if n.channel == RHYTHM:
                e = next((d for d in spec["drum"] if n.key in d["covers"]), None)
            else:
                e = melodic_entry(spec, program_of(n, chan_prog), n.key)
            if e is not None and id(e) in count:
                count[id(e)] += 1
    order = sorted(range(len(entries)), key=lambda i: (count[id(entries[i][1])], -i))
    gone = {id(entries[i][1]) for i in order[:len(entries) - room]}
    for i in order[:len(entries) - room]:
        log(f"  dropped {entries[i][1]['name']} ({count[id(entries[i][1])]} notes): "
            f"{len(entries)} sounds, room for {room}")
    return {**spec, "melodic": [m for m in spec["melodic"] if id(m) not in gone],
            "drum": [d for d in spec["drum"] if id(d) not in gone]}


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
            m = melodic_entry(spec, program_of(n, chan_prog), n.key)
            if m is None:
                continue
            used[m["slot"]] = m
            lo, hi = ranges.get(m["slot"], (n.key, n.key))
            ranges[m["slot"]] = (min(lo, n.key), max(hi, n.key))
            usage_notes.append((program_of(n, chan_prog), n))
    song_spec = {**spec, "melodic": [m for m in spec["melodic"] if m["slot"] in used],
                 "drum": [d for d in spec["drum"] if keys & set(d["covers"])]}
    build = lambda sp, c=cfg: build_samples(sp, c, ranges, munt, tables, log=lambda *a: None)
    every = song_spec
    all_plans = _pitch_plans(song_spec, usage_notes, cfg, tables, munt) if cfg.get("pitch_plan") else {}

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
    # Too many sounds for the MOD's 31 samples (one is the rest) or for DOC RAM at their
    # smallest: the least-played are dropped, one more each time the set still doesn't fit.
    room = MAX_SAMPLES - bool(cfg.get("halt_released_loops", True))
    while True:
        dropped: list[str] = []
        unplanned = drop_least_used(every, songs, room, dropped.append)
        names = {m["name"] for m in unplanned["melodic"]}
        plans = {k: v for k, v in all_plans.items() if k in names}
        song_spec = planned({name: 1 for name in plans}) if plans else unplanned
        samples, programs, drums = build(song_spec, {**cfg, "one_shots": False})
        try:
            check_fit(samples, cfg, last_page, log)
            break
        except ValueError:
            room = len(unplanned["melodic"]) + len(unplanned["drum"]) - 1
            if room < 1:
                raise
    for line in dropped:
        log(line)
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
        count = _extra_zones(plans, samples, unplanned, cfg, last_page)   # by name: see _budget
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
    if cfg.get("loop_budget", "usage") == "docsim2":
        sizes = _valued_loops(samples, song_spec, ranges, usage, usage_notes, programs, cfg, last_page, tables,
                              munt, log)
    else:
        sizes = plan_loops(samples, usage, last_page, cfg.get("max_loop_bytes", 16384))
    if cfg.get("one_shots", True) or any(sizes[i] != samples[i].loop_len for i in sizes):
        by_name = {samples[i].name: sz for i, sz in sizes.items()}
        song_spec = {**song_spec, "melodic": [{**m, "ntp_loop_bytes": by_name.get(m["name"], cfg["loop_bytes"])}
                                              for m in song_spec["melodic"]]}
        samples, programs, drums = build(song_spec)
    return samples, programs, drums


def check_fit(samples: list[ModSample], cfg: dict, last_page: int, log=lambda *a: None) -> int:
    """Pages a sample set takes at its smallest (before extra zones and loop growth), checked
    against `last_page`. `ntp_attack_bytes` (by sound name, e.g. [modes.shared]) is a decision to
    shorten those attacks so the set fits: the build fails, naming the pages missing and the sounds
    still at full length, when the cuts are not enough, and says so when a cut is no longer needed
    (the set fits with that attack at full length)."""
    def blocks(full: str | None = None) -> list[int]:
        out = []
        for s in samples:
            b = s.ntp_blocks()
            if s.name == full and s.loop_start:
                b = [ntp_block(cfg["attack_bytes"]), *b[1:]]
            out += b
        return out

    def fits(b: list[int]) -> bool:
        try:
            docram.allocate([docram.Wave(f"b{i}", bytes(n)) for i, n in enumerate(b)], 0, last_page)
            return True
        except ValueError:
            return False

    need = sum(blocks()) // docram.PAGE
    cuts = [n for n in cfg.get("ntp_attack_bytes", {}) if any(s.name == n for s in samples)]
    if not fits(blocks()):
        cut = ntp_block(min(cfg.get("ntp_attack_bytes", {}).values(), default=cfg["attack_bytes"]))
        full = [s.name for s in samples if s.kind == "melodic" and s.loop_start
                and s.name not in cfg.get("ntp_attack_bytes", {}) and ntp_block(s.loop_start) > min(cut, 1024)]
        raise ValueError(f"the sounds need {need} DOC pages at their smallest, {last_page + 1} are free "
                         f"(reserve_pages {255 - last_page}): shorten more attacks (`ntp_attack_bytes`; at full "
                         f"length: {', '.join(full) or 'none'}) or merge sounds (`covers`)")
    for n in cuts:
        if fits(blocks(full=n)):
            log(f"  attack cut {n} isn't needed to fit: the set fits with its {cfg['attack_bytes']} B attack "
                "(the pages it frees go to zones, loops and drums)")
    if cuts:
        log(f"  smallest: {need} of {last_page + 1} DOC pages with the attack cuts ({', '.join(cuts)})")
    return need


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


def _shared_levels(songs: list[_Song], shared, cfg: dict, conf: dict, cc7, out_dir: Path,
                   game: games.GameInfo) -> dict:
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
        + "".join(f'{game.prefix}{sid} = {{ gain_db = {20 * math.log10(ref / top):.2f}, play_volume_scale = {ref / top:.3f} }}\n'
                  for sid, ref in refs.items()))
    return {"default_volumes": {si: int(np.median(v)) for si, v in vols.items()}}


def _print_reports(reports: list, prefix: str) -> None:
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
    if any(rep.snap_near for _, rep in reports):
        print("delay_snap_ms: " + ", ".join(f"{prefix}{sid} {rep.snap_near} moved" for sid, rep in reports
                                            if rep.snap_near))
    ms = [x for _, rep in reports for x in rep.default_row_ms]
    if ms:
        ms.sort()
        print(f"v:dflt notes ({len(ms)}), ms from their nearest row: " + ", ".join(
            f"{q}% {ms[min(len(ms) - 1, len(ms) * q // 100)]:.0f}" for q in (10, 25, 50, 75, 90))
            + f"; within 10/15/20/25 ms: " + "/".join(str(sum(x <= c for x in ms)) for c in (10, 15, 20, 25)))
    if any(rep.ghost_halts or rep.ghost_moves for _, rep in reports):
        print("halt_before_delayed: " + ", ".join(f"{prefix}{sid} {rep.ghost_halts} halts {rep.ghost_moves} moved"
                                                for sid, rep in reports if rep.ghost_halts or rep.ghost_moves))
    loops = [(sid, rep) for sid, rep in reports if rep.loop_rows]
    if loops:
        def note(rep) -> str:
            extra = [f"{rep.loop_wrapped} release cells into the next pass"] if rep.loop_wrapped else []
            extra += [f"{rep.loop_late} late notes on the last row"] if rep.loop_late else []
            extra += [f"{rep.loop_cut} notes past the end dropped"] if rep.loop_cut else []
            extra += ["start tempo not restored"] if rep.loop_tempo_kept else []
            extra += ["jump over a volume step"] if rep.loop_jump_over_volume else []
            fit = f", last {rep.loop_fit_rows} at tempo {rep.loop_fit_tempo}" if rep.loop_fit_rows else ""
            return f"{rep.loop_rows} rows ({rep.loop_err_ms:+.1f} ms{fit})" + "".join(f", {e}" for e in extra)
        print("loops (like the game): " + "; ".join(f"{prefix}{sid} {note(rep)}" for sid, rep in loops))
        print("play once: " + (", ".join(f"{prefix}{sid}" for sid, rep in reports if not rep.loop_rows) or "none"))
    for sid, rep in reports:
        if rep.dropped_by:
            print(f"{prefix}{sid} dropped (no oscillator): "
                  + ", ".join(f"{k} {v}" for k, v in sorted(rep.dropped_by.items(), key=lambda kv: -kv[1])))


def _build_song(job) -> tuple[str, object, str, str | None]:
    """One song's samples (unless shared), track plan and MOD file: (sid, report, its log,
    error or None). Runs in a worker process; the log is printed by the caller, in order. An
    exception comes back as the error (with its traceback), so the song's log is kept."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rep, err = _build_song_quiet(job)
        except Exception:
            rep, err = None, f"{job[-1].prefix}{job[0].sid}: build failed\n{traceback.format_exc()}"
        finally:
            forget_renders()                    # a worker builds many songs: don't keep them all
    return job[0].sid, rep, buf.getvalue(), err


def _gather(results) -> tuple[list[tuple[str, object]], list[tuple[str, str]]]:
    """_build_song results, in song order: prints each song's log (and a failed song's error),
    and returns (sid, report) for the songs built and (sid, last error line) for the failed.
    One song's failure doesn't stop the others."""
    reports, failed = [], []
    for sid, rep, log, err in results:
        print(log, end="")
        if err:
            print(err)
            failed.append((sid, err.strip().splitlines()[-1]))
        else:
            reports.append((sid, rep))
    return reports, failed


def _build_song_quiet(job) -> tuple[object, str | None]:
    """_build_song's work: (report, error or None)."""
    song, song_cfg, spec, cfg, shared, last_page, tables, munt, cc7, out_dir, game = job
    name = f"{game.prefix}{song.sid}"
    reserve = 255 - last_page
    if shared:
        samples, programs, drums = shared
    else:
        samples, programs, drums = sample_set(spec, cfg, [(song.notes, song.programs)], last_page, tables, munt,
                                              log=lambda line: print(f"{name}{line}"))
        if bad := loop_problems(samples, cfg):
            return None, f"{name}: loops fail the tremolo/buzz check: " + "; ".join(bad)
        print(f"{name}: {len(samples)} samples, DOC {ntp_doc_pages(samples, last_page)}/256 pages "
              f"(reserve {reserve}); loops {loop_summary(samples, cfg)}")
    grid, patterns, rep = plan_tracks(song.path, samples, programs, drums, cfg, song_cfg, cc7)
    if ntp_oscillators([2 if k == "melodic" else 1 for k in rep.track_kinds]) is None:
        return None, f"{name}: NTP's converter can't assign oscillators to these tracks"
    (out_dir / game.song_file(song.sid, "mod")).write_bytes(
        mod_file(game.song_title(song.sid), samples, grid, patterns, tables))
    return rep, None


def doc_key(munt: Munt, program: int) -> int | str:
    """The DOC measure cache's name for a sound: its preset number (as it always was), or
    what the MT-32 plays (munt.sound_tag) for a custom timbre or a game with a driver set-up."""
    return program if program < 128 and not munt.setup else munt.sound_tag(program)


def _same_id(sid: str, want: str) -> bool:
    """A song's id (its MIDI file's stem: 154, Atlantis 009) against one given on the command
    line, with or without leading zeros."""
    return sid == want or (sid.isdigit() and want.isdigit() and int(sid) == int(want))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    games.add_argument(ap)
    ap.add_argument("inputs", nargs="+", type=Path, help="MT-32 MIDI files (build/<game>/extracted/midi/mt32)")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--config", type=Path, help="NTP settings (default the game's ntp.toml)")
    ap.add_argument("--spec", type=Path, help="MT-32 sounds (default the game's mt32sounds.toml)")
    ap.add_argument("--make-tables", type=Path, default=Path("vendor/ntp/src/make_tables.php"))
    ap.add_argument("--smf2wav", type=Path, default=Path("vendor/munt/bin/mt32emu-smf2wav"))
    ap.add_argument("--roms", type=Path, default=Path("original files/mt-32 roms"))
    ap.add_argument("--cache", type=Path, default=Path("build/cache/mt32samples"))
    ap.add_argument("-j", "--jobs", type=int, default=0,
                    help="songs built at once (worker processes); default half the CPUs")
    ap.add_argument("--only", nargs="+", metavar="ID",
                    help="build only these songs (sound ids, e.g. 154 or 9). Every input is still read, so "
                         "default sounds and a shared set come out as in a full build (just testdisk)")
    ap.add_argument("--samples", help="build variant: per-song, shared, or a [modes.<name>] with a `base` "
                                      "(e.g. shared-avg); default ntp.toml's `samples`")
    args = ap.parse_args(argv)
    if missing := [str(p) for p in args.inputs if not p.is_file()]:
        raise SystemExit(f"ntpmod: no MIDI file {', '.join(missing)} (extract the songs first: just extract)")
    game = games.load(args.game)
    args.config = args.config or game.config("ntp.toml")
    args.spec = args.spec or game.config("mt32sounds.toml")
    conf = tomllib.loads(args.config.read_text())
    variant = args.samples or conf["defaults"].get("samples", "per-song")
    mode, cfg = resolve_mode(conf, variant)
    songs = []
    for p in args.inputs:
        try:
            songs.append(_Song(p, *read_notes(p)[:2]))
        except ValueError as e:                  # e.g. tempo changes: not built yet
            print(f"skipped {p.name}: {e}")
    # the songs decide which sounds; mt32sounds.toml (and ntp.toml's [[melodic]]) how
    timbres = read_timbres(game.build_dir / "extracted" / "timbres.toml")
    spec = ntp_spec(sounds.load(args.spec), conf, mode, variant)
    spec = with_attack_bytes(spec if cfg.get("merges", True) else unmerged(spec), cfg)
    spec = sounds.complete(spec, [(s.notes, s.programs) for s in songs], log=print,
                           timbre_names={p: tb.name for p, tb in timbres.items()},
                           rhythm_keys=rhythm_keys(game.mt32_init))
    tables = period_tables(args.make_tables)
    munt = Munt(args.smf2wav, args.roms, args.cache, tuple(game.mt32_init),
                tuple((p, tb.data) for p, tb in sorted(timbres.items())))
    cc7 = cc7_curve(munt)
    print("MT-32 CC7 curve: " + " ".join(f"{int(v)}:{g:.2f}" for v, g in zip(*cc7)))

    last_page = 255 - cfg.get("reserve_pages", 16)
    reserve = 255 - last_page
    args.out_dir.mkdir(parents=True, exist_ok=True)
    shared, bank = None, {}
    if mode == "shared":
        shared = sample_set(spec, cfg, [(s.notes, s.programs) for s in songs], last_page, tables, munt, log=print)
        if bad := loop_problems(shared[0], cfg):
            raise SystemExit("shared loops fail the tremolo/buzz check: " + "; ".join(bad))
        print(f"shared samples ({len(shared[0])}, DOC {ntp_doc_pages(shared[0], last_page)}/256 pages, "
              f"reserve {reserve}): {loop_summary(shared[0], cfg)}")
        bank = _shared_levels(songs, shared, cfg, conf, cc7, args.out_dir, game)
    built = songs
    if args.only:
        built = [s for s in songs if any(_same_id(s.sid, i) for i in args.only)]
        if missing := [i for i in args.only if not any(_same_id(s.sid, i) for s in songs)]:
            raise SystemExit(f"ntpmod: no song {', '.join(missing)} among the inputs")
    work = [(song, {**cfg, **conf.get("songs", {}).get(song.sid, {}), **bank}, spec, cfg, shared, last_page,
             tables, munt, cc7, args.out_dir, game) for song in built]
    # songs are independent once the samples are known (per-song: each makes its own): one worker
    # each, logs printed in song order
    cpus = getattr(os, "process_cpu_count", os.cpu_count)() or 2   # 3.13+: honours CPU affinity
    jobs = min(args.jobs or max(1, cpus // 2), len(work))
    with ProcessPoolExecutor(jobs) if jobs > 1 else contextlib.nullcontext() as pool:
        results = pool.map(_build_song, work) if pool else map(_build_song, work)
        reports, failed = _gather(results)
    _print_reports(reports, game.prefix)
    for sid, why in failed:
        print(f"FAILED {game.prefix}{sid}: {why} (no MOD; its log is above)")
    if failed:
        print(f"{len(failed)} of {len(work)} songs failed")
    return 0 if reports or not work else 1      # every song failed: the build did


if __name__ == "__main__":
    sys.exit(main())
