# CLAUDE.md

Guidance for Claude Code working in this repository.

## Goal
Extract the Roland MT-32 soundtrack of *The Secret of Monkey Island* (DOS VGA floppy, SCUMM v4) and port it to the Apple IIGS as MIDI Synth (System 6 / synthLAB) sequences.

Pipeline:
1. **Docs** (`docs/`): record format knowledge and sources before writing code that depends on it.
2. **Extract** (`src/migs/extract_music.py` → `music/`): MT-32 `RO` tracks → Standard MIDI Files in **two renditions from one parse**:
   - `music/midi/mt32/`: faithful;
   - `music/midi/gm/`: converted per `docs/mt32-to-gm.md`;
   - plus per-song patch notes (`music/patches/*.toml`).

   Keep both renditions working. Test the MT-32 one with Munt (`just mt32-wav`, user-supplied ROMs in `original files/mt-32 roms/`) and the GM one with FluidSynth + GeneralUser GS (`just gm-wav`).
3. **Convert** (`src/migs/` → `gs/`): SMF → MIDI Synth sequence ($D5/aux 1), with channels mapped to instrument slots.

## Key facts (verified against the data; details in docs/)
- Game data lives in `original files/The Secret of Monkey Island (Floppy DOS VGA)/`. ScummVM md5 `15e03ffbfeddb9c2aebc13dcb2a4a8f4` = monkey VGA, EN, DOS. **Read-only. Never commit it** (it is gitignored).
- `000.LFL` index is **not** encrypted. `DISKnn.LEC` are XOR 0x69.
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

## IIGS emulator (testing stage 3)
- Emulator: GSSquared AppImage at `vendor/gssquared/GSSquared-x86_64.AppImage` (`just fetch-gssquared`; `just emu` boots System 6 + synthLAB). CLI: `-p 5` = IIgs, `-dsXdY=img` mounts a disk, `--debug SOCK`.
- System disks: `gs/system6/*.PO` (System 6.0.4, 800K ProDOS, committed). `SYNTHLAB.PO` has synthLAB, TOOL035 (MIDI Synth), 5 stock `.BNK`/`.WAV` pairs and 7 sample `.SEQ` files in `/SEQ.AND.INSTR/`; `SYSTEM.DISK.PO` boots.
- MCP: `gs2-debug` server in `.mcp.json` → `vendor/bin/gs2-mcp` (`just fetch-mcp`) ([gssquared-mcp](https://github.com/jawaidbazyar2/gssquared-mcp) v0.1.4).
  - Call `launch {platform: "IIgs"}` first.
  - `mount` takes absolute paths.
  - After `pause` or a breakpoint, call `wait_stopped`.
  - Use `quit` to stop it, not kill.
  - Memory domains include `ENSONIQ` (DOC registers/RAM), useful for checking that instruments and waves loaded.
- Never write to `gs/system6/`. Boot from working copies in `gs/disks/` (`just disks`, gitignored).
- Disk images and ShrinkIt archives: CiderPress II CLI at `vendor/cp2/cp2` (`just fetch-cp2`).

## Conventions
- Tasks: `justfile` (run `just` to list). Key recipes:
  - `just test`: unit tests;
  - `just extract`: MT-32 + GM MIDI and patch notes into `music/`;
  - `just render`: WAVs through Munt and FluidSynth.
- Python: package `migs` in `src/migs/`, managed by uv (`pyproject.toml` + `uv.lock`). Entry points are listed in `[project.scripts]`. Add deps with `uv add`, never pip.
- Tests live in `tests/`. They use synthetic SCUMM/RO data so they run without the game. The real-game test skips when the data is absent. Add tests for every format rule you implement.
- Tools take input and output paths as arguments; no hard-coded game paths outside defaults.
- Never write into `original files/` (user-supplied: game data, MT-32 ROMs) or `ref/`.
- **Everything not under source control must be fetchable by `just setup`** (into gitignored `vendor/`, pinned versions and sha256 where available). The only exceptions are user-supplied files in `original files/`. When adding a dependency, add a `fetch-*` recipe for it, not manual steps.
- Outputs must be reproducible from the game data plus hand-maintained tables (`music/song_names.toml`, `gs/instruments.toml`). Generated music (`music/midi`, `music/patches`, `music/wav`) is gitignored: it is the copyrighted soundtrack.
- When a format fact is learned, record it in the matching `docs/*.md` file with its source.
