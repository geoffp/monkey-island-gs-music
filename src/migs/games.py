# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-game settings. Each game has a folder `config/games/<id>/` (the id is ScummVM's) holding
`game.toml` (what and where the game is, and the names its outputs take) plus that game's build
configs (`ntp.toml`, `mt32sounds.toml`, `midisynth.toml`, `disk/README.TXT`, optionally
`song_names.toml`). Its outputs go to `build/<id>/`; `build/cache/` (Munt renders) is shared.

Every command takes `--game` (default: $MIGS_GAME, else `monkey`), and its path options default
to that game's files. Paths are relative to the repository root, like the rest of the tools.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT = "monkey"
CONFIG = Path("config/games")
BUILD = Path("build")
ORIGINAL = Path("original files")
ENGINES = ("scumm-v4",)
# ProDOS names: 15 characters, letters/digits/periods, starting with a letter. The longest name
# built from the prefix is the synthLAB disk's volume, <prefix>.SYNTHLAB.
_PREFIX = re.compile(r"[A-Z][A-Z0-9]{0,5}")
MOD_TITLE = 20                   # bytes in a MOD's song title ("<label> <sound id>")


@dataclass(frozen=True)
class GameInfo:
    id: str
    name: str
    engine: str
    data: Path                   # the game's files (original files/<data>)
    prefix: str                  # song files and disk volumes: MI150.mod, MI.NTP
    label: str                   # titles: "MI1 150"
    md5: dict[str, str] = field(default_factory=dict)
    root: Path = Path(".")

    @property
    def config_dir(self) -> Path:
        return self.root / CONFIG / self.id

    @property
    def build_dir(self) -> Path:
        return self.root / BUILD / self.id

    def config(self, name: str) -> Path:
        return self.config_dir / name

    def song_file(self, sound_id: int | str, ext: str) -> str:
        """A song's file name: MI150.mod, MI150.SEQ."""
        return f"{self.prefix}{sound_id}.{ext}"

    def song_title(self, sound_id: int | str) -> str:
        """A song's title in its MOD or sequence: "MI1 150"."""
        return f"{self.label} {sound_id}"

    def md5_mismatches(self) -> list[str]:
        """Files of `md5` that are missing from `data` or differ: the config was tuned on
        another release."""
        bad = []
        for name, want in self.md5.items():
            p = self.data / name
            if not p.is_file():
                hits = [q for q in self.data.iterdir() if q.name.lower() == name.lower()] if self.data.is_dir() else []
                if not hits:
                    bad.append(f"{name}: missing")
                    continue
                p = hits[0]
            got = hashlib.md5(p.read_bytes()).hexdigest()
            if got != want:
                bad.append(f"{name}: md5 {got}, expected {want}")
        return bad


def parse(game_id: str, text: str, root: Path = Path(".")) -> GameInfo:
    """A game.toml's settings, checked."""
    c = tomllib.loads(text)
    missing = [k for k in ("name", "engine", "data", "prefix", "label") if k not in c]
    if missing:
        raise ValueError(f"{game_id}/game.toml: missing {', '.join(missing)}")
    if c["engine"] not in ENGINES:
        raise ValueError(f"{game_id}/game.toml: engine {c['engine']!r} is not one of {', '.join(ENGINES)}")
    if not _PREFIX.fullmatch(c["prefix"]):
        raise ValueError(f"{game_id}/game.toml: prefix {c['prefix']!r} must be 1-6 upper-case letters or "
                         "digits, starting with a letter (ProDOS names: <prefix>.SYNTHLAB has 15 characters)")
    if len(c["label"]) + 4 > MOD_TITLE:
        raise ValueError(f"{game_id}/game.toml: label {c['label']!r} leaves no room in a MOD title "
                         f"({MOD_TITLE} bytes, '<label> <sound id>')")
    return GameInfo(game_id, c["name"], c["engine"], root / ORIGINAL / c["data"], c["prefix"], c["label"],
                    dict(c.get("md5", {})), root)


def load(game_id: str | None = None, root: Path = Path(".")) -> GameInfo:
    """The game's settings from config/games/<id>/game.toml."""
    game_id = game_id or default_id()
    path = root / CONFIG / game_id / "game.toml"
    if not path.is_file():
        known = sorted(p.parent.name for p in (root / CONFIG).glob("*/game.toml"))
        raise SystemExit(f"no game {game_id!r} ({path} not found; known: {', '.join(known) or 'none'})")
    return parse(game_id, path.read_text(), root)


def default_id() -> str:
    return os.environ.get("MIGS_GAME") or DEFAULT


def add_argument(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--game", default=None,
                    help=f"game id, a folder in {CONFIG}/ (default: $MIGS_GAME, else {DEFAULT})")
