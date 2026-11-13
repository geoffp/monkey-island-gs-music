"""DOC RAM maps (src/migs/docmap.py): the converter log's DOC placement fields
(docs/ninjatrackerplus.md, "Converter rules") and the page made from them."""
from migs import docmap as dm

# two lines of a real converter log (MI165): a melodic attack + loop pair and a drum
LOG = """NTP Instruments:
number=  1, modnumber=  1, type=%0010, streamed=no , length= 2048, volume=15, finetune= 1, docptr=$5000, docsiz=%011011, pos=$007369
number=  2, modnumber=  1, type=%0111, streamed=no , length=16384, volume=15, finetune= 1, docptr=$0000, docsiz=%110110, pos=$007B69
number= 25, modnumber=  2, type=%0000, streamed=no , length= 3340, volume=33, finetune= 0, docptr=$4000, docsiz=%100100, pos=$012969
number= 26, modnumber=  3, type=%1010, streamed=yes, length=40000, volume=33, finetune= 0, docptr=$0000, docsiz=%000000, pos=$012969
"""


def mod_with(names):
    head = bytearray(20 + 31 * 30)
    for i, n in enumerate(names):
        head[20 + i * 30:20 + i * 30 + len(n)] = n.encode()
    return bytes(head)


def test_the_size_register_holds_log2_of_the_pages():
    """ntpconverter_lib.php DOC_REGISTER_SIZES: 2**n pages -> n in bits 5-3 and 2-0."""
    for n in range(8):
        assert dm.doc_block_bytes(n << 3 | n) == 256 << n
    assert dm.doc_block_bytes(0b110110) == 16384


def test_the_type_field_names_the_part():
    """file_format.txt: type %0000zxyy, yy = 0 one-shot, 1 looped whole, 2 loop header, 3 loop."""
    assert [dm.part_of(t) for t in (0b0000, 0b0101, 0b0010, 0b0111)] == ["one-shot", "looped", "attack", "loop"]


def test_a_log_gives_each_blocks_sample_address_and_size():
    names = dm.mod_sample_names(mod_with(["Syn Brass4 75", "Snare", "Big"]))
    blocks = dm.parse_log(LOG, names)
    assert [(b.sample, b.part, b.addr, b.bytes) for b in blocks] == [
        ("Syn Brass4 75", "attack", 0x5000, 2048), ("Syn Brass4 75", "loop", 0, 16384), ("Snare", "one-shot", 0x4000, 4096)]
    # the streamed instrument has no DOC block


def test_zones_group_under_their_sound():
    assert dm.sound_of("Elec Organ 64") == "Elec Organ"
    assert dm.sound_of("Violin 2 72") == "Violin 2"
    assert dm.sound_of("Syn Brass4 69") == "Syn Brass4"
    assert dm.sound_of("Trombone 1") == "Trombone 1"                 # 1 is part of its name, not a key


def test_blocks_are_coloured_by_what_they_do():
    drums = {"Snare"}
    b = lambda name, part, size: dm.Block(name, part, 0, size, size)
    assert dm.category(b("Elec Organ 64", "loop", 16384), drums) == "long"
    assert dm.category(b("Elec Organ 64", "loop", 512), drums) == "base"
    assert dm.category(b("Elec Organ 64", "attack", 2048), drums) == "base"
    assert dm.category(b("Snare", "one-shot", 4096), drums) == "drum"
    assert dm.category(b("Xylophone", "one-shot", 2048), drums) == "shot"
    assert dm.category(b("rest", "loop", 256), drums) == "rest"


def test_the_page_carries_the_data_and_no_placeholders():
    blocks = dm.parse_log(LOG, dm.mod_sample_names(mod_with(["Syn Brass4 75", "Snare"])))
    song = dm.song_entry("165", "Voodoo Lady </script>", blocks, {"Snare"})
    page = dm.render([song], title="T & U", dek="d", foot="f", reserve_pages=16)
    assert page.startswith("<!DOCTYPE html>")
    assert "__" not in page.replace("__proto__", "") and "/*DATA*/" not in page
    assert "<title>T &amp; U</title>" in page
    assert "RESERVE = 16" in page
    assert "Voodoo Lady <\\/script>" in page                          # data can't close the script
    assert '"c":"long"' in page and '"s":"Syn Brass4"' in page
