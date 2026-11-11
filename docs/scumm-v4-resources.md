# SCUMM v4 resources

The container format, as `scumm_v4.py` reads it. Verified against Monkey Island 1 (DOS VGA floppy) on 2026-11-06; that game's own facts (its files, where its music is, stale index entries, other releases) are in `games/monkey.md`. ScummVM references are under `ref/scummvm/engines/scumm/`.

## Files
| file | encryption | contents |
|---|---|---|
| `000.LFL` | none | index (directories) |
| `DISKnn.LEC` | XOR 0x69 | room data |

## Block header (v4)
`uint32 LE size` (includes the 6-byte header) + 2-byte ASCII tag.

## Index `000.LFL`
Blocks in order: `RN` (room names), `0R` rooms, `0S` scripts, `0N` sounds, `0C` costumes, `0O` objects.

The `0R`/`0S`/`0N`/`0C` directories are `uint16 count` followed by `count × { uint8 room; uint32 LE offset }`. A room of 0 marks an unused entry.

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
      RO  Roland MT-32 sound (same tag as the room block!)
```
A sound's directory offset is relative to the LF data start, i.e. `LF offset + 8`. The extractor takes every sound whose block is an `RO`, from whichever rooms hold them (Monkey Island 1 keeps all of its in one "roland" room).

**Stale index entries:** an index entry can point mid-block into other data instead of at a sound block. The extractor skips those with a warning listing them (Monkey Island 1 has 15: `games/monkey.md`).

## Sound blocks
- `SO` contains `WA` (PC speaker/PCjr) and `AD` (AdLib) sub-blocks, sometimes nested `SO`s. Both sub-blocks start `7f 00 …`. An `AD` byte at +2 of `0x80` marks music rather than an effect (`convertADResource` in `sound.cpp`).
- `RO` is a raw MT-32 event stream: see `ro-format.md`.

ScummVM picks among these in `Sound::readSoundResourceSmallHeader` (`sound.cpp`).
