"""RO stream parser: timing, event decoding, termination."""

import pytest

from migs import ro


def kinds(track):
    return [(e.tick, e.kind, e.channel, e.a, e.b) for e in track.events]


def test_delays_accumulate_and_apply_to_next_event():
    # 10 + 20 ticks, F1 adds 256 extra, then a program change and a note.
    t = ro.parse(bytes([0xF0, 10, 0xF0, 20, 0xC1, 77, 0xF1, 4, 0x91, 60, 100, 0x00]))
    assert kinds(t) == [(30, "program", 1, 77, 0), (30 + 256 + 4, "note_on", 1, 60, 100)]


def test_note_on_velocity_zero_is_note_off():
    t = ro.parse(bytes([0x92, 64, 90, 0xF0, 5, 0x92, 64, 0, 0x82, 65, 40, 0x00]))
    assert kinds(t) == [
        (0, "note_on", 2, 64, 90),
        (5, "note_off", 2, 64, 0),
        (5, "note_off", 2, 65, 40),
    ]


def test_marker_is_one_byte_and_does_not_consume_params():
    t = ro.parse(bytes([0xA0, 0xB1, 7, 127, 0x00]))
    assert kinds(t) == [(0, "marker", 0, 0, 0), (0, "cc", 1, 7, 127)]


def test_ff_loops_and_end_tick_includes_trailing_delay():
    t = ro.parse(bytes([0x91, 60, 100, 0xF0, 120, 0x81, 60, 0, 0xF0, 60, 0xFF, 0x99, 1, 1]))
    assert t.loops and t.terminated
    assert t.end_tick == 180          # the delay before FF counts toward loop length
    assert len(t.events) == 2         # nothing after the terminator is parsed


def test_00_ends_without_loop():
    t = ro.parse(bytes([0xF0, 7, 0x00]))
    assert t.terminated and not t.loops and t.end_tick == 7


def test_unterminated_stream_runs_to_end():
    t = ro.parse(bytes([0x91, 60, 100, 0xF0, 9]))
    assert not t.terminated and not t.loops and t.end_tick == 9


@pytest.mark.parametrize("data", [
    bytes([0x40, 0x00]),        # data byte without status (no running status in RO)
    bytes([0xE1, 0x00, 0x40]),  # pitch bend is not part of the grammar
    bytes([0x91, 60]),          # truncated note
])
def test_malformed_streams_raise(data):
    with pytest.raises(ValueError):
        ro.parse(data)


def test_event_kinds_compare_as_their_names_and_ticks_convert_at_mi1_tempo():
    t = ro.parse(bytes([0x91, 60, 100, 0xF0, 120, 0x81, 60, 0, 0x00]))
    assert [e.kind for e in t.events] == ["note_on", "note_off"]
    assert t.events[0].kind is ro.Kind.NOTE_ON
    assert ro.ticks_to_seconds(t.end_tick) == 0.5               # 120 ticks per quarter at 120 BPM
