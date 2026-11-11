# SPDX-License-Identifier: GPL-3.0-or-later
"""The MT-32 sounds a build samples: the songs decide which, the game's mt32sounds.toml decides how.

Every MT-32 program and rhythm key the songs play needs a sound. A `[[melodic]]` or `[[drum]]`
entry in mt32sounds.toml (or an NTP-only `[[melodic]]` in ntp.toml) is a decision about one:
which programs or keys share a sample (`covers`), its name, sampled key and sizes. Anything the
songs play that no entry covers gets a default sound here, so a new game builds from an empty
sound list, and a sound missing from the list is reported instead of its notes being dropped.

Defaults: a melodic sound is the program alone, named by its MT-32 preset, sampled at the median
key the songs play it at (the pitch planner may move it); a drum is the rhythm key alone, 2 KB.
Rhythm keys the MT-32 has no sound for are skipped (they are silent on the MT-32 too).
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path

from . import mt32
from .ntpsong import RHYTHM as RHYTHM_CHANNEL     # 0-based (MIDI channel 10)

DRUM_BYTES = 2048
BANK_SLOTS = range(16)            # MIDI Synth bank slots (instruments)
KIT_SLOTS = (9, 0)                # where default drums go: a drum kit holds up to KIT_ZONES sounds
KIT_ZONES = 8                     # midisynth.WAVELISTS
EXTRA_SLOT = 200                  # slots for defaults past the 16-slot MIDI Synth bank (NTP only)


def load(path: Path, bank: str = "") -> dict:
    """A game's mt32sounds.toml, which may hold no entries at all; `bank` names the MIDI Synth
    bank when the file doesn't."""
    spec = tomllib.loads(path.read_text()) if path.is_file() else {}
    return {"bank": bank, **spec, "melodic": spec.get("melodic", []), "drum": spec.get("drum", [])}


def played(songs: Iterable[tuple[list, dict]]) -> tuple[dict[int, list[int]], dict[int, int]]:
    """(MT-32 program -> keys played, one per note; rhythm key -> notes) over (notes, first
    program per channel) songs, as ntpsong.read_notes gives them."""
    programs: dict[int, list[int]] = {}
    rhythm: dict[int, int] = {}
    for notes, chan_prog in songs:
        for n in notes:
            if n.channel == RHYTHM_CHANNEL:
                rhythm[n.key] = rhythm.get(n.key, 0) + 1
            elif (p := chan_prog.get(n.channel, -1)) >= 0:
                programs.setdefault(p, []).append(n.key)
    return programs, rhythm


def _notes(n: int) -> str:
    return f"{n} note" + ("" if n == 1 else "s")


def _median(keys: list[int]) -> int:
    s = sorted(keys)
    return s[(len(s) - 1) // 2]


def complete(spec: dict, songs: Iterable[tuple[list, dict]],
             log: Callable[[str], None] = lambda line: None) -> dict:
    """`spec` plus a default entry for every program and MT-32 rhythm key the songs play that no
    entry covers. Returns `spec` itself when nothing is missing."""
    programs, rhythm = played(songs)
    covered = {p for m in spec["melodic"] for p in m["covers"]}
    covered_keys = {k for d in spec["drum"] for k in d["covers"]}
    melodic_slots = {m["slot"] for m in spec["melodic"]}
    kits: dict = {}
    for d in spec["drum"]:
        kits[d["slot"]] = kits.get(d["slot"], 0) + 1
    names = {e["name"] for e in [*spec["melodic"], *spec["drum"]]}
    missing_keys = [k for k in sorted(set(rhythm) - covered_keys) if k in mt32.MT32_RHYTHM_KEYS]
    for s in KIT_SLOTS:                   # a game with no drum entries: its default drums need a kit
        if missing_keys and not kits and s not in melodic_slots:
            kits[s] = 0
    # melodic defaults take bank slots no sound or kit uses, then slots past the bank
    free = [s for s in range(1, len(BANK_SLOTS)) if s not in melodic_slots and s not in kits and s not in KIT_SLOTS]
    numbered = [s for s in [*melodic_slots, *kits] if isinstance(s, int)]
    extra = iter(range(max([EXTRA_SLOT - 1, *numbered]) + 1, 10 ** 6))

    def unique(name: str, what: str) -> str:
        """Names are lookup keys (pitch plans, loop sizes, ntp_attack_bytes): never share one."""
        out = name if name not in names else f"{name} ({what})"
        names.add(out)
        return out

    melodic, drum = [], []
    for p in sorted(set(programs) - covered):
        slot = free.pop(0) if free else next(extra)
        m = {"slot": slot, "name": unique(mt32.MT32_PRESET_NAMES[p], f"program {p}"), "program": p,
             "covers": [p], "key": _median(programs[p]), "default": True}
        melodic.append(m)
        log(f"sounds: default for MT-32 program {p} ({m['name']}, {_notes(len(programs[p]))}): "
            f"sampled at key {m['key']}, slot {slot}")
    for k in sorted(set(rhythm) - covered_keys):
        if k not in mt32.MT32_RHYTHM_KEYS:
            log(f"sounds: rhythm key {k} ({_notes(rhythm[k])}) is silent on the MT-32: skipped")
            continue
        roomy = [s for s, n in sorted(kits.items(), key=lambda kv: kv[1]) if n < KIT_ZONES]
        slot = roomy[0] if roomy else next(extra)       # past the bank: mt32bank names it
        kits[slot] = kits.get(slot, 0) + 1
        d = {"slot": slot, "name": unique(mt32.GM_DRUM_NAMES.get(k, f"Rhythm {k}"), f"key {k}"), "key": k,
             "covers": [k], "bytes": DRUM_BYTES, "default": True}
        drum.append(d)
        log(f"sounds: default for rhythm key {k} ({d['name']}, {_notes(rhythm[k])})")
    if not melodic and not drum:
        return spec
    return {**spec, "melodic": [*spec["melodic"], *melodic], "drum": [*spec["drum"], *drum]}
