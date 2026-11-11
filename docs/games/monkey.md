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
Durations are one pass at 120 BPM. *loop* means the track ends in `FF`; *end* means it runs to the end of the block.

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

## Other releases (background)
- **CD-ROM** (SCUMM v5, `monkey1.000/.001`, BE size + 4-char tags `SOUN`/`SOU `/`ROL `…): the soundtrack is Redbook CD audio. The 32-byte sound resources just name CD tracks. The MIDI chunks it does have are short effects.
- **EGA floppy**: the MT-32 music is in a separate "Roland Upgrade" file, `DISK09.LEC` (room 94, sounds 150–168). See the workaround in `resource_v4.cpp`.
- **French 8-disk VGA**: the Roland room is missing (`scumm.cpp`).

## Open questions
- Does `MONKEY.EXE` (or its Roland driver) upload custom timbres or patch settings via SysEx at startup? That would make "program N" mean something other than the MT-32 preset. To check: unpack the LZEXE and search for `F0 41 10 16`.
- Track names: identify each cue in game (room scripts that start sounds 150–168, or play them in ScummVM).
