"""NinjaTrackerPlus .NTP section parsing and the shared-bank check."""

import pytest

from migs import ntpfile


def _ntp(name: bytes, vol: int, pattern_byte: int, samples: bytes = b"\x80" * 300) -> bytes:
    tracks, npat = 2, 1
    d = b"nfc!" + bytes([2, tracks, 1, npat, 1]) + bytes([len(name)]) + name
    d += bytes([0, 0, 0x80, 1, 0, 2])                                     # 2 tracks x 3 bytes
    ln = len(samples).to_bytes(3, "little")
    d += bytes([0]) + ln + b"\0\0\0" + b"\0\0\0" + bytes([vol, 0, 0, 0x09]) + bytes([3]) + b"abc"
    d += bytes([0])                                                        # order
    d += bytes([pattern_byte]) * (4 * tracks * 64 * npat)
    return d + samples


def test_sections_round_trip():
    raw = _ntp(b"MI1 150", 40, 7)
    x = ntpfile.parse(raw)
    assert (x.version, x.tracks, len(x.instruments), x.patterns) == (2, 2, 1, 1)
    assert x.header + x.track_bytes + x.instrument_bytes + x.order + x.pattern_bytes + x.samples == raw
    assert x.instruments[0][10] == 40 and len(x.samples) == 300
    assert x.song_part == x.header + x.track_bytes + x.order + x.pattern_bytes


def test_truncated_or_foreign_files_are_rejected():
    with pytest.raises(ValueError):
        ntpfile.parse(b"MThd" + bytes(20))
    with pytest.raises(ValueError):
        ntpfile.parse(_ntp(b"x", 40, 0)[:-10])


def test_shared_bank_check(tmp_path):
    a, b, c = tmp_path / "a.ntp", tmp_path / "b.ntp", tmp_path / "c.ntp"
    a.write_bytes(_ntp(b"song a", 40, 1))
    b.write_bytes(_ntp(b"song bb", 40, 2))          # different song: same bank
    assert ntpfile.check_shared([a, b]) == []
    c.write_bytes(_ntp(b"song c", 41, 3))           # a per-song default volume breaks the bank
    assert any("instrument" in p for p in ntpfile.check_shared([a, c]))
