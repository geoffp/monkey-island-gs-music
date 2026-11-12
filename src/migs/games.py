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
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT = "monkey"
CONFIG = Path("config/games")
BUILD = Path("build")
ORIGINAL = Path("original files")
ENGINES = ("scumm-v4", "scumm-v5")
# build/ folders that aren't games: a game with one of these ids would mix its output into them
RESERVED_IDS = ("cache", "selftest")
# ProDOS names: 15 characters, letters/digits/periods, starting with a letter. The longest name
# built from the prefix is the synthLAB disk's volume, <prefix>.SYNTHLAB.
_PREFIX = re.compile(r"[A-Z][A-Z0-9]{0,5}")
# Other files beside the songs' <prefix><sound id>.SEQ in build/<game>/seq/stock/, which `just seq`
# must not mistake for songs: the drum auditions, AUDITnn.SEQ.
RESERVED_PREFIXES = ("AUDIT",)
MOD_TITLE = 20                   # bytes in a MOD's song title (its name, else "<label> <sound id>")


# NTP build variants a game's disk gets unless its game.toml lists others (`ntp_variants`, a
# space-separated string: the justfile reads it through tools/game.py)
NTP_VARIANTS = ("inst-per-song", "inst-shared")


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
    mt32: dict = field(default_factory=dict)     # [mt32]: the game's MT-32 driver (init SysEx, GM for its timbres)
    music: dict = field(default_factory=dict)    # [music]: which sounds are music (engines with sound effects mixed in)
    ntp_variants: tuple[str, ...] = NTP_VARIANTS  # NTP build variants for its disk (game.toml `ntp_variants`)
    song_names: dict[str, str] = field(default_factory=dict)  # sound id -> name (song_names.toml)

    @property
    def mt32_init(self) -> list[bytes]:
        """SysEx (from the 0x41 byte, without F0/F7) the game's MT-32 driver sends at start-up,
        which renders must send too (MI2: its rhythm-key map)."""
        return [bytes.fromhex(h) for h in self.mt32.get("init", [])]

    @property
    def timbre_gm(self) -> dict[str, int]:
        """Custom MT-32 timbre name -> General MIDI program, for the GM rendition."""
        return dict(self.mt32.get("timbre_gm", {}))

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

    def song_name(self, sound_id: int | str) -> str:
        """A song's name from song_names.toml ("" if it has none); ids match with or without
        leading zeros (Atlantis 009)."""
        return self.song_names.get(str(int(sound_id)) if str(sound_id).isdigit() else str(sound_id), "")

    def song_title(self, sound_id: int | str) -> str:
        """A song's title in its MOD (and so its .NTP) or sequence: its name, else "MI1 150"."""
        return self.song_name(sound_id) or f"{self.label} {sound_id}"

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
    if c["prefix"] in RESERVED_PREFIXES:
        raise ValueError(f"{game_id}/game.toml: prefix {c['prefix']!r} is reserved ({', '.join(RESERVED_PREFIXES)}: "
                         "other sequences share the songs' folder)")
    if len(c["label"]) + 4 > MOD_TITLE:
        raise ValueError(f"{game_id}/game.toml: label {c['label']!r} leaves no room in a MOD title "
                         f"({MOD_TITLE} bytes, '<label> <sound id>')")
    for h in c.get("mt32", {}).get("init", []):
        b = bytes.fromhex(h)
        if len(b) < 6 or b[0] != 0x41 or b[3] != 0x12:
            raise ValueError(f"{game_id}/game.toml: [mt32] init {h[:16]}...: not a Roland DT1 (41 dd 16 12 ...)")
    return GameInfo(game_id, c["name"], c["engine"], root / ORIGINAL / c["data"], c["prefix"], c["label"],
                    dict(c.get("md5", {})), root, dict(c.get("mt32", {})), dict(c.get("music", {})),
                    tuple(c.get("ntp_variants", " ".join(NTP_VARIANTS)).split()))


def parse_names(game_id: str, text: str) -> dict[str, str]:
    """A song_names.toml's [names]: sound id -> name, checked to fit a MOD title."""
    names = {}
    for k, v in tomllib.loads(text).get("names", {}).items():
        if not str(k).isdigit():
            raise ValueError(f"{game_id}/song_names.toml: {k!r} is not a sound id")
        if not (isinstance(v, str) and v and v.isascii() and v.isprintable() and len(v) <= MOD_TITLE):
            raise ValueError(f"{game_id}/song_names.toml: {k} = {v!r}: a name is 1-{MOD_TITLE} printable "
                             "ASCII characters (a MOD title)")
        names[str(int(k))] = v
    return names


def load(game_id: str | None = None, root: Path = Path(".")) -> GameInfo:
    """The game's settings from config/games/<id>/game.toml."""
    game_id = game_id or default_id()
    if game_id in RESERVED_IDS:
        raise SystemExit(f"{game_id!r} can't be a game id: build/{game_id}/ is shared ({', '.join(RESERVED_IDS)})")
    path = root / CONFIG / game_id / "game.toml"
    if not path.is_file():
        known = sorted(p.parent.name for p in (root / CONFIG).glob("*/game.toml"))
        raise SystemExit(f"no game {game_id!r} ({path} not found; known: {', '.join(known) or 'none'})")
    g = parse(game_id, path.read_text(), root)
    names = path.with_name("song_names.toml")
    return replace(g, song_names=parse_names(game_id, names.read_text())) if names.is_file() else g


def default_id() -> str:
    return os.environ.get("MIGS_GAME") or DEFAULT


def add_argument(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--game", default=None,
                    help=f"game id, a folder in {CONFIG}/ (default: $MIGS_GAME, else {DEFAULT})")
