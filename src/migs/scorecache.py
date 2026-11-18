# SPDX-License-Identifier: GPL-3.0-or-later
"""Scores kept on disk between builds: one JSON file `{"version": N, "results": {key: score}}`.

Both similarity measures (docsim, docsim2) use it, each with its own `version`: bump it when the
measure changes and the file's results are dropped. `save` merges into what's on disk (other
builds may have added keys) and replaces the file atomically.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path


class Cache:
    def __init__(self, path: Path | None, version: int):
        self.path, self.version = path, version
        self.data: dict[str, float] = self._read()
        self._dirty = False

    def _read(self) -> dict[str, float]:
        if self.path is not None and self.path.exists():
            d = json.loads(self.path.read_text())
            if d.get("version") == self.version:
                return d["results"]
        return {}

    def get(self, key: str, compute: Callable[[], float]) -> float:
        if key not in self.data:
            self.data[key] = float(compute())
            self._dirty = True
        return self.data[key]

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        merged = self._read()
        merged.update(self.data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": self.version, "results": merged}))
        tmp.replace(self.path)
        self._dirty = False
