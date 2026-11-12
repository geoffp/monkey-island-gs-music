# Monkey Island 1 (`monkey`)

*The Secret of Monkey Island*, DOS VGA floppy release (SCUMM v4). Game id `monkey` (ScummVM's); settings in `config/games/monkey/`, output in `build/monkey/`. The formats themselves are in `../scumm-v4-resources.md` and `../ro-format.md`; this page holds what is true of this game only. Verified against the game data on 2026-11-06.

## Release
| file | md5 |
|---|---|
| `000.LFL` | `15e03ffbfeddb9c2aebc13dcb2a4a8f4` (ScummVM `scumm-md5.h`: monkey, VGA, English, DOS) |
| `DISK04.LEC` | `8de9e90998a81c3c2b5aabd11d20aa87` (holds the MT-32 music, room 94) |

Both are in `game.toml`'s `[md5]`; `just extract` warns when the data differs, and the real-game test checks them.

## Files
| file | encryption | contents |
|---|---|---|
| `000.LFL` | none | index (directories) |
| `901–904.LFL` | – | charset/misc (not used here) |
| `DISK01–04.LEC` | XOR 0x69 | room data |
| `MONKEY.EXE` | LZEXE ("LZ91") packed | interpreter + drivers |

## Where the music is
- The index's sound directory (`0N`) has 199 entries, 124 used.
- The MT-32 music is sounds 150–168, all `RO` blocks in room 94 (the "roland" room) on `DISK04.LEC`: 19 sounds, 16 distinct (155–157 are byte-identical to 154).
- **Stale index entries:** sounds 72, 74, 76, 78, 80, 82, 84, 86, 88, 90, 92, 94, 95, 96 and 97 point mid-block into other data in their rooms (109 other entries resolve fine). The extractor skips them with a warning. None are Roland tracks. They are probably unused leftovers; not investigated further.

## The tracks (sounds 150–168)
Durations are one pass at 120 BPM. *loop* means the track ends in `FF` (the game plays it again from the start); *end* means it ends in `00` and plays once. 12 of the 16 distinct tracks loop; 152, 154 (and its copies 155–157), 160 and 167 play once. The NTP songs do the same (`../ninjatrackerplus.md`, "Song ends and loops").

| id | bytes | dur | end | channels (program) |
|---|---|---|---|---|
| 150 | 6956 | 29.5s | loop | 2(91) 3(65) 4(104) 5(91) 10 |
| 151 | 5530 | 22.4s | loop | 2(77) 3(65) 4(9) 5(91) 10 |
| 152 | 14006 | 72.7s | end | 2(77) 3(77) 4(9) 5(104) 6(65) 10 |
| 153 | 1999 | 56.6s | loop | 2(13) |
| 154 | 1725 | 9.1s | end | 2(77) 4(9) 5(104) 6(65) 10 |
| 155–157 | – | – | – | byte-identical to 154 |
| 158 | 3684 | 22.4s | loop | 2(9) 3(104) 4(65) 10 |
| 159 | 4342 | 39.9s | loop | 3(9) 4(34) 5(65) 10 |
| 160 | 23354 | 93.6s | end | 2(77) 3(103) 4(9) 5(104) 6(65) 7(32) 8(110) 10 |
| 161 | 5147 | 25.4s | loop | 2(53) 4(97) 6(65) 10 |
| 162 | 11116 | 60.6s | loop | 2(97) 3(41) 4(103) 5(110) 6(71) 7(104) 10 |
| 163 | 8459 | 44.8s | loop | 2(110) 3(90) 4(65) 5(9) 6(78) 7(104) 10 |
| 164 | 4048 | 41.9s | loop | 3(9) 5(65) 10 |
| 165 | 3036 | 28.6s | loop | 2(107) 3(107) 4(66) 5(90) 6(27) 10 |
| 166 | 6370 | 50.6s | loop | 2(65) 3(104) 4(117) 10 |
| 167 | 7499 | 54.9s | end | 2(50) 3(92) 4(90) 5(9) 6(65) 10 |
| 168 | 6890 | 42.8s | loop | 2(92) 3(90) 4(9) 5(65) 10 |

Channels are 1-based. Every track also sends `C8 00` (program 0 on channel 9, with no notes). Programs are 0-based MT-32 **preset timbre** numbers on the MT-32's default part layout (parts 1–8 = channels 2–9, rhythm = channel 10). Controllers used: 7 (volume), 10 (pan), 1 (modulation, track 162 only).

## Names in the build (`game.toml`)
- `prefix = "MI"`: song files `MI150.mod`, `MI150.ntp`, `MI150.SEQ`; disk volumes `MI.NTP`, `MI.SYNTHLAB`; the SenseiPlay folder `/SenseiPlay/MI` on the synthLAB disk.
- `label = "MI1"`: song titles (`MI1 150` in the MOD and the sequence), MIDI track names (`MI1 sound 150`), the emulator configs' names. The MIDI Synth bank is also called MI1 (`bank` in `mt32sounds.toml`).
- Since 2026-11-12 a song with a name in `song_names.toml` is titled by it instead: the MOD's 20-byte title (so the `.NTP`'s, which copies it), the sequence's title, the MIDI track name (`MI1 sound 160: Opening Theme`) and the extracted file names (`160-opening-theme.mid`; the build takes the id from the digits before the `-`). See "Song names" below.

## Song names (2026-11-12)
The game data names no songs: the sounds are numbered, and room 94, which holds them, is called `roland`. The names come from the community's MT-32 track list, [The Legend of Monkey Island's "PC - Roland MT-32" rips](https://legendofmi.com/downloads/music/mi1/) (14 tracks).

**How they were matched.** Each rip and each of our Munt renders (`build/monkey/extracted/wav/mt32/`) was reduced to a 12-bin chroma sequence (11,025 Hz mono, 4096-point frames every 1024 samples, 60–4000 Hz, log level, each frame centred and normalised). For each pair, the score is the best mean frame correlation over every lag with at least 15 s of overlap (or the shorter one's length). The match is the highest score. A correct match is unmistakable: the rips are the same MIDI on an MT-32. Each of the 14 rips matches one sound at 0.91–1.00, and the next best is at most 0.66. 164 and 168 are not in that list. They were matched against [the same site's CD-ROM tracks](https://legendofmi.com/downloads/music/mi1/), a different recording, where correct matches score 0.53–0.81:

| id | name | rip (MT-32) | score | next best |
|---|---|---|---|---|
| 150 | LeChuck's Theme | 05 LeChuck's theme | 0.94 | 154 0.47 |
| 151 | The SCUMM Bar | 04 The Scumm Bar | 0.96 | 150 0.49 |
| 152 | The Journey | 11 The journey | 0.96 | 163 0.55 |
| 153 | Church Organ | 14 Church organ | 1.00 | 152 0.49 |
| 154 | Interlude | 03 Interlude | 0.97 | 159 0.60 |
| 158 | Introduction | 02 Introduction | 0.94 | 154 0.47 |
| 159 | Melee Island Forest | 09 Mêlée Island forest | 0.97 | 154 0.63 |
| 160 | Opening Theme | 01 Opening theme | 0.97 | 154 0.41 |
| 161 | Ghost Ship Shuffle | 13 Ghost ship shuffle | 0.98 | 166 0.31 |
| 162 | Melee Island | 07 Mêlée Island overview | 0.92 | 158 0.40 |
| 163 | Fettucini Brothers | 08 Fettucini brothers | 0.93 | 152 0.57 |
| 165 | Voodoo Lady | 06 Voodoo Lady's theme and catacombs | 0.96 | 168 0.24 |
| 166 | Monkey Island | 12 Monkey Island overview | 0.91 | 161 0.35 |
| 167 | Guybrush and Elaine | 10 Love song between Guybrush and Elaine | 0.97 | 168 0.66 |
| 168 | Closing Theme *(unconfirmed)* | CD 19 Closing theme | 0.66 | CD 11 love song 0.64 |
| 164 | Following Shopkeeper *(unconfirmed)* | CD 10 Following shopkeeper | 0.50 | — |

- **168:** it shares material with 167, and the CD's closing theme quotes the love theme, so the scores are close.
- **164** is a variant of the forest music: 164 against 159 scores 0.62. Both the CD's forest (0.69) and its shopkeeper track (0.63) match 159 better than 164 (0.51, 0.50). The name is a guess.
- Names drop accents (`Melee`): a MOD title is ASCII here (`games.parse_names`).
- **No missing songs.** Every rip matched one of our 16 sounds. The CD tracks with no MT-32 counterpart (Stan's theme, LeChuck captures Elaine, the ambiences) are CD-only.

The rips and the matching script stayed in a scratch folder; they are not part of the build.

## Other releases (background)
- **CD-ROM** (SCUMM v5, `monkey1.000/.001`, BE size + 4-char tags `SOUN`/`SOU `/`ROL `…): the soundtrack is Redbook CD audio. The 32-byte sound resources just name CD tracks. The MIDI chunks it does have are short effects.
- **EGA floppy**: the MT-32 music is in a separate "Roland Upgrade" file, `DISK09.LEC` (room 94, sounds 150–168). See the workaround in `resource_v4.cpp`.
- **French 8-disk VGA**: the Roland room is missing (`scumm.cpp`).

## Open questions
- Does `MONKEY.EXE` (or its Roland driver) upload custom timbres or patch settings via SysEx at startup? That would make "program N" mean something other than the MT-32 preset. To check: unpack the LZEXE and search for `F0 41 10 16`.
- Track names for 164 and 168 ("Song names"): confirm by ear, or find the room scripts that start them. A byte scan for `startSound` with an immediate id (`1C nn`) finds only 154 (rooms `cu-bar-1`, `cu-bar-2`) and 168 (`sh-galley`): the others are started through variables, so this needs a script disassembler (as in ScummVM's `descumm`).
