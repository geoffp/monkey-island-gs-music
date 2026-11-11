# SPDX-License-Identifier: GPL-3.0-or-later
"""The NTP build's configuration: NTP-only sounds, key splits, modes (ntpmod)."""

from migs import ntpmod as nm
from migs import ntpsong as sg


def test_ntp_only_sounds_split_out_of_bank_merges():
    spec = {"melodic": [{"slot": 2, "name": "Elec Organ", "program": 9, "covers": [9, 13], "key": 60},
                        {"slot": 3, "name": "Solo", "program": 20, "covers": [20], "key": 60}], "drum": []}
    conf = {"melodic": [{"slot": 101, "name": "Pipe Organ", "program": 13, "covers": [13], "key": 60},
                        {"slot": 102, "name": "Solo2", "program": 20, "covers": [20], "key": 60}]}
    out = nm.ntp_spec(spec, conf)
    assert {m["name"]: m["covers"] for m in out["melodic"]} == {"Elec Organ": [9], "Pipe Organ": [13], "Solo2": [20]}
    assert spec["melodic"][0]["covers"] == [9, 13]                  # shared bank spec untouched


def test_key_split_sounds_pick_their_register():
    spec = {"melodic": [{"slot": 101, "name": "Hi", "program": 13, "covers": [13], "key": 60, "keys": [52, 127]},
                        {"slot": 102, "name": "Lo", "program": 13, "covers": [13], "key": 41, "keys": [0, 51]},
                        {"slot": 1, "name": "Bass", "program": 65, "covers": [65], "key": 36}], "drum": []}
    assert nm.melodic_entry(spec, 13, 40)["name"] == "Lo" and nm.melodic_entry(spec, 13, 52)["name"] == "Hi"
    assert nm.melodic_entry(spec, 65, 90)["name"] == "Bass" and nm.melodic_entry(spec, 7, 60) is None
    programs = {13: [(52, 127, 0), (0, 51, 1)], 65: 2}
    assert [sg.sample_for(programs, 13, k) for k in (36, 51, 52, 90)] == [1, 1, 0, 0]
    assert sg.sample_for(programs, 65, 30) == 2 and sg.sample_for(programs, 9, 60) is None


def test_key_split_falls_back_to_the_nearest_range_and_modes_filter_sounds():
    spec = {"melodic": [{"slot": 2, "name": "Elec Organ", "program": 9, "covers": [9, 13], "key": 60}], "drum": []}
    conf = {"melodic": [{"slot": 101, "name": "Hi", "program": 13, "covers": [13], "key": 60, "keys": [52, 127]},
                        {"slot": 102, "name": "Lo", "program": 13, "covers": [13], "key": 44, "keys": [40, 51]},
                        {"slot": 103, "name": "Lo2", "program": 13, "covers": [13], "key": 37, "keys": [0, 39],
                         "modes": ["per-song"]}]}
    shared = nm.ntp_spec(spec, conf, "shared")
    assert [m["name"] for m in shared["melodic"]] == ["Elec Organ", "Hi", "Lo"]
    assert nm.melodic_entry(shared, 13, 36)["name"] == "Lo"            # nearest range
    assert nm.melodic_entry(nm.ntp_spec(spec, conf, "per-song"), 13, 36)["name"] == "Lo2"
    assert sg.sample_for({13: [(52, 127, 0), (40, 51, 1)]}, 13, 30) == 1


# --- hybrid trial: key weights and the value budget -------------------------------------------

def test_hybrid_key_weights_mix_an_equal_share_with_note_time():
    w = {40: 9.0, 52: 1.0}                                   # 90 % of the note time on key 40
    assert nm.key_weights(w, {}) is w                       # weighted (default): note seconds as they are
    h = nm.key_weights(w, {"pitch_zones": "hybrid", "pitch_key_share": 0.5})
    assert h == {40: 0.25 + 0.45, 52: 0.25 + 0.05}            # half equal per played key, half by time
    assert set(h) == set(w)                                  # unplayed keys never count


def _budget(steps, plans=None, exposure=None, floor=0.01, last_page=255):
    import numpy as np
    from migs import ntpsamples as smp
    mel = smp.ModSample("A", np.zeros(1016 + 512), loop_start=1016, loop_len=512)
    spec = {"melodic": [{"name": "A", "ntp_attack_bytes": 1016}]}
    cfg = {"attack_bytes": 2040, "loop_bytes": 512, "upgrade_min_db_per_page": floor}
    return nm._value_budget(plans or {}, [mel], spec, cfg, last_page, exposure or {}, steps)


def test_value_budget_takes_the_best_db_per_page_and_may_jump_blocks():
    """Tom gains 0.2 dB at 16 pages and 3.8 more at 32: one step at a time it would stall."""
    steps = {"Tom": [(2048, -13.2, 0.35), (4096, -13.4, 0.7), (8192, -17.2, 0.74)],
             "Kick": [(1024, -12.0, 0.13), (2048, -16.5, 0.13)]}
    _, drums, lines = _budget(steps, exposure={"Tom": 10.0, "Kick": 10.0})
    assert drums == {"Tom": 8192, "Kick": 2048}
    assert "Kick" in lines[0]                               # 4.5 dB for 4 pages beats 4.0 dB for 24


def test_value_budget_stops_below_its_floor_and_scales_by_exposure():
    steps = {"Kick": [(1024, -12.0, 0.13), (2048, -16.5, 0.13)],
             "Rare": [(1024, -12.0, 0.13), (2048, -16.5, 0.13)]}
    # Kick: 4.5 dB x 99 % of the exposure / 4 pages = 1.1; Rare: x 1 % / 4 = 0.011
    _, drums, _ = _budget(steps, exposure={"Kick": 99.0, "Rare": 1.0}, floor=0.05)
    assert drums == {"Kick": 2048, "Rare": 1024}


def test_value_budget_never_overfills_the_doc():
    steps = {"Big": [(1024, -10.0, 0.1), (65536, -30.0, 2.5)]}
    _, drums, _ = _budget(steps, exposure={"Big": 1.0}, last_page=100)     # 256 pages won't fit in 100
    assert drums == {"Big": 1024}


def test_drum_error_counts_a_low_rate_and_a_cut():
    import numpy as np
    from migs import docsim
    from migs.munt import SR
    from scipy.signal import resample
    rng = np.random.default_rng(3)
    t = np.arange(int(0.15 * SR)) / SR
    hit = rng.standard_normal(len(t)) * np.exp(-t / 0.03)    # a bright, decaying noise burst
    at = lambda r, secs=0.15: resample(hit[:int(secs * SR)], int(secs * r))
    lo, hi = (docsim.drum_error_db(hit, at(r), r, 0, 0.15) for r in (5872.0, 26273.0))
    assert hi < lo - 6                                       # the top end and drop-sample images
    cut = docsim.drum_error_db(hit, at(26273.0, 0.03), 26273.0, 0, 0.15)
    assert cut > hi                                          # stopped after 30 ms of 150


def test_a_missing_midi_file_stops_with_a_hint_to_extract(tmp_path, monkeypatch):
    """After `just clean` the shell passes the unexpanded glob: say what to do, not a traceback.
    Checked before the game is looked up, so it holds from any directory and for any game."""
    import pytest
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MIGS_GAME", "nosuch")
    with pytest.raises(SystemExit, match="just extract"):
        nm.main([str(tmp_path / "*.mid"), "-o", str(tmp_path / "out")])
