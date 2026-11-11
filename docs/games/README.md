# Games

One page per game, named by its game id: what is true of that game's data only (release, md5s, where its music is, track list, open questions). The formats are documented once, in `../scumm-v4-resources.md` and `../ro-format.md`.

| id | game | engine | page |
|---|---|---|---|
| `monkey` | The Secret of Monkey Island (DOS VGA floppy) | scumm-v4 | `monkey.md` |

## How a game is set up (since 2026-11-11)
- **Id:** ScummVM's game id (`ref/scummvm/engines/scumm/detection_tables.h`: `monkey`, `monkey2`, `loom`, `indy3`, ...).
- **Settings:** `config/games/<id>/`, read by `src/migs/games.py`:
  - `game.toml`: `name`, `engine`, `data` (the folder under `original files/`), `prefix`, `label`, and `[md5]` for the release the settings were tuned on;
  - the build configs, as they were for Monkey Island 1 before there was a second game: `ntp.toml`, `mt32sounds.toml`, `midisynth.toml`, `disk/README.TXT` (a template), optionally `song_names.toml`.
- **Config is what starts a build; what a build works out goes to `build/`** (since 2026-11-11). A value in config is a decision (a threshold, a merge, a sampled key, a by-ear mapping), even when a measurement informed it. A value that only restates what the build or an analysis found, and that would need editing when the songs changed, isn't config. So:
  - **the sound list comes from the songs** (`src/migs/sounds.py`): every MT-32 program and rhythm key they play gets a sound. `mt32sounds.toml`'s entries are decisions about those sounds (`covers`, names, keys, sizes); one the songs play that no entry covers gets a default (its preset name, the median key played, 2 KB drums) and the build logs it. Rhythm keys the MT-32 has no sound for are skipped, as on the MT-32. Before this, such notes were silently counted as "dropped";
  - **the disk README is a template** (`src/migs/diskreadme.py`): `{songs}`, `{first}`, `{last}` and `{gaps}` come from the staged songs, and `just disk` checks the image holds exactly the placeholders the README names;
  - **attack cuts are a decision the build checks** (`ntpmod.check_fit`): `ntp_attack_bytes` says which attacks to shorten; the build fails, naming the pages missing, when they aren't enough, and logs a cut that is no longer needed.
- **Output:** `build/<id>/` (`extracted/`, `ntp/`, `seq/`, `looplab/`, `disks/`, `dist/`). The Munt render caches in `build/cache/` are shared (see the ROM limit below).
- **Choosing the game:** `just game=<id> <recipe>` or `MIGS_GAME=<id>`; the default is `monkey`. The justfile exports `MIGS_GAME`, so nested `just` runs and the Python tools' `--game` default follow it. The justfile reads `game.toml` through `tools/game.py` (standard library only, so it also runs on macOS's system Python 3.9).
- **Rules `games.parse` checks** (tests in `tests/test_games.py`):
  - `prefix`: 1–6 upper-case letters or digits, starting with a letter. ProDOS names allow 15 characters, and the longest name built from it is `<prefix>.SYNTHLAB`. Not `AUDIT`: `just seq` replaces `<prefix><digits>.SEQ` in `seq/stock/`, where the drum auditions (`AUDITnn.SEQ`) live too.
  - `label`: room for `"<label> <sound id>"` in a MOD's 20-byte title.
  - `engine`: one the extractor knows. Only `scumm-v4` (`RO` music) exists so far.

## Adding a game
1. Document its data first: a page here with its release, md5s and where its music is. Read ScummVM for how its engine version stores music.
2. `config/games/<id>/game.toml`, plus copies of Monkey Island 1's build configs as a starting point. `mt32sounds.toml` can start with no `[[melodic]]` or `[[drum]]` entries: every sound the songs play gets a default, and the build log names each one. Add entries where you decide otherwise (merging programs into one sample, a sampled key, sizes); `just extract`'s patch notes (`build/<id>/extracted/patches/`) list what each song plays. Drop Monkey Island's `[modes.shared] ntp_attack_bytes` and NTP-only `[[melodic]]` sounds: they are about its sounds. The MIDI Synth bank holds 14 melodic sounds (slots 1-15 but 9) and two kits of 8 drums (slots 0 and 9); `just mt32bank` fails, naming the sounds, when defaults don't fit.
3. Put its files in `original files/<data>/`, then `just game=<id> extract`, `ntp`, `disk`.

## Known limits for a second game (not yet needed by Monkey Island 1)
- **Other SCUMM versions.** `scumm_v4` reads v4 containers (`000.LFL`, `DISKnn.LEC`, XOR 0x69) and `ro` reads `RO` streams. Games with other layouts need their own engine first:
  - v3 games such as Loom EGA keep rooms in separate `nn.LFL` files;
  - v5 games such as Monkey Island 2 are iMUSE, with other blocks and MIDI formats (`ref/scummvm/engines/scumm/imuse/`).
- **Custom timbres.** Sounds are rendered by MT-32 program number, so they are the presets. A game whose driver uploads its own timbres by SysEx would need those sent before each render, and the cache key would have to include them.
- **The ROM set.** There is one ROM folder (`original files/mt-32 roms`). The render cache (`build/cache/mt32samples/`, `docsim.json`) is keyed by what is played (program, key, hold, velocity, volume), not by which ROMs played it. So a game that wants other ROMs (another MT-32 ROM version, or a CM-32L with its extra rhythm sounds) needs a ROM folder of its own and a cache keyed by it. Otherwise it silently reuses the current renders.
- **The NTP variants** (`ntp_modes` in the justfile) are the same for every game.
- **Shared settings.** Each game has its own copy of the NTP defaults (`[defaults]` in `ntp.toml`) and of the MT-32 sound notes (such as the Pipe Org 2 key splits, which describe the MT-32, not the game). Splitting the shared part out waits until a second game shows where the line falls.
