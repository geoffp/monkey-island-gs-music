# SPDX-License-Identifier: GPL-3.0-or-later
"""The NTP disk's README is a template filled in from the build (migs.diskreadme): the song
count, file names and SenseiPlay placeholders aren't written into config."""

import subprocess
from pathlib import Path

import pytest

from migs import diskreadme as dr

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config/games/monkey/disk/README.TXT"


def stage(tmp_path, variants=("INST.PER.SONG", "INST.SHARED"), ids=(150, *range(158, 169), 151, 152, 153, 154)):
    for v in variants:
        (tmp_path / v).mkdir(parents=True)
        for i in ids:
            (tmp_path / v / f"MI{i}.NTP#d50008").write_bytes(b"")
    return tmp_path


def test_monkey_islands_readme_comes_out_as_it_was_written_by_hand(tmp_path):
    """The template plus MI's 16 songs give the README that was in config before (commit a41ceb2)."""
    old = subprocess.run(["git", "show", "a41ceb2:config/games/monkey/disk/README.TXT"], cwd=ROOT,
                         capture_output=True, text=True)
    if old.returncode:
        pytest.skip("git history not available")
    facts = dr.song_facts(stage(tmp_path), "MI")
    assert (facts["songs"], facts["first"], facts["last"], facts["gaps"]) == ("16", "MI150", "MI168", "GAP.13")
    assert dr.render(TEMPLATE.read_text(), facts) == old.stdout


def test_placeholders_follow_p8_trees_rule():
    """Entries 13, 26, 39, ... of a folder, counting its subfolders first."""
    assert dr.gap_names(0, 12) == [] and dr.gap_names(0, 13) == ["GAP.13"]
    assert dr.gap_names(0, 16) == ["GAP.13"] and dr.gap_names(0, 25) == ["GAP.13", "GAP.26"]
    assert dr.gap_names(2, 11) == ["GAP.13"]                       # 2 folders + 10 files, then the gap


def test_a_short_folder_drops_the_placeholder_sentence(tmp_path):
    facts = dr.song_facts(stage(tmp_path, ids=(1, 2, 3)), "MI")
    text = dr.render(TEMPLATE.read_text(), facts)
    assert "GAP" not in text and "[[" not in text and "{" not in text
    assert "/SONGS holds the same 3 songs built two ways:" in text
    assert text.rstrip().endswith("The file names (MI1 to MI3) are the game's sound numbers.")
    assert max(len(l) for l in text.splitlines()) <= dr.WIDTH


def test_several_placeholders_read_as_a_sentence(tmp_path):
    facts = dr.song_facts(stage(tmp_path, ids=range(100, 130)), "MI")
    assert facts["gaps"] == "Each of GAP.13 and GAP.26" and facts["gap_list"] == ["GAP.13", "GAP.26"]
    text = dr.render(TEMPLATE.read_text(), facts)
    assert "numbers. Each of GAP.13 and GAP.26 is a placeholder that" in " ".join(text.split())
    assert max(len(l) for l in text.splitlines()) <= dr.WIDTH


def test_template_rules():
    t = "# a comment\nTitle\n=====\n\nOne {x} two{y}[[ three {y}]].\n\n   indented {x}\n"
    assert dr.render(t, {"x": "X", "y": ""}) == "Title\n=====\n\nOne X two.\n\n   indented X\n"
    with pytest.raises(ValueError, match="unknown template fields: z"):
        dr.render("{z}", {})


def test_staged_songs_must_match_across_variants_and_be_song_files(tmp_path):
    stage(tmp_path / "a", variants=("A",), ids=(1, 2))
    stage(tmp_path / "a", variants=("B",), ids=(1,))
    with pytest.raises(ValueError, match="different songs"):
        dr.song_facts(tmp_path / "a", "MI")
    stage(tmp_path / "b", variants=("A",), ids=(1,))
    (tmp_path / "b/A/GAP.13#040000").write_bytes(b"")
    with pytest.raises(ValueError, match="not a song file"):
        dr.song_facts(tmp_path / "b", "MI")
