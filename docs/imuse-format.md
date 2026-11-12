# iMUSE MT-32 songs (SCUMM v5: Monkey Island 2, Fate of Atlantis)

How a `ROL ` sound part (`scumm-v5-resources.md`) plays, and how `src/migs/imuse.py` turns it
into one linear event stream for the extractor. Sources: ScummVM
`ref/scummvm/engines/scumm/imuse/` (`imuse_player.cpp`, `imuse_part.cpp`, `sysex_scumm.cpp`,
`instrument.cpp`, `drivers/midi.cpp`). Checked against Monkey Island 2 (DOS) on 2026-11-11:
every one of its 142 `ROL ` parts reads.

## Container
`MDhd` (8 bytes: priority and flags; not needed here), then a Standard MIDI File.
- **Format 2, 480 ticks per beat** (`TICKS_PER_BEAT`), one tempo meta at the start (MI2: four
  music sounds change tempo: 154, 159, 211, 222).
- Several `MTrk` chunks are separate *sections*, reached by jumps; play starts at track 0.
- mido can't read four MI2 files (an SMPTE-offset meta with frame-rate code 4), so the
  module has its own tolerant SMF reader (`read_smf`, running status supported).

## Parts
A MIDI channel is an iMUSE *part* (`Part`). Defaults (`Part::setup`): on, volume 127, pan
centre, no transpose, percussion only on channel 9.
- `F0 7D 00 cc <nibbles>`, **allocate part** (`sysex_scumm.cpp` case 0): after the channel
  byte, each data byte is sent as two nibbles (`decode_sysex_bytes`). Decoded: on/reverb
  flags, priority, volume, pan (signed), transpose (bit 7 = percussion; 0x80 = "none"),
  detune, pitch-bend factor, program.
- Note numbers get `part transpose + song transpose`, clamped to +-12 (the MI2/Indy4 MT-32
  driver's limits). Percussion parts aren't transposed.
- **Percussion parts play on the rhythm part** whatever their channel (MI2 has them on 9-14),
  and a rhythm note carries its part's volume (`Part::noteOn`: `_rhyState.vol`).
- `7D 01` shuts a part down.
- Program changes are normal `Cn pp`; they select the MT-32 preset (the driver writes patch
  timbre group `pp >> 6`, number `pp & 63`, i.e. the preset of that number).
- 30 MI2 channel-tracks change program mid-song: a note plays the program its channel has at
  its onset (`ntpsong.Note.program`), not the channel's first.

## Custom timbres
`F0 41 pp 16 12 aa aa aa <246 bytes> cs F7`: a Roland DT1 of one whole MT-32 timbre (common
+ 4 partials) for part `pp` (the device-id byte carries the iMUSE part). The driver writes it
to the timbre temporary area of the hardware part playing that iMUSE part
(`IMuseChannel_MT32::sysEx_customInstrument`), so the part plays it until its next program
change.
- MI2 has **20 distinct custom timbres**, all addressed 04 00 00. Seven are musical:
  ReggaeBass (in 42 sounds), Big Drum, Fire Pit, LeshBass, Swell 1, Swell 2, SdTrk Bend; the
  rest are effects (badspit, burp, foghorn, glop, thnderclap, ...) in sounds below 100.
- The extractor gives each timbre an id from 128 (sorted by name, then content) and lists
  them in `build/<game>/extracted/timbres.toml`. In the MT-32 MIDI a timbre change is a text
  event `timbre ch<c> <id> <name>` (which `ntpsong.read_notes` reads) followed by the DT1 to
  that part's temporary area (04 00 00 + 246 bytes per part: part 2 is 04 01 76). The NTP
  build renders a custom timbre in Munt by sending the same DT1 to part 1 instead of a
  program change (`munt._note_file`), and names its cache entries by the timbre's content
  (`munt.sound_tag`), never by the id, which is a game's own numbering.
- GM equivalents (for the GM rendition) are by name: ScummVM's `roland_to_gm_map`
  (`instrument.cpp`), in the game's `game.toml` `[mt32] timbre_gm`.

## The driver's start-up SysEx
`IMuseDriver_MT32::initDevice` ("old system", MI2 and Indy4) sends, besides a display message
and a reset:
1. system area 10 00 00: master tune, reverb mode 0 / time 4 / level 4, partial reserve 4
   per melodic part and 0 for rhythm, parts 1-8 on channels 2-9 and rhythm on 10 (the
   MT-32's defaults), master volume 100;
2. **rhythm setup 03 01 10: keys 24-34 play rhythm timbres 0x40-0x51**, which the MT-32's
   power-on map leaves silent; MI2's songs play them;
3. bender range 16 in all 128 patches (pitch bends aren't followed here).

Items 1 and 2 are in `config/games/monkey2/game.toml` `[mt32] init`; every render sends
them first, and `mt32.rhythm_keys` reads the extra rhythm keys from them.

## Hooks, jumps and loops
- `7D 30` **hook jump**: decoded `(hook id, track, beat, tick)`; the target is track `track`
  at tick `(beat - 1) * 480 + tick` (`Player::jump`). A non-zero hook id waits for a script to
  set that hook (`maybe_jump`); id 0 always jumps. A jump stops the sounding notes
  (`MidiParser::jumpToTick`, `stopNotes`).
- `7D 31`-`35` hooks: song transpose, part on/off, part volume, part program, part
  transpose. The same rule: id 0 applies at once, others wait for scripts. MI2 has 143
  unconditional part on/off hooks, which switch parts during a song.
- `7D 50` **counted loop** `(count, to beat, to tick, from beat, from tick)`: when play
  reaches *from*, it jumps back to *to*, `count` times (`Player::onTimer`). MI2's counts are
  100 or 1000.
- `7D 40` markers are for scripts.

**What the reader plays:** the song as it starts with no hooks set by scripts, following
the unconditional jumps (forward jumps skip; MI2 often skips a short count-in at the start).
A jump **back into what has already played is the song's loop**: `loop_start` is the target's
output tick, `end_tick` the jump's. A counted loop of `FOREVER` (2) passes or more is the
loop the same way. A song whose track ends (`FF 2F`) plays once. MI2's 97 music sounds: 64
loop, 33 play once.

## Not followed
- **Pitch bend** (16,193 events in MI2's sounds): the NTP pipeline plays no pitch bends.
- Detune, the pitch-bend factor, modulation and the effect level.
- Hooks that wait for scripts (1,071 jumps, 120 other hooks in MI2): the game's scripts set
  them as the player moves about (MI2's Woodtick song, sound 100, switches tracks per
  building). What is extracted is the song before any such change.
- iMUSE's MT-32 part allocation by priority: the MT-32 has 8 melodic parts, iMUSE songs use
  up to 15 channels. The reader keeps every part and puts them in order of first note onto
  MIDI channels 2-9 (MT-32 parts 1-8), then 11-16 (`remap_channels`); a whole-song MT-32
  render hears only the first eight, the NTP build plays them all.
