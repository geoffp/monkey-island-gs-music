# original files/: user-supplied, not distributable

Everything in this folder is copyrighted material you supply yourself. It is **never committed** (this README is the only tracked file here) and **never published**. The tools only read it.

The same goes for everything under `build/` (one folder per game, `build/<game>/`, plus the shared `build/cache/`). It holds material derived from these files: the extracted soundtrack (MIDI, WAV), NinjaTrackerPlus songs, MIDI Synth sequences and banks, disk images with the songs on them, and the Munt sample renders.

## The Secret of Monkey Island: DOS VGA floppy release
Folder: `The Secret of Monkey Island (Floppy DOS VGA)/` (exact name: `data` in `config/games/monkey/game.toml`). Each game has its own folder here, named by its `game.toml` (`docs/games/README.md`).

Files: `000.LFL`, `901.LFL`–`904.LFL`, `DISK01.LEC`–`DISK04.LEC`, `MONKEY.EXE`.

| file | md5 |
|---|---|
| 000.LFL | 15e03ffbfeddb9c2aebc13dcb2a4a8f4 (ScummVM: monkey, VGA, English, DOS) |
| DISK04.LEC | 8de9e90998a81c3c2b5aabd11d20aa87 (contains the MT-32 music, room 94) |

The CD-ROM release won't work: its music is CD audio. The EGA release keeps its MT-32 music in a Roland Upgrade file (`DISK09.LEC`), which isn't supported yet.

Used by `just extract`, which warns if your files' md5s differ from the ones above (they're also in `game.toml`), and by the real-game test in `tests/test_scumm_v4.py`, which is skipped when the folder is absent.

## Roland MT-32 ROMs (for Munt)
Folder: `mt-32 roms/` (`justfile` variable `roms`).

| file | md5 (known good) |
|---|---|
| MT32_Control.ROM | 9513fec4f09a7d327748340ce3a2a59b |
| mt32_pcm.rom | 89e42e386e82e0cacb4a2704a03706ca |

Munt's `mt32emu-smf2wav` finds them by content, so other MT-32 ROM dumps it accepts should work. Different dumps may give slightly different renders.

Used by `just mt32-wav`, `just render`, `just mt32bank`, `just ntpmod` and `just looplab`. Every MT-32 sample render is a build artifact (`build/cache/mt32samples/`, shared by every game), made from these ROMs.
