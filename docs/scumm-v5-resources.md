# SCUMM v5 resources (Monkey Island 2, Fate of Atlantis: DOS)

What the extractor needs from a v5 game's files: the sound directory and the sound blocks.
Code: `src/migs/scumm_v5.py`. Sources: ScummVM `ref/scummvm/engines/scumm/resource.cpp`
(`readIndexFile`, `readResTypeList`), `sound.cpp` (`readSoundResource`). Checked against
Monkey Island 2 (DOS, English: `monkey2.000` md5 `3686cf8f89e102ececf4366e1d2c8126`), 2026-11-11.

## Files
| file | contents |
|---|---|
| `<game>.000` | the index: directories, XOR 0x69 |
| `<game>.001` | the data: one `LECF` block, XOR 0x69 |

Both are runs of **blocks: a 4-char tag, then a `uint32 BE` size that includes the 8-byte
header** (v4 blocks are LE with 2-char tags: `scumm-v4-resources.md`).

## Index (`.000`)
Top-level blocks in order (MI2): `RNAM`, `MAXS`, `DROO`, `DSCR`, `DSOU`, `DCOS`, `DCHR`, `DOBJ`.
A directory (`DROO`, `DSCR`, `DSOU`, `DCOS`, `DCHR`) is `uint16 LE count`, then `count` room
bytes, then `count` `uint32 LE` offsets. Room 0 = unused entry. MI2's `DSOU` has 254 entries,
199 used.

## Data (`.001`)
- `LECF` holds a `LOFF` block, then one `LFLF` per room.
- `LOFF`: a count byte, then `(uint8 room, uint32 LE offset)` per room. The offset is where
  the room's `ROOM` block starts (inside its `LFLF`).
- **A resource is at `LOFF[room] + directory offset`** (MI2: every used sound resolves).

## Sound blocks
A sound is a `SOUN` block. In MI2 it holds a `SOU ` container whose children are one per
driver: `ROL ` (MT-32), `ADL ` (AdLib), `SPK ` (PC speaker), `SBL ` (Sound Blaster digitised),
and in other releases `GMD `, `AMI `, `MAC `, `TOWS`.
- **The children's size fields exclude their 8-byte header** (`readSoundResource`:
  `size = readUint32BE() + 8`); the `SOUN` and `SOU ` sizes include it.
- ScummVM picks one child per output device by priority; for a native MT-32 it is `ROL `
  (priority 5).
- MI2: 142 of the 199 sounds have a `ROL ` part (most also `ADL `, many `SPK `); 19 are
  AdLib only, 3 Sound Blaster only, 1 PC speaker only. Two `SOUN` blocks hold an empty `SOU `
  (size 0), which the reader treats as having no parts.
- A `ROL ` part is iMUSE MIDI: `imuse-format.md`.
