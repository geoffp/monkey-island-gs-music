# `RO` MT-32 music stream

Source: `ref/scummvm/engines/scumm/midiparser_ro.cpp` (`MidiParser_RO`). Confirmed by parsing all 19 tracks of Monkey Island 1 (DOS VGA floppy); its track list is in `games/monkey.md`.

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

Programs are 0-based MT-32 program numbers. A stream can't upload timbres (no SysEx), so they are the MT-32's presets unless the game's driver loads others at startup: an open question per game (`games/`).

## Open questions
- What the markers do: are they sync points for scripts, or loop points?
