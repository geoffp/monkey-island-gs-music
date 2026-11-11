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

from collections.abc import Callable, Iterable

from . import mt32

DRUM_BYTES = 2048
RHYTHM_CHANNEL = 9                # 0-based (MIDI channel 10)
BANK_SLOTS = range(1, 16)         # MIDI Synth bank slots for melodic sounds; 0 and 9 are drum kits
DEFAULT_DRUM_SLOT = 9
EXTRA_SLOT = 200                  # slots for defaults past the 16-slot MIDI Synth bank (NTP only)


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
    used_slots = {m["slot"] for m in spec["melodic"]}
    free = [s for s in BANK_SLOTS if s != DEFAULT_DRUM_SLOT and s not in used_slots]
    extra = iter(range(max([EXTRA_SLOT - 1, *used_slots]) + 1, 10 ** 6))
    melodic, drum = [], []
    for p in sorted(set(programs) - covered):
        slot = free.pop(0) if free else next(extra)
        m = {"slot": slot, "name": mt32.MT32_PRESET_NAMES[p], "program": p, "covers": [p],
             "key": _median(programs[p]), "default": True}
        melodic.append(m)
        log(f"sounds: default for MT-32 program {p} ({m['name']}, {_notes(len(programs[p]))}): "
            f"sampled at key {m['key']}, slot {slot}")
    for k in sorted(set(rhythm) - covered_keys):
        if k not in mt32.MT32_RHYTHM_KEYS:
            log(f"sounds: rhythm key {k} ({_notes(rhythm[k])}) is silent on the MT-32: skipped")
            continue
        d = {"slot": DEFAULT_DRUM_SLOT, "name": mt32.GM_DRUM_NAMES.get(k, f"Rhythm {k}"), "key": k,
             "covers": [k], "bytes": DRUM_BYTES, "default": True}
        drum.append(d)
        log(f"sounds: default for rhythm key {k} ({d['name']}, {_notes(rhythm[k])})")
    if not melodic and not drum:
        return spec
    return {**spec, "melodic": [*spec["melodic"], *melodic], "drum": [*spec["drum"], *drum]}
