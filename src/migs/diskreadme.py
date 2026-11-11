# SPDX-License-Identifier: GPL-3.0-or-later
"""The NTP disk's README.TXT, from the game's template (config/games/<game>/disk/README.TXT) and
the songs staged for the disk: what the build decides (how many songs, their file names, the
placeholder files SenseiPlay needs) is filled in, not written into config.

Template rules (also in the template's own header):
- lines starting with "#" are dropped;
- fields: {songs}, {first}, {last}, {gaps};
- [[...]] is left out when a field in it is empty;
- a paragraph (blank-line separated, not indented) with a field is re-wrapped to 70 columns.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

from . import games

WIDTH = 70                       # config/.editorconfig max_line_length
_FIELD = re.compile(r"\{(\w+)\}")
_OPTIONAL = re.compile(r"\[\[(.*?)\]\]", re.S)
# The justfile's p8_tree inserts a placeholder before entries 13, 26, 39, ... of a folder
# (counting its subfolders first): SenseiPlay v2.0 skips the first entry of each directory
# block after the first. Keep the two in step; `just disk` checks the image against this.
FIRST_GAP, GAP_EVERY = 12, 13


def gap_names(subdirs: int, files: int) -> list[str]:
    """The GAP.nn placeholders p8_tree adds to a folder with these many subfolders and files."""
    out, k = [], subdirs
    for _ in range(files):
        if k >= FIRST_GAP and (k - FIRST_GAP) % GAP_EVERY == 0:
            out.append(f"GAP.{k + 1}")
            k += 1
        k += 1
    return out


def song_facts(songs_dir: Path, prefix: str) -> dict[str, str]:
    """Fields from the staged songs: songs_dir/<VARIANT>/<prefix><id>.NTP[#type], one folder per
    build variant, each holding the same songs."""
    sets = {}
    gaps: set[str] = set()
    for d in sorted(p for p in songs_dir.iterdir() if p.is_dir()):
        files = [p for p in d.iterdir() if p.is_file()]
        ids = []
        for f in files:
            m = re.fullmatch(rf"{re.escape(prefix)}(\d+)\.NTP(#[0-9a-fA-F]{{6}})?", f.name)
            if not m:
                raise ValueError(f"{f}: not a song file ({prefix}<sound id>.NTP)")
            ids.append(int(m.group(1)))
        sets[d.name] = sorted(ids)
        gaps |= set(gap_names(sum(p.is_dir() for p in d.iterdir()), len(files)))
    if not sets:
        raise ValueError(f"{songs_dir}: no variant folders")
    if len({tuple(v) for v in sets.values()}) > 1:
        raise ValueError(f"{songs_dir}: the variant folders hold different songs: " +
                         "; ".join(f"{k} {v}" for k, v in sets.items()))
    ids = next(iter(sets.values()))
    if not ids:
        raise ValueError(f"{songs_dir}: no songs")
    g = sorted(gaps, key=lambda n: int(n.split(".")[1]))
    return {"songs": str(len(ids)), "first": f"{prefix}{ids[0]}", "last": f"{prefix}{ids[-1]}",
            "gaps": "" if not g else g[0] if len(g) == 1 else f"Each of {', '.join(g[:-1])} and {g[-1]}",
            "gap_list": g}


def render(template: str, fields: dict) -> str:
    """The template filled in (rules in the module docstring)."""
    fields = {k: v for k, v in fields.items() if isinstance(v, str)}
    text = "".join(l for l in template.splitlines(keepends=True) if not l.startswith("#"))
    unknown = set(_FIELD.findall(text)) - set(fields)
    if unknown:
        raise ValueError(f"unknown template fields: {', '.join(sorted(unknown))}")
    text = _OPTIONAL.sub(lambda m: m.group(1) if all(fields[f] for f in _FIELD.findall(m.group(1))) else "", text)
    paras = re.split(r"(\n\s*\n)", text)
    for i in range(0, len(paras), 2):
        p = paras[i]
        if _FIELD.search(p) and not any(l[:1].isspace() for l in p.splitlines() if l):
            end = p[len(p.rstrip("\n")):]
            p = textwrap.fill(" ".join(_FIELD.sub(lambda m: fields[m.group(1)], p).split("\n")), WIDTH,
                              break_on_hyphens=False) + end
        else:
            p = _FIELD.sub(lambda m: fields[m.group(1)], p)
        paras[i] = p
    return "".join(paras)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    games.add_argument(ap)
    ap.add_argument("songs_dir", type=Path, help="the disk's staged /songs folder (one folder per variant)")
    ap.add_argument("out", type=Path, help="README to write (LF line ends; the disk recipe converts to CR)")
    ap.add_argument("--template", type=Path, help="default the game's disk/README.TXT")
    args = ap.parse_args(argv)
    game = games.load(args.game)
    fields = song_facts(args.songs_dir, game.prefix)
    args.out.write_text(render((args.template or game.config("disk/README.TXT")).read_text(), fields))
    print(f"README: {fields['songs']} songs, {fields['first']}-{fields['last']}")
    print("placeholders:", " ".join(fields["gap_list"]) or "none")         # `just disk` checks the image
    return 0


if __name__ == "__main__":
    sys.exit(main())
