#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Print one top-level value of config/games/<GAME>/game.toml, for the justfile:

    python3 tools/game.py GAME KEY

Standard library only, like tools/fresh.py, and it must run before `just setup` (on macOS the
system python3 is 3.9, which has no tomllib): without tomllib it reads `key = "string"` lines
before the first table, which is all the justfile needs. migs.games reads the whole file and
checks it; tests/test_games.py checks both read the same values.
"""

import re
import sys
from pathlib import Path


def values(text: str) -> dict:
    try:
        import tomllib
    except ImportError:
        out = {}
        for line in text.splitlines():
            if line.lstrip().startswith("["):
                break
            m = re.match(r'\s*([A-Za-z0-9_-]+)\s*=\s*"((?:[^"\\]|\\.)*)"', line)
            if m:
                out[m.group(1)] = m.group(2).replace('\\"', '"').replace("\\\\", "\\")
        return out
    return {k: v for k, v in tomllib.loads(text).items() if isinstance(v, str)}


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    game, key = argv
    path = Path("config/games") / game / "game.toml"
    if not path.is_file():
        known = sorted(p.parent.name for p in Path("config/games").glob("*/game.toml"))
        print(f"no game {game!r} ({path} not found; known: {', '.join(known)})", file=sys.stderr)
        return 1
    v = values(path.read_text())
    if key not in v:
        print(f"{path}: no top-level string {key!r}", file=sys.stderr)
        return 1
    print(v[key])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
