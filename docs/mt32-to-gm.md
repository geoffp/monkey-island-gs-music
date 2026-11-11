# MT-32 → General MIDI conversion

We keep **two** MIDI renditions of every track:
- `build/monkey/extracted/midi/mt32/`: a faithful transcription of the `RO` stream, for an MT-32 (real hardware or Munt).
- `build/monkey/extracted/midi/gm/`: converted for General MIDI players, using the rules below.

Both come from the same parse. Only the steps marked *GM* differ.

## Sources
- `ref/scummvm/audio/mididrv.cpp` `MidiDriver::_mt32ToGm[128]`: the table ScummVM itself uses for this game when an MT-32-flagged sound plays on a GM device (`engines/scumm/imuse/instrument.cpp`, `Instrument_Program::send`). **Our default.**
- `ref/scummvm/engines/sci/sound/drivers/map-mt32-to-gm.h`: SCI's hand-tuned `Mt32PresetTimbreMaps` (with preset names), `Mt32RhythmTimbreMaps`, and `Mt32PresetRhythmKeymap`. A second opinion, and our source for preset names.
- `ref/scummvm/audio/mt32gm.cpp` (`MidiDriver_MT32GM`): MT-32 power-on defaults (panning, volume), reversed pan, bend range.

## Rules
1. **Programs** (*GM*): `gm = _mt32ToGm[mt32]`. Override per song in `build/monkey/extracted/patches/*.toml` where a listening test calls for it.
2. **Rhythm** (channel 10, *GM*): MT-32 default keys 35–51, 54, 56, 60–73 and 75 map to the same GM key (`Mt32PresetRhythmKeymap`). All other keys are silent on a stock MT-32, so drop them in GM. MI1 uses one such key: **76**, a single hit.
3. **Pan** (*GM*): MT-32 pan is reversed relative to the MIDI spec, so GM gets `min(128 − v, 127)`. That keeps centre (64) at 64 (ScummVM `mt32gm.cpp`).
4. **Default pan** (*GM*): emit the MT-32 power-on panning for channels 2–9 at tick 0 (`MT32_DEFAULT_PANNING` in `mt32gm.cpp`, reversed per rule 3) unless the track sets pan itself. Every MI1 track sets pan on most channels.
5. **Pitch-bend range**: the MT-32 defaults to ±12 semitones and GM to ±2. MI1 sends **no pitch bends**, so no action is needed.
6. **Channel 9**: every track sends `C8 00` (program 0) with no notes. Harmless; keep it.
7. **Reverb** (*GM*): CC91 = 64 on every channel used, at tick 0. ScummVM `initGM` calls this the "equivalent of MT-32 default Reverb Level 5".
8. **Assumption**: program N means MT-32 *preset* timbre N. That holds only if the game doesn't upload custom patches or timbres via SysEx at startup. Still to check: `MONKEY.EXE`/its driver (see `ro-format.md`, open questions). If it does, the MT-32 rendition must include that SysEx too.

## Programs used by the MI1 soundtrack (0-based)
| MT-32 | preset name | GM (SCUMM) | GM (SCI) | GM name (SCUMM choice) |
|---|---|---|---|---|
| 0 | AcouPiano1 | 0 | 0 | Acoustic Grand Piano |
| 9 | Elec Org 2 | 17 | 17 | Percussive Organ |
| 13 | Pipe Org 2 | 19 | 19 | Church Organ |
| 27 | Syn Brass4 | 63 | 63 | Synth Brass 2 |
| 32 | Fantasy | 88 | 88 | Pad 1 (new age) |
| 34 | Chorale | 52 | 52 | Choir Aahs |
| 41 | Ice Rain | **96** | **101** | FX 1 (rain) / SCI: FX 6 (goblins) |
| 50 | Str Sect 3 | 49 | 49 | String Ensemble 2 |
| 53 | Violin 2 | 40 | 40 | Violin |
| 65 | Acou Bass2 | **32** | **33** | Acoustic Bass / SCI: Electric Bass (finger) |
| 66 | Elec Bass1 | 34 | 34 | Electric Bass (pick) |
| 71 | Fretless 2 | 35 | 35 | Fretless Bass |
| 77 | Panpipes | 75 | 75 | Pan Flute |
| 78 | Sax 1 | 64 | 64 | Soprano Sax |
| 90 | Trombone 1 | 57 | 57 | Trombone |
| 91 | Trombone 2 | 57 | 57 | Trombone |
| 92 | Fr Horn 1 | 60 | 60 | French Horn |
| 97 | Vibe 1 | 11 | 11 | Vibraphone |
| 103 | Xylophone | 13 | 13 | Xylophone |
| 104 | Marimba | 12 | 12 | Marimba |
| 107 | Shakuhachi | 77 | 77 | Shakuhachi |
| 110 | BottleBlow | 76 | 76 | Blown Bottle |
| 117 | Taiko | 116 | 116 | Taiko Drum |

## Rhythm keys used (channel 10, counts across all 16 distinct tracks)
35(1) 36(834) 37(676) 38(43) 41(70) 42(226) 43(15) 44(36) 45(16) 47(12) 48(20) 50(3) 60(42) 61(120) 62(523) 63(684) 64(76) 66(2) 67(61) 68(55) 69(188) 73(3) 75(422) **76(1, unmapped)**

Rough approximations under GM:
- 73 is Quijada on the MT-32 but Short Guiro in GM.
- 67/68 are Agogos in both, but they sound different.
- 62–64 congas and 60–61 bongos map one-to-one.

## Implementation
`src/migs/mt32.py` holds the tables, and `src/migs/extract_music.py` (`_gm_transform`, `_gm_channel_defaults`) holds the rules. Both renditions are written as format-1 SMF at 120 PPQN:
- track 0 is the conductor: tempo, a GM System On SysEx (GM only), `loopStart`/`loopEnd` markers for looping songs, and `A<n>` markers for the RO sync markers;
- then one track per MIDI channel.
