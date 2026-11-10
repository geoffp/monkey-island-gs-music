# SPDX-License-Identifier: GPL-3.0-or-later
"""GSSquared .gs2 config generation."""

import tomllib

from bram_fixtures import REAL
from migs import bram
from migs.emu import Disk, write_config


def test_new_config_is_valid_toml_with_seeded_patched_bram(tmp_path):
    f = tmp_path / "m.gs2"
    write_config(f, "MI1", "ludicrous", {7: "bazfast3"}, [(7, 1, "MI.NTP.PO")],
                 {bram.STARTUP_SLOT: 7, bram.SLOT_INTERNAL[6]: 1, bram.SLOT_INTERNAL[7]: 1}, REAL)
    cfg = tomllib.loads(f.read_text())
    assert cfg["platform"] == "apple2gs" and cfg["speed"] == "ludicrous"
    assert cfg["cards"] == [{"slot": 7, "card": "bazfast3"}]
    assert cfg["storage"] == [{"slot": 7, "drive": 1, "image": "MI.NTP.PO"}]
    b = bytes.fromhex(cfg["bram"])
    assert bram.is_valid(b) and (b[0x28], b[0x26], b[0x27]) == (7, 1, 1)
    assert f.read_text().index("bram") < f.read_text().index("[[cards]]")   # root key first


def test_rewrite_keeps_gssquared_id_and_saved_bram(tmp_path):
    f = tmp_path / "m.gs2"
    saved = bram.patch(REAL, {0x1E: 3})                                    # user changed volume
    f.write_text(f'gs2_version = 1\nid = "abc-123"\nclock = "ntsc"\nbram = "{saved.hex()}"\n')
    write_config(f, "MI1", "2.8mhz", {}, [(5, 1, "SYSTEM.DISK.PO")], {bram.STARTUP_SLOT: 5}, REAL)
    cfg = tomllib.loads(f.read_text())
    b = bytes.fromhex(cfg["bram"])
    assert cfg["id"] == "abc-123" and cfg["speed"] == "2.8mhz"
    assert b[0x1E] == 3 and b[0x28] == 5 and bram.is_valid(b)              # kept + patched


def test_names_with_quotes_and_control_characters_stay_valid_toml(tmp_path):
    f = tmp_path / "m.gs2"
    name = 'MI1 "music"\\\tx'
    write_config(f, name, "ludicrous", {}, [Disk(7, 1, "A B.PO")], {}, None)
    cfg = tomllib.loads(f.read_text())
    assert cfg["name"] == name and "bram" not in cfg
