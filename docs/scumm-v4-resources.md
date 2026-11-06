# SCUMM v4 resources (Monkey Island 1, DOS VGA floppy)

Verified against the game data on 2026-11-06. ScummVM references are under `ref/scummvm/engines/scumm/`.

## Files
| file | encryption | contents |
|---|---|---|
| `000.LFL` | none | index (directories) |
| `901–904.LFL` | – | charset/misc (not used here) |
| `DISK01–04.LEC` | XOR 0x69 | room data |
| `MONKEY.EXE` | LZEXE ("LZ91") packed | interpreter + drivers |

## Block header (v4)
`uint32 LE size` (includes the 6-byte header) + 2-byte ASCII tag.

## Index `000.LFL`
Blocks in order: `RN` (room names), `0R` rooms, `0S` scripts, `0N` sounds, `0C` costumes, `0O` objects.

The `0R`/`0S`/`0N`/`0C` directories are `uint16 count` followed by `count × { uint8 room; uint32 LE offset }`. Sounds: 199 entries, 124 used.

(The ScummVM code compares the tags byte-swapped, e.g. `0x4E30` = `'0N'`; see `resource_v4.cpp`.)

## Disk files `DISKnn.LEC`
```
LE  (whole file)
  FO  uint8 count, count × { uint8 room; uint32 LE offset }   room → LF offset
  LF  uint16 room number, then children:
      RO  room block (the first child; HD, CC, palettes, etc. inside)
      SC  scripts
      CO  costumes
      SO  sound (non-Roland)
      RO  Roland MT-32 sound (only in room 94; same tag as the room block!)
```
A sound's directory offset is relative to the LF data start, i.e. `LF offset + 8`. Room 94 (the "roland" room) is on DISK04.

## Sound blocks
- `SO` contains `WA` (PC speaker/PCjr) and `AD` (AdLib) sub-blocks, sometimes nested `SO`s. Both sub-blocks start `7f 00 …`. An `AD` byte at +2 of `0x80` marks music rather than an effect (`convertADResource` in `sound.cpp`).
- `RO` is a raw MT-32 event stream: see `ro-format.md`.

ScummVM picks among these in `Sound::readSoundResourceSmallHeader` (`sound.cpp`).

## Other releases (background)
- **CD-ROM** (SCUMM v5, `monkey1.000/.001`, BE size + 4-char tags `SOUN`/`SOU `/`ROL `…): the soundtrack is Redbook CD audio. The 32-byte sound resources just name CD tracks. The MIDI chunks it does have are short effects.
- **EGA floppy**: the MT-32 music is in a separate "Roland Upgrade" file, `DISK09.LEC` (room 94, sounds 150–168). See the workaround in `resource_v4.cpp`.
- **French 8-disk VGA**: the Roland room is missing (`scumm.cpp`).
