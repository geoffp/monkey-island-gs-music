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

## The DOC RAM planner
Every song has to fit its instruments into the Ensoniq DOC's 64 KB of sound RAM: 256 pages of 256 bytes, of which 16 are kept free for the game's sound effects. A sample is stored in a power-of-two block. A MOD has 31 sample slots, and NinjaTrackerPlus 31 oscillators: a looped instrument plays on two (attack, then loop), a one-shot on one. The planner (`src/migs/ntpmod.py`, `ntpsamples.py`, `pitchplan.py`) decides, per song, how to spend that room so the song sounds as close to the MT-32 as it can.

**How it judges "close".** Each candidate sample is built exactly as it would ship, played through a model of the DOC, and compared with Munt's render of the same notes by **docsim 2.0** (`src/migs/docsim2.py`). That is a small auditory model: specific loudness in half-Bark bands, with ear weighting and masking. It compares the note's time shape, how each band moves (a loop's chorus or wobble), and how alive the whole sound is. The measure was chosen and tuned against the maintainer's listening verdicts, which are the only ground truth (`docs/similarity-measures.md`). Every trade below is made in its units: dB gained per DOC page.

**The steps, for the per-song set (`INST.PER.SONG`):**
1. **Which sounds.** Every MT-32 program and rhythm key the song plays. A sound may share another's sample (a merge) when its notes score at most 1 dB worse from it. Rhythm keys that the MT-32 renders identically always share, and so do drums that are one sound at another pitch (the six toms are one sample, played at three pitches).
2. **The smallest set first.** Each melodic sound starts as a 2 KB attack plus a 512 B single-cycle loop. Each drum is a one-shot. If even that doesn't fit, the least-played sounds are dropped.
3. **Pitch.** For each sound, the planner picks which key(s) to sample and at what rate (9–22 kHz), from the notes the song actually plays. A wide-ranging sound may get up to three key zones, each with its own sample.
4. **Spending the free pages.** Extra key zones, bigger (higher-rate) drum blocks, and long loops (8 or 16 KB of real sustain, so a steady sound keeps its movement) all bid for pages. Value = dB gained × the sound's share of the song's exposure ÷ pages. The best value per page wins, until nothing fits or is worth it.
   - **Exposure is listening time, weighted.** A note counts more with little else in the mix: 1 + 2/(1 + k) while k other parts sound.
   - **Loops count only the time after the attack**, so staccato notes never buy one. They also count long sustains up to 20 s, because a short loop's sameness grows the longer a note is held.
   - **Calibration by ear.** An extra zone counts half as much as a loop, and a drum half as much as a melodic sound.
5. **Taking back.** A long loop that still doesn't fit may take back upgrades bought for less per page than it: drum blocks, extra zones, or an own sample (which goes back to sharing). The set is then rebuilt, and the result is kept only if the loop fits.
6. **Finishing.** Decaying sounds that fade out within the space they'd take become one-shots. Every choice is written to the build log (`build/<game>/ntp/<variant>.log`) with its numbers: `merge`, `pitch`, `budget zone`/`budget drum`, `loop value`, and `takes back` lines. A warning flags any note whose DOC playback has much more inharmonic energy than Munt's.

**The shared set (`INST.SHARED`)** runs the same steps once for all 16 songs together, with the earlier settings: the configured merges and the older measure. Whether a game port wants the shared set at all is still open. It only pays off with a custom NTP loader that keeps the instruments in sound RAM between songs.

`just ntp` draws the result for each variant: `build/<game>/ntp/<variant>/docram.html` has one treemap per song, showing every block in DOC RAM, grouped by instrument and coloured by what it does. Hover a block for its sample, size and address. The settings are in `config/games/<game>/ntp.toml`. What each option gains and costs is in `docs/ninjatrackerplus.md` ("Build options: tradeoffs"), and the history of the current planner is in "docsim 2.0 trials" there.

## From MIDI to tracker music
NinjaTrackerPlus plays a ProTracker-style pattern grid. Each track is a column of rows, and each row's cell holds at most a note, an instrument and one effect, played out over a few ticks. There is no voice allocation: a track owns its oscillators for the whole song. So the arranger (`src/migs/ntpsong.py`) has to turn the MT-32 performance into that grid itself: when every note plays, on which track, and how loud. The details and measurements are in `docs/ninjatrackerplus.md`.

**Timing**
- **Each song gets its own grid.** The arranger fits the note-ons to the song's underlying grid unit (a 16th, or a triplet 8th). Rows fall on half that unit, and each row lasts 3 ticks.
- **Micro-timing is kept.** A note that falls between rows is delayed into place by a tick or two with `EDx`, so the music keeps its performance feel. Mean timing error is 4.6–12 ms per song.
  - A delayed note can't carry its velocity, so a note close to a row (within 20 ms) moves onto the row with its velocity in the cell. So does one whose start at the default volume would be more than 4 dB off. Neither ever lands on a neighbour from the same part, so flams and rolls survive.
- **Tempo is dithered.** MOD tempos are whole numbers, so `Fxx` switches between the two nearest tempos whenever playback drifts more than 2 ms from the ideal grid. A song that changes tempo is followed row by row.
- **Songs loop like the game.** A song that loops in the game repeats from its start: a one-row lead-in pattern, then a jump back after each pass. The pass's last row gets its own tempo, so every pass lasts the game's loop to within 2 ms. A song that plays once stops on its last row.

**Notes and tracks**
- **One note per key, as on the MT-32.** Striking a key that is still sounding ends the old note, as the MT-32's single-assign parts do.
- **A looped note takes two oscillators** (attack, then loop), and a one-shot one. Of NinjaTrackerPlus's 31, the arranger chooses each song's split between two-oscillator melodic tracks and one-oscillator drum tracks. It picks the split that loses the least: 64 points per dropped note, plus the volume of every audible cut.
- **Choosing a track for a note,** in order:
  1. a free track of its type, preferring one that last played the same sample;
  2. a new track, while oscillators remain;
  3. for a drum, an idle melodic track;
  4. a steal;
  5. dropping the note, which is logged.
- **Stealing is selective.** Only a note that is already releasing can be stolen. A silent release goes first. Then the arranger prefers a part with more than two notes sounding, so a chord loses one voice of many while bass lines, leads and duets keep their tails. Among those, it takes the quietest release, then the one nearest its end.
- **Track order matters.** Melodic tracks come before drum tracks, because Ninjaforce's converter assigns oscillators first-fit, in even pairs. The build replays that assignment and fails if a song wouldn't fit.

**Volume**
- **Loudness is measured, not guessed.** A note's level is the sample's peak × the MT-32's velocity response × its key response × its CC7 (part volume) curve, all measured through Munt. Each song is scaled so its loudest moment is volume 64.
- **Velocity goes where NinjaTrackerPlus will apply it:**
  - in the note's own cell;
  - for a delayed note on a silent drum track, in the cell before;
  - otherwise, the sample's default volume (its median in that song), corrected on the next row.
- **The envelope is redrawn row by row.** Each row's `Cxx` is the MT-32 loudness contour averaged over that row, after the attack, swells included. The contour is smoothed in decibels: over 250 ms for decaying sounds, and over at least 0.8 s for steady ones, whose finer movement is in the loop itself.
  - A held note rises at most 1.5 dB a row, because the DOC applies a new volume instantly and a beating sound would otherwise pump. Falls follow the contour.
- **Releases end cleanly.** A note-off becomes a ramp of up to 0.3 s, each row at the release's average loudness over it, so short staccato notes aren't held at full volume for a row.
  - At silence, the track switches to a silent rest sample. Otherwise NinjaTrackerPlus would briefly bring the old loop back when the next delayed note arrives.
  - A track that is still sounding is also silenced on the row before a delayed note.

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
    mt32sounds.toml    how to sample the MT-32 sounds the songs play (NTP and the MIDI Synth bank)
    midisynth.toml     MIDI Synth export profiles: channel -> bank slot / drum keys
    disk/README.TXT    template for the NTP disk's README (song count and names filled in by `just disk`)
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
  ntp/<variant>/             MODs, .NTP files, desktop previews (wav/), build logs, DOC RAM map (docram.html)
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
              # (at 7.159 MHz; `just emu 2.8mhz` for stock, `just emu ludicrous` for unlimited)
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
