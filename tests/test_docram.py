"""DOC RAM: aligned wave placement, 8-bit conversion, the RAM image (docram)."""

import numpy as np
import pytest

from migs.docram import Wave, allocate, ram_image, to_doc


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


def test_ram_image_places_each_wave_at_its_page():
    waves = [Wave("a", bytes([1]) * 512), Wave("b", bytes([2]) * 256)]
    pages = allocate(waves, 2, 255)
    ram = ram_image(waves, pages)
    assert len(ram) == 65536 and ram[0] == 0x80
    assert ram[pages["a"] * 256:pages["a"] * 256 + 512] == bytes([1]) * 512
    assert ram[pages["b"] * 256] == 2
