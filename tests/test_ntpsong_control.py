"""Control commands (loop jump, pattern break, song end) need an effect cell on their row."""

from migs import ntpsong


def test_a_control_command_gets_a_track_of_its_own_when_every_cell_is_taken():
    # MI2's 122: on its pass's last row every track starts a note, so the loop jump had no cell
    arr = ntpsong._Arranger([], {"oscillators": 5}, {}, ntpsong.Report())
    for _ in range(2):
        t = arr.new_track("melodic")
        c = arr.grid.get(t, 10)
        c[0], c[1], c[2], c[3] = 30, 1, ntpsong.FX_VOLUME, 40     # a note start with its volume
    assert arr._overwritable(10) == 2 and arr.tracks[2].kind == "drum" and arr.osc_used == 5
    c = arr.grid.get(2, 10)
    c[2], c[3] = ntpsong.FX_JUMP, 1
    assert arr._overwritable(10) is None                         # no oscillator left: still None
