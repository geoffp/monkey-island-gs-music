# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-game settings (migs.games, config/games/<id>/game.toml) and the justfile's reader of
them (tools/game.py), which must agree."""

import dataclasses
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from migs import games

ROOT = Path(__file__).resolve().parents[1]
GAME_IDS = sorted(p.parent.name for p in (ROOT / games.CONFIG).glob("*/game.toml"))
GOOD = '''name = "Test"
engine = "scumm-v4"
data = "Test (DOS)"
prefix = "TG"
label = "TG1"
[md5]
"000.LFL" = "{md5}"
'''


def test_every_game_config_loads_and_has_its_build_files():
    assert "monkey" in GAME_IDS
    for gid in GAME_IDS:
        g = games.load(gid, ROOT)
        assert g.id == gid and g.data.parent == ROOT / games.ORIGINAL
        for f in ("ntp.toml", "mt32sounds.toml", "midisynth.toml", "disk/README.TXT"):
            assert g.config(f).is_file(), f"{gid}: {f}"


def test_names_paths_and_titles_come_from_the_game():
    g = games.parse("tg", GOOD.format(md5="0" * 32), ROOT)
    assert g.build_dir == ROOT / "build/tg" and g.config("ntp.toml") == ROOT / "config/games/tg/ntp.toml"
    assert g.data == ROOT / "original files/Test (DOS)"
    assert g.song_file(150, "mod") == "TG150.mod" and g.song_title("150") == "TG1 150"


def test_monkey_keeps_its_names():
    """Moving MI1 into config/games/monkey/ must not rename a single output."""
    g = games.load("monkey", ROOT)
    assert (g.song_file(150, "mod"), g.song_file("150", "SEQ")) == ("MI150.mod", "MI150.SEQ")


def test_a_song_with_a_name_is_titled_by_it():
    """song_names.toml names a song's MOD (and so its .NTP) title; others keep "<label> <id>"."""
    g = games.parse("tg", GOOD.format(md5="0" * 32), ROOT)
    g = dataclasses.replace(g, song_names=games.parse_names("tg", '[names]\n9 = "Opening Theme"\n'))
    assert g.song_title("009") == g.song_title(9) == "Opening Theme" and g.song_name("010") == ""
    assert g.song_title("150") == "TG1 150"
    assert games.load("monkey", ROOT).song_title(160) == "Opening Theme"


@pytest.mark.parametrize("text", ['[names]\n1 = "A name far longer than twenty"', '[names]\n1 = "Mêlée Island"',
                                  '[names]\nx = "Name"', '[names]\n1 = ""'])
def test_a_song_name_must_fit_a_mod_title(text):
    with pytest.raises(ValueError):
        games.parse_names("tg", text)


@pytest.mark.parametrize("field, value", [
    ("prefix", "mi"), ("prefix", "1MI"), ("prefix", "MONKEY1"), ("prefix", "M-I"), ("prefix", "AUDIT"),
    ("label", "A LABEL FAR TOO LONG"), ("engine", "scumm-v6"),
])
def test_bad_settings_are_refused(field, value):
    text = "\n".join(f'{field} = "{value}"' if line.startswith(field + " ") else line
                     for line in GOOD.format(md5="0" * 32).splitlines())
    with pytest.raises(ValueError, match=field):
        games.parse("tg", text)


def test_missing_settings_and_unknown_games_are_named():
    with pytest.raises(ValueError, match="prefix"):
        games.parse("tg", 'name = "x"\nengine = "scumm-v4"\ndata = "x"\nlabel = "x"\n')
    with pytest.raises(SystemExit, match="no game 'nosuch'.*monkey"):
        games.load("nosuch", ROOT)


def test_default_game_follows_migs_game(monkeypatch):
    monkeypatch.delenv("MIGS_GAME", raising=False)
    assert games.default_id() == "monkey"
    monkeypatch.setenv("MIGS_GAME", "tg")
    assert games.default_id() == "tg"


def test_md5_check_names_missing_and_different_files(tmp_path):
    data = tmp_path / "original files" / "Test (DOS)"
    data.mkdir(parents=True)
    g = games.parse("tg", GOOD.format(md5=hashlib.md5(b"index").hexdigest()), tmp_path)
    assert g.md5_mismatches() == ["000.LFL: missing"]
    (data / "000.lfl").write_bytes(b"index")            # DOS names: any case
    assert g.md5_mismatches() == []
    (data / "000.lfl").write_bytes(b"other")
    assert "000.LFL: md5" in g.md5_mismatches()[0]


def _tool(*args, python=sys.executable):
    return subprocess.run([python, "-I", str(ROOT / "tools/game.py"), *args], cwd=ROOT,
                          capture_output=True, text=True)


@pytest.mark.parametrize("gid", GAME_IDS)
def test_the_justfile_reader_agrees_with_migs_games(gid):
    g = games.load(gid, ROOT)
    for key, want in (("data", g.data.name), ("prefix", g.prefix), ("label", g.label)):
        r = _tool(gid, key)
        assert r.returncode == 0 and r.stdout == want + "\n", r.stderr
    r = _tool(gid, "ntp_variants", " ".join(games.NTP_VARIANTS))       # as the justfile asks
    assert r.returncode == 0 and r.stdout.split() == list(g.ntp_variants), r.stderr


def test_a_game_lists_its_ntp_variants_or_gets_both():
    """iMUSE games play too many sounds for a shared set: their disks are per-song only."""
    assert games.load("monkey", ROOT).ntp_variants[:2] == ("inst-per-song", "inst-shared")   # plus any trials
    assert games.load("monkey2", ROOT).ntp_variants == ("inst-per-song",)
    assert _tool("monkey", "no_such_key").returncode == 1
    r = _tool("monkey", "no_such_key", "x y")
    assert r.returncode == 0 and r.stdout == "x y\n"


def test_the_justfile_reader_works_without_tomllib():
    """macOS's system python3 (3.9) has no tomllib; the fallback reads the same top-level strings."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import game as tool
    finally:
        sys.path.pop(0)
    real = __import__("tomllib")
    sys.modules["tomllib"] = None                      # import tomllib -> ImportError
    try:
        for gid in GAME_IDS:
            text = (ROOT / games.CONFIG / gid / "game.toml").read_text()
            assert tool.values(text) == {k: v for k, v in real.loads(text).items() if isinstance(v, str)}
    finally:
        sys.modules["tomllib"] = real


def test_the_fallback_reads_both_toml_string_kinds():
    """Basic and literal strings, escapes and comments, as tomllib reads them; tables end it."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import game as tool
    finally:
        sys.path.pop(0)
    text = ('name = "A \\"quoted\\" game"   # comment\n'
            "data = 'Loom (DOS EGA)'\n"
            "path = 'C:\\games'\n"
            'prefix = "LM" # no space before the hash\n'
            "count = 3\n"
            "[md5]\n"
            '"000.LFL" = "x"\n')
    real = __import__("tomllib")
    want = {k: v for k, v in real.loads(text).items() if isinstance(v, str)}
    sys.modules["tomllib"] = None
    try:
        assert tool.values(text) == want
    finally:
        sys.modules["tomllib"] = real
    assert want["data"] == "Loom (DOS EGA)" and want["name"] == 'A "quoted" game'


def test_the_justfile_reader_fails_clearly():
    r = _tool("nosuch", "data")
    assert r.returncode == 1 and "no game 'nosuch'" in r.stderr
    r = _tool("monkey", "md5")
    assert r.returncode == 1 and "no top-level string" in r.stderr


@pytest.mark.parametrize("gid", games.RESERVED_IDS)
def test_shared_build_folders_cant_be_game_ids(gid):
    with pytest.raises(SystemExit, match="can't be a game id"):
        games.load(gid, ROOT)
    r = _tool(gid, "data")
    assert r.returncode == 1 and "can't be a game id" in r.stderr


def test_mt32_driver_settings_are_roland_sysex():
    """[mt32] init: what the game's MT-32 driver sends at start-up, as Roland DT1 SysEx (hex,
    from the 0x41 byte); timbre_gm: custom timbre names to GM programs."""
    base = GOOD.format(md5="0" * 32)
    g = games.parse("tg", base + '\n[mt32]\ninit = ["41101612030110407f0600"]\ntimbre_gm = { "Big Drum" = 116 }\n')
    assert g.mt32_init == [bytes.fromhex("41101612030110407f0600")] and g.timbre_gm == {"Big Drum": 116}
    assert games.parse("tg", base).mt32_init == []
    with pytest.raises(ValueError, match="DT1"):
        games.parse("tg", base + '\n[mt32]\ninit = ["7d00"]\n')
