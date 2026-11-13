# SPDX-License-Identifier: GPL-3.0-or-later
"""DOC RAM maps: how each song of an NTP build variant spends the Ensoniq DOC's 64 KB.

Reads the Ninjaforce converter's log beside each `.ntp` (every instrument's DOC address and
wavetable-size register; docs/ninjatrackerplus.md, "Converter rules") and the song's MOD
(sample names), and writes one self-contained HTML page per variant: a treemap per song, each
the whole 64 KB at one scale, blocks grouped by sound and coloured by what they do (long
loop, attack and short loop, one-shot), with free pages and the game's sound-effect reserve.
`just ntp` runs it for every variant: build/<game>/ntp/<variant>/docram.html.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import games, sounds
from .mt32 import GM_DRUM_NAMES

DOC_PAGES = 256                       # 64 KB of DOC RAM in 256-byte pages
TEMPLATE = Path(__file__).with_name("docmap.html")

_INSTRUMENT = re.compile(r"modnumber=\s*(\d+), type=%([01]+), streamed=\s*\w+\s*, length=\s*(\d+).*?"
                         r"docptr=\$([0-9a-fA-F]+), docsiz=%([01]+)")


@dataclass(frozen=True)
class Block:
    sample: str        # MOD sample name
    part: str          # "attack" (loop header), "loop", "one-shot" or "looped" (looped whole)
    addr: int          # DOC RAM address
    bytes: int         # block size in DOC RAM
    length: int        # bytes of sample in it


def doc_block_bytes(register: int) -> int:
    """Block size from the DOC wavetable-size register: bits 5-3 hold n for 2**n pages
    (ntpconverter_lib.php DOC_REGISTER_SIZES: 0b000000, 0b001001, ... 0b111111)."""
    return 256 << ((register >> 3) & 7)


def part_of(type_bits: int) -> str:
    """The NTP instrument type %0000zxyy's yy (file_format.txt)."""
    return ("one-shot", "looped", "attack", "loop")[type_bits & 3]


def mod_sample_names(mod: bytes) -> list[str]:
    """The 31 sample names of a ProTracker MOD (22 bytes each, from offset 20)."""
    return [mod[20 + i * 30:42 + i * 30].split(b"\0")[0].decode("latin-1").strip() for i in range(31)]


def parse_log(text: str, names: list[str]) -> list[Block]:
    """Every DOC-resident instrument in a converter log, named by its MOD sample (`modnumber`
    counts from 1). Streamed instruments (docsiz 0, no DOC block) are left out."""
    out = []
    for m in _INSTRUMENT.finditer(text):
        mod_no, typ, length, ptr, reg = int(m[1]), int(m[2], 2), int(m[3]), int(m[4], 16), int(m[5], 2)
        if typ & 0b1000:               # streamed: played from main memory through a buffer
            continue
        name = names[mod_no - 1] if 0 < mod_no <= len(names) else f"#{mod_no}"
        out.append(Block(name, part_of(typ), ptr, doc_block_bytes(reg), length))
    return out


def sound_of(sample: str) -> str:
    """The sound a sample belongs to: a key zone is named "<sound> <key>" (keys >= 12), so
    "Elec Organ 64" is Elec Organ, "Violin 2 72" is Violin 2, and "Trombone 1" is itself."""
    m = re.match(r"^(.*) (\d+)$", sample)
    return m[1] if m and int(m[2]) >= 12 else sample


def category(b: Block, drums: set[str]) -> str:
    """long (a loop longer than the 512 B short loop), base (attack or short loop), drum (a
    one-shot named as a drum: `drums`), shot (a melodic one-shot, e.g. Xylophone), rest (the
    silent pair that ends released notes)."""
    if b.sample == "rest":
        return "rest"
    if b.part == "one-shot":
        return "drum" if b.sample in drums else "shot"
    return "long" if b.part in ("loop", "looped") and b.bytes > 512 else "base"


def drum_names(spec: dict) -> set[str]:
    """What a drum sample may be called: the game's mt32sounds.toml drum entries, and the GM
    names a rhythm key no entry covers is given (src/migs/sounds.py)."""
    return {d["name"] for d in spec.get("drum", [])} | set(GM_DRUM_NAMES.values())


def song_entry(sound_id: str, title: str, blocks: list[Block], drums: set[str]) -> dict:
    return {"id": sound_id, "t": title,
            "blocks": [{"n": b.sample, "s": "Rest" if b.sample == "rest" else sound_of(b.sample), "p": b.part,
                        "b": b.bytes, "a": b.addr, "c": category(b, drums)} for b in blocks]}


def variant_songs(variant_dir: Path, game: games.GameInfo, drums: set[str]) -> list[dict]:
    """One entry per converted song in a variant folder, in sound-id order."""
    out = []
    for log in sorted(variant_dir.glob(f"{game.prefix}*.log")):
        sound_id = log.stem[len(game.prefix):]
        mod = log.with_suffix(".mod")
        if not mod.is_file():
            continue
        blocks = parse_log(log.read_text(errors="replace"), mod_sample_names(mod.read_bytes()))
        if blocks:
            out.append(song_entry(sound_id, game.song_name(sound_id), blocks, drums))
    return out


def render(songs: list[dict], *, title: str, dek: str, foot: str, reserve_pages: int) -> str:
    """The page: the template with the songs' data and its words filled in."""
    data = json.dumps(songs, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text()
    for key, value in (("__TITLE__", html.escape(title)), ("__DEK__", html.escape(dek)),
                       ("__FOOT__", html.escape(foot)), ("__RESERVE__", str(int(reserve_pages))),
                       ("/*DATA*/null", data)):
        page = page.replace(key, value)
    return page


VARIANT_WORDS = {
    "per-song": "each song carries only its own instruments, with all of the sound RAM to itself",
    "shared": "every song carries the same instrument set, identical in each file",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    games.add_argument(ap)
    ap.add_argument("variants", nargs="+", type=Path, help="NTP variant folders (build/<game>/ntp/<variant>)")
    ap.add_argument("--config", type=Path, help="NTP settings, for reserve_pages (default the game's ntp.toml)")
    ap.add_argument("--spec", type=Path, help="MT-32 sounds, for drum names (default the game's mt32sounds.toml)")
    args = ap.parse_args(argv)
    game = games.load(args.game)
    conf = tomllib.loads((args.config or game.config("ntp.toml")).read_text())
    drums = drum_names(sounds.load(args.spec or game.config("mt32sounds.toml")))
    from .ntpmod import resolve_mode               # the build's own layering of [modes.<name>]
    for d in args.variants:
        variant = d.name
        try:
            mode, cfg = resolve_mode(conf, variant)
        except SystemExit:
            mode, cfg = "per-song", conf.get("defaults", {})
        reserve = int(cfg.get("reserve_pages", 16))
        songs = variant_songs(d, game, drums)
        if not songs:
            print(f"docmap: {d}: no converted songs (run `just ntp`)")
            continue
        page = render(
            songs,
            title=f"{game.name}: DOC RAM by song ({variant})",
            dek=(f"How each song of the {variant} build spends the Ensoniq DOC's 64 KB of sound RAM "
                 f"({VARIANT_WORDS.get(mode, mode)}): {DOC_PAGES} pages of 256 bytes, with {reserve} "
                 f"kept free for the game's sound effects. Every panel is the whole 64 KB at the same "
                 f"scale, with one rectangle per memory block, grouped by instrument."),
            foot=(f"Source: the NinjaTrackerPlus converter's log for each song in {d} (block address "
                  f"and DOC size register), with sample names from the song's MOD. Made by `just ntp` "
                  f"(src/migs/docmap.py)."),
            reserve_pages=reserve)
        out = d / "docram.html"
        out.write_text(page)
        print(f"DOC RAM map: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
