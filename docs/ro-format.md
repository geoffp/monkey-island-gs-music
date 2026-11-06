# `RO` MT-32 music stream

Source: `ref/scummvm/engines/scumm/midiparser_ro.cpp` (`MidiParser_RO`). Confirmed by parsing all 19 tracks in this release.

## Layout
`uint32 LE size` + `"RO"` + event stream. ScummVM passes the data starting at the tag.

## Timing
- **120 PPQN**, initial tempo 500000 µs/quarter (120 BPM).
- Events have **no** delta-times. Delays are separate events:
  - `F0 nn`: wait nn ticks
  - `F1 nn`: wait nn + 256 ticks
  - consecutive delays add up.

## Events
| byte | meaning | params |
|---|---|---|
| `8c` | note off | key, vel |
| `9c` | note on (vel 0 = off) | key, vel |
| `Bc` | control change | ctrl, val |
| `Cc` | program change | prog |
| `Ax` | **marker** (one byte, no params). ScummVM counts them for `getTick()` (2 markers = 1 beat), apparently for script sync | – |
| `FF` | end of track, loop | – |
| `00` | end of track | – |

No running status. No SysEx: `F0` is always a delay.

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

## Open questions
- Does `MONKEY.EXE` (or its Roland driver) upload custom timbres or patch settings via SysEx at startup? That would make "program N" mean something other than the MT-32 preset. To check: unpack the LZEXE and search for `F0 41 10 16`.
- What the markers do: are they sync points for scripts, or loop points?
- Track names: identify each cue in game (room scripts that start sounds 150–168, or play them in ScummVM).
