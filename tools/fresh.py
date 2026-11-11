#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Skip work whose inputs haven't changed (the justfile's freshness checks). Standard library
only, so it runs before `uv sync`.

  fresh.py get URL FILE SHA256
      Download URL to FILE unless FILE is already there with that sha256; check the download.

  fresh.py run STAMP [--out PATH]... INPUT... -- COMMAND...
      Run COMMAND unless the inputs hash to what STAMP holds (and every --out PATH exists, a
      folder non-empty). STAMP is removed before COMMAND runs and written only if it succeeds,
      so a failed or interrupted run always reruns. An INPUT is:
        PATH          a file, or a folder (every file under it, __pycache__ skipped)
        opt:PATH      the same, but may be absent (its absence is part of the hash)
        py:FILE       a Python module plus every module of its package it imports, recursively
        text:STRING   a literal: a version, a mode name
"""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def files_under(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def package_imports(file: Path) -> list[Path]:
    """`file` and the modules of its package it imports (relative or by package name), with
    each package's __init__.py: the source a `python -m pkg.module` run executes."""
    root = file.resolve().parent
    while (root.parent / "__init__.py").exists():
        root = root.parent                      # the top package folder
    seen: set[Path] = set()

    def module(base: Path, dotted: str) -> list[Path]:
        p = base.joinpath(*dotted.split(".")) if dotted else base
        return [q for q in (p.with_suffix(".py"), p / "__init__.py") if q.is_file()]

    def visit(f: Path) -> None:
        if f in seen:
            return
        seen.add(f)
        for d in f.parents:                     # a module's packages run their __init__ first
            if d == root.parent:
                break
            if (d / "__init__.py").is_file():
                visit(d / "__init__.py")
        for node in ast.walk(ast.parse(f.read_text(), str(f))):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = f.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    name = node.module or ""
                elif node.module and node.module.split(".")[0] == root.name:
                    base, name = root.parent, node.module
                else:
                    continue
                targets = module(base, name)
                for a in node.names:            # `from . import x`: x may be a module
                    targets += module(base, f"{name}.{a.name}".lstrip("."))
            elif isinstance(node, ast.Import):
                targets = [t for a in node.names if a.name.split(".")[0] == root.name
                           for t in module(root.parent, a.name)]
            else:
                continue
            for t in targets:
                visit(t.resolve())

    visit(file.resolve())
    return sorted(seen)


def fingerprint(inputs: list[str]) -> str:
    """sha256 over each input's name and contents, in the order given."""
    h = hashlib.sha256()
    for spec in inputs:
        if spec.startswith("text:"):
            entries = [(spec, "")]
        elif spec.startswith("py:"):
            cwd = Path.cwd().resolve()
            entries = [(str(p.relative_to(cwd) if p.is_relative_to(cwd) else p), sha256(p))
                       for p in package_imports(Path(spec[3:]))]
        else:
            optional = spec.startswith("opt:")
            path = Path(spec[4:] if optional else spec)
            if not path.exists():
                if not optional:
                    raise SystemExit(f"fresh: no input {path} (write opt:PATH if it may be absent)")
                entries = [(str(path), "absent")]
            else:
                entries = [(str(p), sha256(p)) for p in files_under(path)]
        for name, digest in entries:
            h.update(f"{name}\0{digest}\n".encode())
    return h.hexdigest()


def present(path: Path) -> bool:
    return path.is_file() or (path.is_dir() and any(path.iterdir()))


def run(args: list[str]) -> int:
    if "--" not in args or args.index("--") < 1:
        raise SystemExit("usage: fresh.py run STAMP [--out PATH]... INPUT... -- COMMAND...")
    split = args.index("--")
    stamp, rest, command = Path(args[0]), args[1:split], args[split + 1:]
    outs = [Path(rest[i + 1]) for i, a in enumerate(rest) if a == "--out"]
    inputs = [a for i, a in enumerate(rest) if a != "--out" and (i == 0 or rest[i - 1] != "--out")]
    fp = fingerprint(inputs)
    if stamp.is_file() and stamp.read_text().strip() == fp and all(map(present, outs)):
        print(f"{outs[0] if outs else stamp.parent}: up to date")
        return 0
    stamp.unlink(missing_ok=True)
    try:
        rc = subprocess.run(command).returncode
    except KeyboardInterrupt:
        return 130
    if rc == 0:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(fp + "\n")
    return rc


def get(url: str, file: str, want: str) -> int:
    f = Path(file)
    if f.is_file():
        if sha256(f) == want:
            return 0
        print(f"{f}: checksum mismatch, fetching again")
    f.parent.mkdir(parents=True, exist_ok=True)
    part = f.with_name(f.name + ".part")         # renamed only once it checks out
    try:
        if subprocess.run(["curl", "-fL", "--retry", "3", "-o", str(part), url]).returncode:
            raise SystemExit(f"fresh: couldn't download {url}")
        if (got := sha256(part)) != want:
            raise SystemExit(f"fresh: {url}: sha256 {got}, expected {want}")
        part.replace(f)
    finally:
        part.unlink(missing_ok=True)
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["get"] and len(argv) == 4:
        return get(*argv[1:])
    if argv[:1] == ["run"]:
        return run(argv[1:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
