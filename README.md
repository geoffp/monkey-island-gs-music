# Monkey Island 1 MT-32 → Apple IIGS

This project ports the Roland MT-32 soundtrack of *The Secret of Monkey Island* (LucasArts, 1990/91) to the Apple IIGS as **MIDI Synth** sequences (System 6.0 / synthLAB).

## Approach
1. **Document** the SCUMM v4 resource format, the MT-32 `RO` music format, and the IIGS MIDI Synth file formats (`docs/`).
2. **Extract** each distinct piece of music to Standard MIDI Files in two renditions (`music/`):
   - `midi/mt32/`: faithful to the original, for an MT-32 or Munt;
   - `midi/gm/`: converted for General MIDI (rules in `docs/mt32-to-gm.md`);
   - plus a patch-notes file per song listing each channel's MT-32 and GM instrument.
3. **Convert** the MIDI files to MIDI Synth sequences, keeping the channel→instrument mapping as far as the IIGS allows (`gs/`).

## Required game files
Use the **DOS VGA floppy** release. The CD-ROM release won't work, because its music is CD audio. Put its files in `original files/The Secret of Monkey Island (Floppy DOS VGA)/`. The tools only read them, and they are not stored in git.

| file | md5 |
|---|---|
| 000.LFL | 15e03ffbfeddb9c2aebc13dcb2a4a8f4 (ScummVM: monkey, VGA, English, DOS) |
| DISK04.LEC | 8de9e90998a81c3c2b5aabd11d20aa87 (contains the MT-32 music, room 94) |

The EGA release keeps its MT-32 music in a separate Roland Upgrade file (`DISK09.LEC`). It is not supported here yet.

## Layout
```
docs/          format notes, findings, sources
tools/         Python tools (extraction, conversion)
music/midi/mt32/  extracted SMFs, original MT-32 programs
music/midi/gm/    the same, converted to General MIDI
music/patches/ per-song channel → instrument notes (TOML)
gs/            IIGS MIDI Synth output and instrument mapping
gs/system6/    System 6.0.4 disk images (synthLAB, stock instruments)
ref/scummvm/   ScummVM source (git submodule, reference only)
vendor/        fetched/built tools (gitignored; `just fetch`)
```

## Setup
Host tools: [just](https://github.com/casey/just), [uv](https://github.com/astral-sh/uv), git, the GitHub CLI (`gh`), curl, unzip, cmake, a C++ compiler, glib2 headers, and FluidSynth. On Fedora:
```sh
sudo dnf install just uv git gh curl unzip cmake gcc-c++ pkgconf glib2-devel fluidsynth
```
Then:
```sh
just setup    # checks tools, fetches the ScummVM submodule, builds the Python env, fetches and builds everything in vendor/
just          # list all recipes
just emu      # boot the IIgs (System 6.0.4 + synthLAB) in GSSquared
```
`just setup` fetches into the gitignored `vendor/`, with pinned versions and checksums where published:
- GSSquared (IIgs emulator)
- gs2-mcp (its MCP server)
- CiderPress II CLI (disk images and ShrinkIt archives)
- Munt (MT-32 emulator, built from source)
- the GeneralUser GS soundfont
- synthfile 1.5 source

The downloads are Linux x86_64 builds for now.

### You must supply
These go in `original files/`, which is gitignored and never committed:
- `The Secret of Monkey Island (Floppy DOS VGA)/`: the game data (see above).
- `mt-32 roms/`: MT-32 control and PCM ROMs, needed only for MT-32 rendering with Munt. MT-32 v1.07 matches the period hardware; CM-32L also works. Tested with control ROM sha1 `7b8c2a5d…` (Munt: "MT-32 Control BlueRidge", a modified control ROM) and PCM sha1 `f6b1eebc…` (standard MT-32 PCM).

## Listening tests
1. **MT-32**: `just mt32-wav <mid> <wav>` renders with [Munt](https://github.com/munt/munt)'s `mt32emu-smf2wav`. Needs your ROMs. Plays `music/midi/mt32/`.
2. **General MIDI**: `just gm-wav <mid> <wav>` renders with FluidSynth and the [GeneralUser GS](https://github.com/mrbumpy409/GeneralUser-GS) soundfont. Plays `music/midi/gm/`.
3. **IIGS**: synthLAB under GSSquared (see CLAUDE.md).

## Status
- [x] Repo setup, ScummVM reference submodule
- [x] Located the MT-32 music: 19 sounds (150–168), 16 distinct, in room 94
- [ ] Docs: SCUMM v4 resources, `RO` format, MT-32, MIDI Synth formats
- [ ] Extract `RO` → SMF + patch notes
- [ ] SMF → MIDI Synth sequence converter
- [ ] Test on a IIGS emulator with synthLAB
