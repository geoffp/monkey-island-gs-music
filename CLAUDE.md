# CLAUDE.md

Guidance for Claude Code working in this repository.

## Goal
Extract the Roland MT-32 soundtrack of *The Secret of Monkey Island* (DOS VGA floppy, SCUMM v4) and port it to the Apple IIGS as **NinjaTrackerPlus** songs: the MT-32 sampled through Munt, played by the Ensoniq DOC. MIDI Synth (System 6 / synthLAB) sequences are an **experimental aside**, kept working but not the target. The tools are being readied for other SCUMM games' MT-32 soundtracks (`docs/games/README.md`); Monkey Island 1 (`monkey`, SCUMM v4) is the default; Monkey Island 2 (`monkey2`, SCUMM v5 with iMUSE, since 2026-11-11) is the second (`docs/games/monkey2.md`).

**Per game** (since 2026-11-11): settings in `config/games/<id>/` (`game.toml` plus the build configs below), output in `build/<id>/`, facts about its data in `docs/games/<id>.md`. The id is ScummVM's. `src/migs/games.py` loads them; every CLI takes `--game` (default `$MIGS_GAME`, else `monkey`); recipes use `just game=<id> <recipe>`, and the justfile exports `MIGS_GAME` so nested `just` runs follow. Paths below written `build/<game>/...` and "the game's `ntp.toml`" mean `build/monkey/...` and `config/games/monkey/ntp.toml` today. Nothing game-specific belongs in code: names (`prefix`, `label`), paths and release facts go in `game.toml`.

Pipeline:
1. **Docs** (`docs/`): record format knowledge and sources before writing code that depends on it.
2. **Extract** (`src/migs/extract_music.py` → `build/<game>/extracted/`): MT-32 `RO` tracks (v4), or iMUSE `ROL ` parts played through iMUSE's rules (v5: `src/migs/scumm_v5.py`, `src/migs/imuse.py`, `docs/imuse-format.md`; custom timbres listed in `extracted/timbres.toml`) → Standard MIDI Files in **two renditions from one parse**:
   - `build/<game>/extracted/midi/mt32/`: faithful;
   - `build/<game>/extracted/midi/gm/`: converted per `docs/mt32-to-gm.md`;
   - plus per-song patch notes (`build/<game>/extracted/patches/*.toml`).

   Keep both renditions working. Test the MT-32 one with Munt (`just mt32-wav`, user-supplied ROMs in `original files/mt-32 roms/`) and the GM one with FluidSynth + GeneralUser GS (`just gm-wav`).
3. **NinjaTrackerPlus** (`src/migs/ntpmod.py` build CLI: samples in `ntpsamples.py`, arrangement in `ntpsong.py`, MOD writer in `modfile.py`; the game's `ntp.toml` → `build/<game>/ntp/`): MT-32 SMF → ProTracker MOD within NinjaTrackerPlus limits (31 oscillators, 64 KB DOC RAM, no streaming) → `.NTP` via Ninjaforce's PHP converter (`just ntp`). The MOD is the primary artefact: preview it on the desktop (`just ntpmod`) before going to the IIGS. Sample construction research (attack detection, loop candidates, metrics): `just looplab` → `build/<game>/looplab/`, findings in `docs/sample-construction.md`.
4. **MIDI Synth (experimental aside)** (`src/migs/` → `build/<game>/seq/`): SMF → MIDI Synth sequence ($D5/aux 1), with channels mapped to instrument slots.

## Key facts about Monkey Island 1 (verified against the data; details in docs/games/monkey.md)
- Game data lives in `original files/The Secret of Monkey Island (Floppy DOS VGA)/`. ScummVM md5 `15e03ffbfeddb9c2aebc13dcb2a4a8f4` = monkey VGA, EN, DOS. **Read-only. Never commit it** (it is gitignored; `original files/README.md` lists what goes there).
- `000.LFL` index is **not** encrypted. `DISKnn.LEC` are XOR 0x69 (SCUMM v4: `docs/scumm-v4-resources.md`).
- v4 blocks are `uint32 LE size (includes 6-byte header)` + 2-char tag.
- The MT-32 music is **sounds 150–168, all in room 94 on DISK04.LEC**, stored as top-level `RO` blocks in that room's `LF`. The tag clashes with the room block `RO`. Sound offset from index `0N` is relative to the LF data start (LF offset + 8).
- Ordinary `SO` sound blocks hold only `WA` (PC speaker) and `AD` (AdLib) data. They are not the MT-32 source.
- `RO` stream format: see `docs/ro-format.md`. Reference parser: `ref/scummvm/engines/scumm/midiparser_ro.cpp`.
- The CD release (SCUMM v5) has no soundtrack MIDI (its music is Redbook audio). Don't use it as a music source.

## Reference code
`ref/scummvm` is a shallow git submodule. Most useful files:
- `engines/scumm/midiparser_ro.cpp`: `RO` event grammar
- `engines/scumm/sound.cpp`: `readSoundResourceSmallHeader` (v4 WA/AD/RO selection)
- `engines/scumm/resource_v4.cpp`: v4 index/directory reading, DISK09 Roland-patch workaround (EGA)
- `engines/scumm/imuse/`: iMUSE (MT-32 handling, SysEx)
- `engines/scumm/scumm-md5.h`: release identification

## IIGS emulator
- Emulator: GSSquared AppImage at `vendor/gssquared/GSSquared-x86_64.AppImage` (`just fetch-gssquared`).
  - **`just emu`** boots the **NinjaTrackerPlus disk**, a BazFast hard-disk image (`build/<game>/disks/MI.NTP.PO`, from `just disk`, sized to the smallest power of two that holds it, at most 32 MB; 4 MB now), straight into SenseiPlay with our songs in `/songs`. BazFast (`bazfast3`, slot 7) is GSSquared's SmartPort card with zero-cycle "Super DMA" transfers, much faster than the 800K floppies (removed 2026-11-07).
  - **Experimental aside:** `just emu-synthlab` boots `build/<game>/disks/MI.SYNTHLAB.PO` (`just synthlab-disk`, volume `/MI.SYNTHLAB`, sized the same way: 8 MB now). It merges System 6.0.4 (boot disk + SystemTools1 Finder/CDevs/DAs/FSTs/tools/fonts + SystemTools3), Tool035, `/synthLAB` with our `Seq.and.Instr`, and SenseiPlay with the NTP songs too (verified 2026-11-06 as `MI.HD.PO`).
  - **Machine configs** are generated by `gs2-config` (`migs.gs2config`) into `build/<game>/disks/<prefix>-ntp.gs2` / `<prefix>-synthlab.gs2` (lower case: `mi-ntp.gs2`). They keep the `id` and battery RAM that GSSquared writes back on quit, and patch battery RAM: Startup Slot (7 for HD, 5 for floppies), Slot 6 = "Your Card" (no 5.25" drives), and Slot 7 = "Your Card" for BazFast.
  - New configs are seeded from `config/emu/default-bram.hex`, a ROM-accepted image.
  - The battery-RAM checksum is ROM `BCheckSum`. The carry into each `ROL` is always 0 because `CPX #$FF` clears it (verified against saved battery RAM). Map: GSSquared `Docs/GSClockBatteryRAM.md`.
  - Host speed defaults to `7.159mhz` (justfile `emu_speed`, since 2026-11-11; `ludicrous` before); pass e.g. `just emu 2.8mhz` (stock) or `just emu ludicrous`. Config format: GSSquared `Docs/SystemConfigTOML.md`; storage and BazFast: `Docs/Storage.md`.
- System 6.0.4 (an unofficial community GS/OS release, not redistributable here) is fetched by `just fetch-system6` into `vendor/system6/` (Asimov archive, sha256-pinned): System.Disk, SystemTools1/3 and synthLAB, for the synthLAB disk and `stock-banks`.
- Disk images and ShrinkIt archives: CiderPress II CLI at `vendor/cp2/cp2` (`just fetch-cp2`).

## Conventions
- **Cross-platform.** We value builds that work on other people's machines. Linux is the base: everything must work there. macOS is a goal: keep it working. The BSDs are out of scope. FreeBSD was tried in CI on 2026-11-11 and dropped to keep things simple: PyPI has no numpy/scipy wheels for it, so it means 15 minutes of scipy compiling or FreeBSD-specific setup, and GSSquared and CiderPress II have no BSD builds. Windows is a maybe-someday. In practice:
  - write recipes for POSIX tools, not GNU-only flags (no `nproc`, `stat -c`, `sed -i`, `xargs -d`, `sha256sum`, bare `make -j`; use `num_cpus()`, `wc -c`, `tools/fresh.py`);
  - expect bash 3.2 (macOS);
  - choose downloads by `os()`/`arch()`, and when a tool has no build for a platform, say so and carry on rather than fail;
  - `just selftest` checks a clean-clone build (with your game data); `just smoke` checks the fetched tools run; CI (`.github/workflows/build.yml`) runs setup, tests and smoke on Ubuntu and macOS, weekly too, to catch downloads that break.
- Tasks: `justfile` (run `just` to list). Key recipes:
  - `just test`: unit tests;
  - `just extract`: MT-32 + GM MIDI and patch notes into `build/<game>/extracted/`;
  - `just render`: WAVs through Munt and FluidSynth;
  - `just ntp`: MODs and `.NTP` songs for every build variant in `build/<game>/ntp/` (`just ntpmod` stops at the MODs and desktop previews);
  - `just disk`: the NinjaTrackerPlus disk `build/<game>/disks/MI.NTP.PO`;
  - `just emu`: boot it in GSSquared.
  - `just testdisk "154 152" [variants]`: a quick test disk with only those songs (`build/<game>/disks/<prefix>.TEST.PO`, about 12 s for two songs). `ntpmod --only` still reads every song, so its MODs equal the full build's. `just emu-test` boots it. Use it to check a change on the IIGS instead of rebuilding the whole disk.
  - Experimental aside (MIDI Synth): `just seq` (sequences in `build/<game>/seq/`), `just mt32bank` (sample the MT-32 through Munt into the shared MI1 bank, spec: the game's `mt32sounds.toml`), `just synthlab-disk` (`build/<game>/disks/MI.SYNTHLAB.PO`), `just emu-synthlab`.
- Export profiles (`[profiles]` in the game's `midisynth.toml`): `stock` (COMBO, from the GM MIDI) and `mt32` (MI1, from the MT-32 MIDI). Each goes to `build/<game>/seq/<profile>/` and `/synthLAB/<Folder>` on the HD. **Keep one shared bank** for MIDI Synth: a game port can't load banks between songs. NinjaTrackerPlus is different: each `.NTP` carries its own samples, and `NTPprepare` loads them per song. So NTP builds **both** sample-set modes (`per-song`, `shared`) side by side, as the variants `inst-per-song` and `inst-shared` (`[modes.<name>]` with a `base` in the game's `ntp.toml`; output in `build/<game>/ntp/inst-per-song/`, `build/<game>/ntp/inst-shared/`), plus any trial variants, all listed in the justfile's `ntp_modes`. A game can list its own in `game.toml` `ntp_variants` (MI2 and Fate of Atlantis: `inst-per-song` only, since their 100-odd sounds can't share 31 samples; MI1 adds the trial `no-merges`; default `migs.games.NTP_VARIANTS`). The NTP builds inherit `mt32sounds.toml`'s merges (`covers`, made for the 16-slot MIDI Synth bank) unless a mode sets `merges = false` (docs/ninjatrackerplus.md, "Sound merges"). The disks have one folder per variant (`INST.PER.SONG`, `INST.SHARED`; ProDOS names: 15 characters, no `-`). Short loops are E′+ (`loop_construction = "eprime"`, code in `src/migs/loopbuild.py`); the original averaged loops stay available as `"averaged"`.
- NinjaTrackerPlus facts live in `docs/ninjatrackerplus.md`. MIDI Synth facts (the experimental aside: 7 voices, channel = instrument, file layouts) live in `docs/iigs-midisynth.md`. What each NTP build option gains and costs (fidelity, polyphony, file size) is in its "Build options: tradeoffs" section: update it when an option is added or re-measured. **Record every new MIDI Synth/synthLAB finding there, with its source.**
- Deferred plans (ideas to try later, with their background) live in `plans/`.
- Python: package `migs` in `src/migs/`, managed by uv (`pyproject.toml` + `uv.lock`). Entry points are listed in `[project.scripts]`. Add deps with `uv add`, never pip.
- Tests live in `tests/`. They use synthetic SCUMM/RO data so they run without the game. The real-game test skips when the data is absent. Add tests for every format rule you implement.
- Tools take input and output paths as arguments; their defaults come from the game (`--game`, `migs.games`), never hard-coded game paths or names.
- Never write into `original files/` (user-supplied: game data, MT-32 ROMs) or `ref/`.
- **Everything not under source control must be fetchable by `just setup`** (into gitignored `vendor/`, pinned versions and sha256 where available). The only exceptions are user-supplied files in `original files/`. When adding a dependency, add a `fetch-*` recipe for it, not manual steps.
- **Layout: tracked files are inputs, everything generated goes to `build/`.**
  - `config/`: hand-maintained settings. Per game, `games/<id>/`: `game.toml`, `midisynth.toml`, `mt32sounds.toml`, `ntp.toml`, optional `song_names.toml` (each song's title in its MOD and `.NTP`: at most 20 ASCII characters; MI1's from community track lists, `docs/games/monkey.md`), and `disk/README.TXT` (a template for the NTP disk's root README, explaining the variant folders; keep it in step with them; `just disk` fills in the song count, names and placeholders). Shared: `emu/default-bram.hex`. **Config holds only what starts a build** (decisions, thresholds, mappings); anything a build or analysis works out (which sounds the songs play, song counts and names, whether a set fits) is computed into `build/`, not written into config (`docs/games/README.md`).
  - `original files/`: user-supplied game data and MT-32 ROMs. Only its README is tracked.
  - `build/`: all generated output, gitignored. Per game, `build/<id>/`: `extracted/`, `ntp/<variant>/`, `seq/`, `disks/` (images plus the `.gs2` configs beside them), `dist/` (each disk as `<VOLUME>.zip` holding the image and `<VOLUME>.SHK`, a ShrinkIt archive of its files, made by `just disk` / `just synthlab-disk`) and `looplab/`. Shared by every game: `cache/` (`mt32samples/`, `docsim.json`: keyed by what is played, not by game or ROM) and `selftest/`. `just clean` keeps `build/cache/`; `just clean-all` removes it all.
- Outputs must be reproducible from the game data plus `config/`. Byte-for-byte holds on one CPU architecture; known differences across platforms are accepted and listed here, not fixed:
  - **arm64 vs x86_64 floating point.** numpy's arm64 builds fuse multiply-adds (one rounding instead of two), so float results can differ in the last bit. One example: `audio.interp_at` matches `np.interp` exactly on x86_64 only. MODs built on Apple silicon may therefore differ from Linux x86_64 builds in a few sample bytes. That's inaudible, but not identical. Unmeasured: CI has no game data. Found in CI, 2026-11-11.
- **Builds run at low CPU priority** (nice 10, since 2026-11-12), so audio and the emulator don't skip while they run. The bash build recipes start with `{{lowprio}}` and the one-line ones prefix `{{nice}}`. `MIGS_NICE=0` gives full speed. The `emu` recipes stay at normal priority; a new heavy recipe should use one of the two.
- `just` has no freshness tracking (every dependency recipe runs), so recipes skip themselves through `tools/fresh.py` (standard library only; usage in its docstring). `fresh get URL FILE SHA256` fetches a pinned download unless it's already there. `fresh run STAMP [--out PATH]... INPUT... -- CMD` runs CMD unless its inputs hash to the stamp, which is written only after success. It's used by `extract`, `fetch-munt`, `ntpmod` (one stamp per variant in `build/<game>/ntp/<mode>/.inputs`, inputs in the justfile's `ntp_inputs`) and `disk`. `py:FILE` inputs hash a module plus every `migs` module it imports, so new modules are picked up without editing the list. A new data file or tool that a build reads must be added to its inputs. `just ntpmod-force` rebuilds the NTP variants regardless.
- **Distribution:** never commit or publish `original files/` or `build/`. `build/` holds copyrighted derivatives of the game and ROMs: the extracted soundtrack, renders, NTP songs, sequences and disk images. Don't propose checking them in, even for CI.
- When a format fact is learned, record it in the matching `docs/*.md` file with its source.
