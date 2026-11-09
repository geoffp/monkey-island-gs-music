"""MT-32 bank builder pieces that don't need Munt: DOC RAM placement and conversion, and the
audio analysis the samplers share."""

import numpy as np
import pytest

from migs.audio import one_cycle, pitch_offset, refine_period
from migs.docram import Wave, allocate, ram_image, to_doc
from migs.munt import SR


def test_allocator_packs_power_of_two_waves_aligned_and_reports_overflow():
    waves = [Wave("a", bytes(4096)), Wave("b", bytes(256)), Wave("c", bytes(1024)), Wave("d", bytes(2048))]
    pages = allocate(waves, 2, 31)
    for w in waves:
        n = len(w.data) // 256
        assert pages[w.name] % n == 0 and 2 <= pages[w.name] and pages[w.name] + n - 1 <= 31
    spans = sorted((pages[w.name], pages[w.name] + len(w.data) // 256) for w in waves)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))         # no overlap
    with pytest.raises(ValueError):
        allocate([Wave("big", bytes(8192))], 2, 31)                     # 32 pages don't fit in 30


def test_doc_bytes_never_contain_zero():
    y = np.sin(np.linspace(0, 20, 1000)) * 5
    assert 0 not in to_doc(y) and max(to_doc(y)) <= 255


def test_period_and_cycle_extraction_on_a_pure_tone():
    f = 261.63
    x = np.sin(2 * np.pi * f * np.arange(SR) / SR)
    p = refine_period(x, 1000, f)
    assert p == pytest.approx(SR / f, rel=1e-3)
    cyc = one_cycle(x, 1000, p)
    assert len(cyc) == 256
    ref = np.sin(2 * np.pi * np.arange(256) / 256 + 2 * np.pi * f * 1000 / SR)
    assert np.corrcoef(cyc, ref)[0, 1] > 0.999


def test_pitch_offset_sign_lower_is_negative():
    t = np.arange(int(0.2 * SR)) / SR
    tone = lambda hz: np.sin(2 * np.pi * hz * t) * np.exp(-t * 10) + 0.5 * np.sin(4 * np.pi * hz * t) * np.exp(-t * 10)
    assert pitch_offset(tone(400), tone(400 * 2 ** (-5 / 12))) == -5
    assert pitch_offset(tone(400), tone(400 * 2 ** (3 / 12))) == 3


def test_ram_image_places_each_wave_at_its_page():
    waves = [Wave("a", bytes([1]) * 512), Wave("b", bytes([2]) * 256)]
    pages = allocate(waves, 2, 255)
    ram = ram_image(waves, pages)
    assert len(ram) == 65536 and ram[0] == 0x80
    assert ram[pages["a"] * 256:pages["a"] * 256 + 512] == bytes([1]) * 512
    assert ram[pages["b"] * 256] == 2
