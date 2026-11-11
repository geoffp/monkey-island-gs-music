# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/fresh.py: input fingerprints, stamps and the import walk."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("fresh", Path(__file__).parent.parent / "tools" / "fresh.py")
fresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fresh)


def test_fingerprint_follows_names_and_contents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a").write_text("1")
    (tmp_path / "d" / "__pycache__").mkdir()
    (tmp_path / "d" / "__pycache__" / "x.pyc").write_text("ignored")
    fp = fresh.fingerprint(["d", "text:v1"])
    (tmp_path / "d" / "__pycache__" / "x.pyc").write_text("still ignored")
    assert fresh.fingerprint(["d", "text:v1"]) == fp
    assert fresh.fingerprint(["d", "text:v2"]) != fp
    (tmp_path / "d" / "a").write_text("2")
    assert fresh.fingerprint(["d", "text:v1"]) != fp
    (tmp_path / "d" / "a").rename(tmp_path / "d" / "b")
    assert fresh.fingerprint(["d", "text:v1"]) != fp


def test_missing_inputs_fail_unless_optional(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        fresh.fingerprint(["nope.toml"])
    absent = fresh.fingerprint(["opt:nope.toml"])
    (tmp_path / "nope.toml").write_text("")
    assert fresh.fingerprint(["opt:nope.toml"]) != absent


def test_run_skips_when_unchanged_and_stamps_only_on_success(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "in").write_text("x")
    count = tmp_path / "count"
    cmd = [sys.executable, "-c", "import pathlib; p = pathlib.Path('count'); p.write_text(p.read_text() + '.' "
                                 "if p.exists() else '.'); pathlib.Path('out').write_text('o')"]
    args = ["st/.inputs", "--out", "out", "in", "--"] + cmd
    assert fresh.run(args) == 0 and count.read_text() == "."
    assert fresh.run(args) == 0 and count.read_text() == "."          # up to date
    assert "out: up to date" in capsys.readouterr().out
    (tmp_path / "out").unlink()                                        # an output gone: rerun
    assert fresh.run(args) == 0 and count.read_text() == ".."
    (tmp_path / "in").write_text("y")                                  # an input changed: rerun
    assert fresh.run(args) == 0 and count.read_text() == "..."
    (tmp_path / "in").write_text("z")
    assert fresh.run(["st/.inputs", "in", "--", sys.executable, "-c", "raise SystemExit(3)"]) == 3
    assert not (tmp_path / "st" / ".inputs").exists()                   # failed: no stamp


def test_py_follows_package_imports(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    for name, text in {"__init__.py": "", "main.py": "import os\nfrom . import a\nfrom .sub.b import f\n",
                       "a.py": "from pkg import c\n", "c.py": "", "unused.py": "",
                       "sub/__init__.py": "", "sub/b.py": "def f():\n    from ..d import g\n", "d.py": ""}.items():
        (pkg / name).write_text(text)
    got = {p.relative_to(pkg).as_posix() for p in fresh.package_imports(pkg / "main.py")}
    assert got == {"__init__.py", "main.py", "a.py", "c.py", "sub/__init__.py", "sub/b.py", "d.py"}


@pytest.mark.parametrize("module", ["extract_music", "ntpmod"])
def test_py_covers_what_the_build_entry_points_load(module):
    """Every migs module a build entry point loads is hashed (checked in a fresh interpreter)."""
    import migs
    loaded = subprocess.run([sys.executable, "-c", f"import sys, migs.{module}; "
                             "print(*(m.__file__ for n, m in sys.modules.items() if n.split('.')[0] == 'migs'))"],
                            capture_output=True, text=True, check=True).stdout.split()
    src = Path(migs.__file__).parent
    hashed = set(fresh.package_imports(src / f"{module}.py"))
    assert {Path(f).resolve() for f in loaded} <= hashed
