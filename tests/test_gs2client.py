"""Ensoniq state-blob parsing for the DOC scope (no emulator needed)."""

import struct

import pytest

from migs import gs2client as g


def _blob(**osc3) -> bytes:
    b = bytearray(g.BLOB_SIZE)
    b[10] = 32
    struct.pack_into("<I", b, 12, 26320)
    at = g.HEADER_SIZE + 3 * g.RECORD_SIZE
    struct.pack_into("<HHBB", b, at, osc3["freq"], 0, osc3["control"], osc3["vol"])
    struct.pack_into("<I", b, at + 8, 0x1200)
    b[at + 12], b[at + 13] = 2, 5
    struct.pack_into("<I", b, at + 16, 0xABCDEF)
    return bytes(b)


def test_parse_ensoniq_reads_header_and_records():
    hdr, oscs = g.parse_ensoniq(_blob(freq=2197, control=0, vol=200))
    assert hdr == {"oscs_enabled": 32, "output_rate": 26320} and len(oscs) == 32
    o = oscs[3]
    assert (o.osc, o.freq, o.vol, o.ptr, o.size, o.res, o.acc) == (3, 2197, 200, 0x1200, 2, 5, 0xABCDEF)
    assert o.running and not g.parse_ensoniq(_blob(freq=1, control=1, vol=0))[1][3].running


def test_pass_rate_matches_the_measured_a4():
    # docs/iigs-midisynth.md: freq 2197 at resolution 0 plays a 256 B single cycle at ~441 Hz
    assert g.pass_hz(26320, 2197, 0) == pytest.approx(441.2, abs=0.1)


def test_short_blobs_are_rejected_not_misread():
    assert g.BLOB_RE.search("x " + "ab" * (g.BLOB_SIZE - 1) + " y") is None
    with pytest.raises(ValueError):
        g.parse_ensoniq(bytes(g.BLOB_SIZE - 1))
