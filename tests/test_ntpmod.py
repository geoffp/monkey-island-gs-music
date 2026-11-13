# SPDX-License-Identifier: GPL-3.0-or-later
"""The NTP build's configuration: NTP-only sounds, key splits, modes (ntpmod)."""

import numpy as np

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


def test_a_trial_variant_can_have_its_own_sound():
    spec = {"melodic": [{"slot": 5, "name": "Trombone", "program": 91, "covers": [90, 91, 27], "key": 60}], "drum": []}
    conf = {"melodic": [{"slot": 104, "name": "Syn Brass", "program": 27, "covers": [27], "key": 75,
                         "modes": ["per-song-trial"]}]}
    names = lambda out: {m["name"]: m["covers"] for m in out["melodic"]}
    assert names(nm.ntp_spec(spec, conf, "per-song", "per-song-trial")) == {"Trombone": [90, 91], "Syn Brass": [27]}
    assert names(nm.ntp_spec(spec, conf, "per-song", "inst-per-song")) == {"Trombone": [90, 91, 27]}
    assert names(nm.ntp_spec(spec, conf, "per-song")) == {"Trombone": [90, 91, 27]}


def test_unmerged_sounds_keep_only_their_own_program_or_key():
    from migs import sounds
    spec = {"melodic": [{"slot": 5, "name": "Trombone", "program": 91, "covers": [90, 91, 27], "key": 60}],
            "drum": [{"slot": 9, "name": "Kick", "key": 36, "covers": [35, 36], "bytes": 2048},
                     {"slot": 9, "name": "Tom", "key": 45, "covers": [41, 45], "bytes": 1024}]}
    out = nm.unmerged(spec)
    assert [(m["name"], m["covers"]) for m in out["melodic"]] == [("Trombone", [91])]
    assert [(d["key"], d["covers"], d["bytes"]) for d in out["drum"]] == [(36, [36], 2048), (35, [35], 2048),
                                                                          (45, [45], 1024), (41, [41], 1024)]
    assert len({d["name"] for d in out["drum"]}) == 4
    assert spec["melodic"][0]["covers"] == [90, 91, 27]               # the bank spec untouched
    notes = [sg.Note(t=0.0, off=1.0, key=k, vel=100, channel=0, volume=100, program=p) for p, k in ((90, 50), (27, 75))]
    done = sounds.complete(out, [(notes, {0: 90})])
    assert {m["program"]: m["key"] for m in done["melodic"]} == {91: 60, 27: 75, 90: 50}


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


def _att(name, attack):
    import numpy as np
    from migs import ntpsamples as smp
    return smp.ModSample(name, np.zeros(attack + 512), loop_start=attack, loop_len=512)


def test_attack_cuts_are_a_decision_the_build_checks():
    """`ntp_attack_bytes` shortens attacks so a set fits (ntp.toml [modes.shared]): the build
    fails when the cuts aren't enough and logs a cut that is no longer needed. A 2040 B attack
    takes a 2 KB block, a 1016 B one 1 KB; each loop here 512 B."""
    import pytest
    cfg = {"attack_bytes": 2040, "ntp_attack_bytes": {"C": 1016}}
    cut = [_att("A", 2040), _att("B", 2040), _att("C", 1016)]          # 8+2, 8+2, 4+2 pages
    lines = []
    assert nm.check_fit(cut, cfg, 25, lines.append) == 26             # 26 pages free: fits, cut needed
    assert lines == ["  smallest: 26 of 26 DOC pages with the attack cuts (C)"]
    lines.clear()
    nm.check_fit(cut, cfg, 29, lines.append)                          # 30 free: C fits at full length
    assert lines[0].startswith("  attack cut C isn't needed to fit: the set fits with its 2040 B attack")
    with pytest.raises(ValueError, match=r"need 26 DOC pages at their smallest, 24 are free .*at full length: A, B"):
        nm.check_fit(cut, cfg, 23)
    assert nm.check_fit(cut[:2], {"attack_bytes": 2040}, 25, lines.append) == 20   # no cuts: nothing to say
    organ = _att("Organ", 1016)                         # its own short attack (ntp.toml [[melodic]]): not a candidate
    with pytest.raises(ValueError, match=r"at full length: A\)"):
        nm.check_fit([_att("A", 2040), organ, cut[2]], cfg, 10)


def test_too_many_sounds_drop_the_least_played():
    """A MOD holds 31 samples (one the rest): past that, the sounds with the fewest notes go,
    their notes unplayed (MI2's sound 222 plays 37). Ties: the later entry goes first."""
    from migs.ntpmod import drop_least_used
    from migs.ntpsong import Note
    spec = {"melodic": [{"slot": 1, "name": "Bass", "program": 1, "covers": [1], "key": 40},
                        {"slot": 2, "name": "Flute", "program": 2, "covers": [2], "key": 70}],
            "drum": [{"slot": 9, "name": "Kick", "key": 36, "covers": [35, 36]},
                     {"slot": 9, "name": "Tom", "key": 45, "covers": [45]},
                     {"slot": 9, "name": "Bongo", "key": 60, "covers": [60]}]}
    n = lambda ch, key, prog=-1: Note(0.0, 0.5, key, 100, ch, program=prog)
    notes = [n(0, 40, 1)] * 3 + [n(1, 70, 2)] + [n(9, 35), n(9, 36)] + [n(9, 45)] + [n(9, 60)] * 2
    lines = []
    out = drop_least_used(spec, [(notes, {})], 3, lines.append)
    assert [m["name"] for m in out["melodic"]] == ["Bass"] and [d["name"] for d in out["drum"]] == ["Kick", "Bongo"]
    assert lines[0].startswith("  dropped Tom (1 notes)") and len(lines) == 2
    assert drop_least_used(spec, [(notes, {})], 5) is spec


def test_the_budget_finds_a_sound_that_starts_zoned_by_its_own_name():
    """A sound too wide for one sample (Fate of Atlantis's Str Sect 3: keys 27-87) has no 1-zone
    plan, so it is zoned from the start ('Str Sect 3 27', 'Str Sect 3 76'). The budget prices
    its next zone from the sound's own entry, not the zones."""
    from migs import pitchplan
    from migs.ntpmod import _value_budget, _zone_entries
    z = lambda lo, hi, key: pitchplan.Zone(lo, hi, key, 20000.0, -10.0)
    two = pitchplan.Plan((z(27, 50, 40), z(51, 87, 76)), -10.0)
    three = pitchplan.Plan((z(27, 40, 34), z(41, 60, 52), z(61, 87, 76)), -14.0)
    m = {"slot": 1, "name": "Str Sect 3", "program": 50, "covers": [50], "key": 60}
    plans = {"Str Sect 3": ([two, three], 30.0, -8.0, False)}
    assert [e["name"] for e in _zone_entries(m, two, False)] == ["Str Sect 3 40", "Str Sect 3 76"]
    cfg = {"loop_bytes": 512, "attack_bytes": 1024}
    count, _, lines = _value_budget(plans, [], {"melodic": [m], "drum": []}, cfg, 239, {"Str Sect 3": 1.0}, {})
    assert count == {"Str Sect 3": 2} and "-> 2 zones" in lines[0]


def test_a_failed_song_is_logged_and_the_others_kept(capsys):
    """One song's error doesn't stop the build (Fate of Atlantis found its bugs one full build at a time)."""
    results = [("001", "rep1", "log 1\n", None),
               ("002", None, "log 2\n", "FOA002: build failed\nTraceback ...\nKeyError: 'Str Sect 3'"),
               ("003", "rep3", "log 3\n", None)]
    reports, failed = nm._gather(iter(results))
    assert reports == [("001", "rep1"), ("003", "rep3")] and failed == [("002", "KeyError: 'Str Sect 3'")]
    out = capsys.readouterr().out
    assert out.index("log 2") < out.index("FOA002: build failed") < out.index("log 3")


def test_only_matches_song_ids_with_or_without_leading_zeros():
    """ntpmod --only (just testdisk): Atlantis's songs are 009.mid ..., asked for as 9 or 009."""
    assert nm._same_id("009", "9") and nm._same_id("009", "009") and nm._same_id("154", "154")
    assert not nm._same_id("154", "15") and not nm._same_id("154a", "154")


def test_merge_options_list_what_a_spec_merges():
    spec = {"melodic": [{"name": "Trombone", "program": 91, "covers": [90, 91, 27]},
                        {"name": "Marimba", "program": 104, "covers": [104]}],
            "drum": [{"name": "Tom", "key": 45, "covers": [41, 43, 45]}, {"name": "Kick", "key": 36, "covers": [36]}]}
    assert nm.merge_options(spec) == {"melodic": [("Trombone", 90), ("Trombone", 27)],
                                      "drum": [("Tom", 41), ("Tom", 43)]}


def test_priced_merges_keep_a_merge_only_when_it_costs_little(monkeypatch):
    """merges = "priced": a program shares the merged sound's sample when its notes score at
    most merge_max_cost_db worse from it than from their own sample; drums alike."""
    class S:
        def __init__(self, name):
            self.name, self.data, self.rate, self.seconds = name, None, 1.0, 1.0
            self.steady, self.raw_ok, self.max_loop = False, (), 512

    monkeypatch.setattr(nm, "build_melodic", lambda m, *a, **k: (S(m["name"]), ""))
    monkeypatch.setattr(nm, "build_drum", lambda d, *a, **k: (S(d["name"]), {c: (0, 1.0) for c in d["covers"]}, ""))
    monkeypatch.setattr(nm, "cached_render", lambda *a, **k: np.zeros(100))
    # score of (sample, program/key): own samples -20; the merged sound's: by how alike they are
    melodic_cost = {("Trombone", 90): -19.5, ("Trombone", 27): -15.0}
    drum_cost = {("Tom", 43): -24.8, ("Tom", 41): -10.0}

    class Scorer:
        cache = type("C", (), {"save": lambda self: None})()

        def sample_db(self, s, p, keys):
            return melodic_cost.get((s.name, p), -20.0)

    monkeypatch.setattr(nm, "docsim2_scorer", lambda munt, cfg: Scorer())
    # drum_db is called merged sample then own sample, per rhythm key in option order: the
    # merged sound's score comes from drum_cost, the own sample's is -25
    calls = [("Tom", 43), None, ("Tom", 41), None]
    monkeypatch.setattr(nm.docsim2, "drum_db", lambda ref, sr, data, rate, semis, secs: drum_cost.get(calls.pop(0), -25.0))
    spec = {"melodic": [{"name": "Trombone", "program": 91, "covers": [91], "key": 60, "slot": 5},
                        {"name": "Trombone 1", "program": 90, "covers": [90], "key": 55, "slot": 20},
                        {"name": "Syn Brass4", "program": 27, "covers": [27], "key": 72, "slot": 21}],
            "drum": [{"name": "Tom", "key": 45, "covers": [45]},
                     {"name": "High Floor Tom", "key": 43, "covers": [43]},
                     {"name": "Low Floor Tom", "key": 41, "covers": [41]}]}
    opts = {"melodic": [("Trombone", 90), ("Trombone", 27)], "drum": [("Tom", 43), ("Tom", 41)]}
    notes = [sg.Note(0.0, 1.0, 55, 100, 0, program=90), sg.Note(0.0, 1.0, 72, 100, 1, program=27),
             sg.Note(0.0, 0.1, 43, 100, sg.RHYTHM), sg.Note(0.0, 0.1, 41, 100, sg.RHYTHM)]
    lines = []
    out = nm._priced_merges(spec, opts, [(notes, {})], {"merge_max_cost_db": 1.0}, None, None, lines.append)
    names = [m["name"] for m in out["melodic"]]
    assert names == ["Trombone", "Syn Brass4"]                 # Trombone 1 merged (+0.5 dB)
    assert out["melodic"][0]["covers"] == [91, 90]             # Syn Brass4 kept (+5 dB)
    assert [d["name"] for d in out["drum"]] == ["Tom", "Low Floor Tom"]
    assert out["drum"][0]["covers"] == [45, 43]
    assert any("Syn Brass4" in l and "own sample" in l for l in lines)
