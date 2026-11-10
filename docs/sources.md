# Sources

Retrieved 2026-11-06 unless noted.

## SCUMM / Monkey Island
- ScummVM source, submodule `ref/scummvm`, commit 8abdef4a (2026-11-06): `engines/scumm/{midiparser_ro.cpp, sound.cpp, resource_v4.cpp, scumm.cpp, scumm-md5.h, imuse/}`
- ScummVM wiki, SCUMM Technical Reference / Sound resources: https://wiki.scummvm.org/index.php/SCUMM/Technical_Reference/Sound_resources. Behind Anubis bot protection; save a copy by hand from a browser.
- DOS Days, The Secret of Monkey Island (release/MT-32 history): https://www.dosdays.co.uk/topics/Games/game_monkey.php
- PCGamingWiki: https://www.pcgamingwiki.com/wiki/The_Secret_of_Monkey_Island
- Nicola Ariutti, iMUSE series: https://ariutti.github.io/articles/game_audio_series_imuse_pt1_intro/index.html

## Apple IIGS MIDI Synth
- Dave Tribby, MIDI Synth page (synthfile, overview): https://apple2.callapple.org/software/tribby/midisynth.html
- GEnie Lamp A2Pro 1994-03 (midi2gs; filetype $D5/aux 1): http://www.apple2scans.net/files/newsletters/genielamp/genielampa2pro/GLP_1994/aplp9403.asc
- KansasFest 1993, IIGS Sound (Gunn): https://www.kansasfest.org/wp-content/uploads/1993-gunn-sound.pdf
- Ground (GNO) sounds mirror: https://mirrors.apple2.org.za/ground.icaen.uiowa.edu/apple16/gno/sounds/index.html
- comp.sys.apple2, "Question on IIGS music/sound files": https://groups.google.com/d/topic/comp.sys.apple2/gtHSJf1upZE

## Apple IIGS System 6.0.4 (retrieved 2026-11-09)
- Release announcement (6.0.4, released 2016-12-31; an unofficial community GS/OS release, not Apple's): A2Central, "The Source Is Strong With This One – System 6.0.4.", https://a2central.com/2017/01/the-source-is-strong-with-this-one-system-6-0-4/. It names the Asimov archive as the download location and lists the images' MD5s.
- Images: Asimov archive, https://www.apple.asimov.net/images/gs/os/gsos/Apple_IIGS_System_6.0.4/PO%20Disk%20Images/ (`just fetch-system6` → `vendor/system6/`, sha256 pinned in the justfile). Their MD5s match the announcement.
- Until 2026-11-09 the repo tracked copies in `system6/` (removed from the history before publishing). Six were byte-identical to the archive's; `System.Disk` and `Install` differed only by Finder bookkeeping files (`Finder.Data`, `Finder.Root`, dated 2019) written when the disks were opened in the Finder.
- Status: Wikipedia, GS/OS, "Unofficial versions": https://en.wikipedia.org/wiki/GS/OS. No redistribution license was found, so the images are fetched, not tracked.

## MT-32 / General MIDI
- ScummVM `audio/mididrv.cpp` (`_mt32ToGm`, `_gmToMt32`), `audio/mt32gm.cpp` (MT-32 defaults, reversed pan), `engines/sci/sound/drivers/map-mt32-to-gm.h` (preset/rhythm names, SCI mapping, `Mt32PresetRhythmKeymap`)
- Munt MT-32 emulator: https://github.com/munt/munt (latest tag munt_2_8_3)
- *SynthLAB and MIDI Synth ERS* (APDA, Mark Cecys), OCR scan, Brutal Deluxe: https://www.brutaldeluxe.fr/documentation/synthlab.html (`just fetch-docs` → vendor/docs/)
- synthLAB User's Manual scan, same page
- synthfile 1.5 (Dave Tribby), GNO archive mirror: https://mirrors.apple2.org.za/ground.icaen.uiowa.edu/apple16/gno/sounds/synthfile15.shk (`just fetch-synthfile`)
