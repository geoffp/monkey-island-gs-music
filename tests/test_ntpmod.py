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
