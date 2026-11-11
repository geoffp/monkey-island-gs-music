# Monkey Island 1 MT-32 → Apple IIGS

This project ports the Roland MT-32 soundtrack of *The Secret of Monkey Island* (LucasArts, 1990/91) to the Apple IIGS as **NinjaTrackerPlus** songs: the MT-32's own sounds, sampled through Munt, played by the Ensoniq DOC. MIDI Synth sequences (System 6.0 / synthLAB) are an experimental aside. The tools are being readied for other SCUMM games' MT-32 soundtracks too: each game has its own settings and output folder (see **Games** below); Monkey Island 1 is the only one so far.

**This repository contains no game data, music or ROMs.** You need your own copy of the game (DOS VGA floppy release) and, to sample the MT-32, your own MT-32 control and PCM ROMs. The tools read them from `original files/` and write everything derived from them to `build/`, which is never committed. *The Secret of Monkey Island* is © LucasArts / Disney; MT-32 is a trademark of Roland Corporation; Apple IIGS and GS/OS are trademarks of Apple Inc. This is an unofficial fan project, not affiliated with or endorsed by any of them.

## Approach
1. **Document** the SCUMM v4 resource format, the MT-32 `RO` music format, and the IIGS formats (`docs/`).
2. **Extract** each distinct piece of music to Standard MIDI Files in two renditions (`build/<game>/extracted/`):
   - `midi/mt32/`: faithful to the original, for an MT-32 or Munt;
   - `midi/gm/`: converted for General MIDI (rules in `docs/mt32-to-gm.md`);
   - plus a patch-notes file per song listing each channel's MT-32 and GM instrument.
3. **NinjaTrackerPlus.** Convert the MT-32 MIDI to ProTracker MODs that fit NinjaTrackerPlus's limits (up to 31 oscillators, samples packed into the 64 KB DOC RAM), preview them on the desktop, then convert them to `.NTP` with Ninjaforce's converter (`docs/ninjatrackerplus.md`).
4. **Experimental aside: MIDI Synth.** Convert the MIDI files to MIDI Synth sequences, keeping the channel→instrument mapping as far as the IIGS allows (`build/<game>/seq/`, configured in `config/games/<game>/`; `docs/iigs-midisynth.md`).

## Games
Each game has an id (ScummVM's), its settings in `config/games/<id>/` and its output in `build/<id>/`. Recipes build `monkey` unless you pick another: `just game=<id> ntp`, or set `MIGS_GAME=<id>`. How it works, adding a game, and what a second game may still need: `docs/games/README.md`.

| id | game | notes |
|---|---|---|
| `monkey` | The Secret of Monkey Island (DOS VGA floppy) | `docs/games/monkey.md` |

## Required game files
Use the **DOS VGA floppy** release of Monkey Island 1. The CD-ROM release won't work, because its music is CD audio. Put its files in `original files/The Secret of Monkey Island (Floppy DOS VGA)/`. The tools only read them, and they are not stored in git.

| file | md5 |
|---|---|
| 000.LFL | 15e03ffbfeddb9c2aebc13dcb2a4a8f4 (ScummVM: monkey, VGA, English, DOS) |
| DISK04.LEC | 8de9e90998a81c3c2b5aabd11d20aa87 (contains the MT-32 music, room 94) |

`config/games/monkey/game.toml` records these md5s, and `just extract` warns if your files differ. The EGA release keeps its MT-32 music in a separate Roland Upgrade file (`DISK09.LEC`). It is not supported here yet.

## Layout
Tracked files are inputs; everything generated goes to `build/`.
```
src/migs/          Python package (extraction, conversion, NTP, emulator tools)
tests/             unit tests (`just test`)
docs/              format notes, findings, sources
plans/             deferred plans and the backlog
config/            hand-maintained settings
  games/<id>/        one folder per game (games/monkey/ for Monkey Island 1):
    game.toml          what and where the game is (data folder, md5s) and its output names
    ntp.toml           MOD/NTP conversion (timing, sample sizes, build variants, per-song overrides)
    mt32sounds.toml    the MT-32 sounds to sample (NTP and the MIDI Synth bank)
    midisynth.toml     MIDI Synth export profiles: channel -> bank slot / drum keys
    disk/README.TXT    the NTP disk's README
    song_names.toml    names for sound ids (optional; not present yet)
  emu/default-bram.hex  battery-RAM seed for the emulator configs
original files/    YOUR copies of the game and MT-32 ROMs; not distributable, not in git (see its README.md)
ref/scummvm/       ScummVM source (git submodule, reference only)
vendor/            fetched/built tools and System 6.0.4 disk images (`just fetch`), not in git
build/             everything generated, not in git (`just clean`, `just clean-all`):
 <game>/                     one folder per game (build/monkey/), holding:
  extracted/midi/{mt32,gm}/  extracted MIDI (faithful / General MIDI)
  extracted/patches/         per-song channel -> instrument notes
  extracted/wav/{mt32,gm}/   Munt / FluidSynth renders of the songs
  seq/{stock,mt32,tune}/     MIDI Synth sequences, banks and wave files
  ntp/<variant>/             MODs, .NTP files, desktop previews (wav/), build logs
  disks/                     hard-disk images (MI.NTP.PO; experimental MI.SYNTHLAB.PO) and the GSSquared machine
                             configs (*.gs2) that boot them
  dist/                      each disk as <VOLUME>.zip (the image) and <VOLUME>.SHK (ShrinkIt archive of its files)
  looplab/                   loop-lab report, A/B WAVs and plots
 cache/mt32samples/          Munt sample renders, shared by every game (kept by `just clean`)
```

## What may be distributed
- **Tracked files** (code, docs, configs): this repository.
- **`vendor/`:** third-party tools and files, fetched by `just setup` from their own sources; we don't redistribute them. That includes the Apple IIGS System 6.0.4 disk images (an unofficial community release of Apple's GS/OS, from the [Asimov archive](https://www.apple.asimov.net/images/gs/os/gsos/Apple_IIGS_System_6.0.4/)), used only by the experimental synthLAB disk.
- **`original files/` and `build/`: never commit or share them.** The first holds the copyrighted game and ROMs. The second holds what's derived from them: the extracted soundtrack, renders, NTP songs and sequences, and disk images with the songs on them.


## Setup
Host tools: [just](https://github.com/casey/just), [uv](https://github.com/astral-sh/uv), git, curl, unzip, cmake, a C++ compiler, glib2 headers, FluidSynth, ffmpeg (with libopenmpt, for MOD previews), and PHP CLI (for Ninjaforce's MOD → NTP converter). `just doctor` checks them and prints the install command for Fedora, Debian/Ubuntu or macOS (Homebrew). On Fedora:
```sh
sudo dnf install just uv git curl unzip cmake gcc-c++ pkgconf glib2-devel fluidsynth ffmpeg php-cli
```
Then:
```sh
just setup    # checks tools, fetches the ScummVM submodule, builds the Python env, fetches and builds everything in vendor/
just          # list all recipes
just emu      # build the NTP songs and boot straight into SenseiPlay in GSSquared
              # (unlimited speed; `just emu 2.8mhz` for stock)
just emu-synthlab  # experimental aside: System 6 + synthLAB + the MIDI Synth sequences
```
`just setup` fetches into the gitignored `vendor/`. Every download is pinned to a sha256, and the unversioned ones fall back to an identical Internet Archive copy:
- GSSquared (IIgs emulator)
- gs2-mcp (its MCP server)
- CiderPress II CLI (disk images and ShrinkIt archives)
- Munt (MT-32 emulator, built from source)
- the GeneralUser GS soundfont
- synthfile 1.5 source
- NinjaTrackerPlus player/converter sources, demo disk, and the SenseiPlay player disk

### Platforms
- **Linux** (x86_64) is the base: everything works.
- **macOS** is supported. CiderPress II's macOS build is x86_64, so Apple silicon needs Rosetta 2 (`just doctor` checks).
- **BSDs** aren't supported: PyPI has no numpy or scipy wheels for them, and GSSquared and CiderPress II have no BSD builds.
- **Windows**: maybe someday.

CI (`.github/workflows/build.yml`) runs `setup`, the tests and `just smoke` on Ubuntu and macOS, on every push and weekly. `just selftest` builds the whole disk from a clean clone with your game data.

**Reproducibility:** a build is byte-for-byte reproducible on the same CPU architecture. Across architectures, floating-point rounding can differ in the last bit: numpy's arm64 builds fuse multiply-adds. So songs built on Apple silicon may differ from x86_64 builds in a few sample bytes. That's inaudible, and accepted rather than fixed.

### You must supply
These go in `original files/`, which is gitignored and never committed:
- `The Secret of Monkey Island (Floppy DOS VGA)/`: the game data (see above; a game's folder is `data` in its `game.toml`).
- `mt-32 roms/`: MT-32 control and PCM ROMs, needed only for MT-32 rendering with Munt. MT-32 v1.07 matches the period hardware; CM-32L also works. Tested with control ROM sha1 `7b8c2a5d…` (Munt: "MT-32 Control BlueRidge", a modified control ROM) and PCM sha1 `f6b1eebc…` (standard MT-32 PCM).

## Listening tests
1. **MT-32**: `just mt32-wav <mid> <wav>` renders with [Munt](https://github.com/munt/munt)'s `mt32emu-smf2wav`. Needs your ROMs. Plays `build/<game>/extracted/midi/mt32/`.
2. **General MIDI**: `just gm-wav <mid> <wav>` renders with FluidSynth and the [GeneralUser GS](https://github.com/mrbumpy409/GeneralUser-GS) soundfont. Plays `build/<game>/extracted/midi/gm/`.
3. **NinjaTrackerPlus, desktop**: `just ntpmod` writes MODs in two builds, one per sample-set mode:
   - `build/<game>/ntp/inst-per-song/`: each song carries only its own sounds, with longer loops;
   - `build/<game>/ntp/inst-shared/`: one set for every song, like a resident instrument bank;
   Short loops are built as E′+ (`loop_construction` in the game's `ntp.toml`; `"averaged"` gives the original loops). Trial variants of a mode can be built side by side (`[modes.<name>]` with a `base`, added to `ntp_modes`).

   It renders previews of each to `<mode>/wav/` (ffmpeg/libopenmpt). `just ntpmod inst-per-song` builds just one.
   A mode whose inputs (code, the game's config, MIDI, converter, justfile) haven't changed since its last successful build is skipped, and so are the `.NTP` conversion and the disk image; `just ntpmod-force` (or `just clean`) rebuilds anyway.
   `just looplab` measures attack/loop construction methods on the MT-32 samples (`build/<game>/looplab/report.md`, A/B WAVs, plots; see `docs/sample-construction.md`).
4. **NinjaTrackerPlus, IIGS**: `just emu` converts the MODs to `.NTP`, builds the disk (`just disk`) and boots straight into SenseiPlay. Open `songs`, then `INST.PER.SONG` or `INST.SHARED`, and press Return on a song.
5. **Experimental aside, MIDI Synth on the IIGS**: `just emu-synthlab` boots the synthLAB disk; open `MI.SYNTHLAB/synthLAB/synthLAB`, then a sequence from the `Stock` or `MT32` folder. It also has SenseiPlay and the NTP songs in `/SenseiPlay/MI` (launch `sensei.system`).

## Status
- [x] Repo setup, ScummVM reference submodule
- [x] Located the MT-32 music: 19 sounds (150–168), 16 distinct, in room 94
- [ ] Docs: SCUMM v4 resources, `RO` format, MT-32, MIDI Synth formats
- [x] Extract `RO` → SMF (MT-32 + GM) + patch notes (`just extract`, `just render`)
- [ ] Name the 16 tracks (`config/games/monkey/song_names.toml`)
- [x] Experimental aside: SMF → MIDI Synth sequence converter (`just seq`), synthLAB disk (`just synthlab-disk`)
- [ ] Experimental aside: play in synthLAB (`just emu-synthlab`, open `/synthLAB/MT32/MI160.SEQ`)
- [x] Custom MT-32-sampled bank MI1 (`just mt32bank`), `mt32` export profile (first build, needs listening)
- [x] MT-32 → ProTracker MOD → NinjaTrackerPlus pipeline (`just ntpmod`, `just ntp`): first build plays; known problems in `docs/ninjatrackerplus.md`
- [ ] Measure MIDI Synth CPU cost vs. tracker players (SoundSmith/NinjaTracker)
- [ ] Test on a IIGS emulator with synthLAB
- [x] Per-game settings and output folders (`config/games/<id>/`, `build/<id>/`), ready for a second game

## License
Our source code (`src/`, `tests/`, `justfile`, docs, configs) is licensed under the **GNU General Public License v3.0 or later**; see `LICENSE`.
- Tables in `src/migs/mt32.py` come from [ScummVM](https://www.scummvm.org) (GPL-2.0-or-later), used here under GPLv3.
- The MIDI Synth format code was written from Dave Tribby's synthfile 1.5 and Apple's MIDI Synth ERS (see `docs/sources.md`), with credit as synthfile's terms ask.
- Not covered by this license, and not ours: System 6.0.4 (GS/OS © Apple; fetched into `vendor/`, never committed), the game data and the music derived from it (© LucasArts/Disney), and the MT-32 ROMs (© Roland), none of which are in this repository.
