# Apple IIGS MIDI Synth and synthLAB: everything we know

MIDI Synth is Apple's integrated sequencer and synthesizer tool set, tool #35 (`$23`): `/SYSTEM/TOOLS/TOOL035`. It shipped with System 6.0. synthLAB is Apple's player/editor application for it.

Each fact is tagged with its source:
- **[ERS]**: *SynthLAB and MIDI Synth ERS* (APDA, Mark Cecys), `vendor/docs/apda_midisynth_synthlab_ocr.pdf` (`just fetch-docs`). Page numbers are the ERS's own: "ERS n" for the MIDI Synth part, "synthLAB n" for the synthLAB part.
- **[SF]**: Dave Tribby's synthfile 1.5 source, `vendor/synthfile/src/` (`just fetch-synthfile`): `SynthFile.h`, `GSStuff.h` (structs from Apple's `MidiSynth.h`), `synthfile.c`.
- **[DATA]**: verified by decoding the stock files on `gs/system6/SYNTHLAB.PO` `/SEQ.AND.INSTR/` (2026-11-06).
- **[GL]**: GEnie Lamp A2Pro 1994-03 (midi2gs note).

All multi-byte values are **little-endian** (65816).

## Hard limits (these drive the whole port)
- **At most 7 simultaneous voices.** [ERS 6, 12] The DOC has 32 oscillators. MIDI Synth reserves #31 (5 ms update timebase), #30 (metronome) and #28–29 ("generator 7", for the app's Sound Tool Set use). That leaves 28 oscillators, and **every voice uses 4** (2 generators × 2 oscillators), whatever the instrument. Beyond 7, MIDI Synth "steals" the least noticeable active note.
  - A note keeps its voice until **both envelopes reach zero**, i.e. through the release segments, not just until note-off. [ERS 16]
  - MI1 peaks at 7–16 simultaneous notes (see `music/patches/*.toml` `max_polyphony`).
  - **Decision:** by default the converter passes every note through and lets MIDI Synth's voice stealing cap the polyphony at playback time. It knows the real voice state, release tails included, and targets the least noticeable note. The converter only reports peak polyphony. Thinning notes in the converter is a per-song fallback, only for songs that sound bad in listening tests.
- **16 instruments per bank, and the MIDI channel *is* the instrument.** [ERS 8, 9, 24; synthLAB 12] The sequencer always plays in Multi mode: "the MIDI channel field specifies which of the 16 Synthesizer Instruments gets the MIDI message".
  - **Program Change does not change the instrument.** `$Cx` (0–15) is only passed to a call-back. [ERS 8]
  - To put a track on a particular instrument, use its channel number, or force the track's channel with `TrackToChan` (the sequence header's `t_channel`).
- **Recognized messages** [ERS 8, "Recognized MIDI Messages"]:
  - Note Off `$8x` (velocity ignored);
  - Note On `$9x` (velocity 0 = off);
  - Control Change `$Bx` for **only** CC7 Volume, CC64 Sustain (0 = off, 127 = on), and CC123–127 (All Notes Off, which forces voices into Release 1);
  - Program Change `$Cx` (call-back only);
  - Pitch Bend `$Ex` (centre MSB 64; range per instrument, 0–12 semitones);
  - `$F8`/`$FA`/`$FB`/`$FC` (sync, start, continue, stop).
- **Everything else is ignored by the synth**, including pan CC10, reverb CC91, modulation CC1, expression and aftertouch. (Such messages can still be recorded and played back to an external MIDI port.) There's **no pan control**: each wavelist has a 3-bit "Stereo" value that drives the sound expansion connector, which only matters with a third-party stereo card. A stock IIGS is mono. [ERS 21]
- **One 64 KB wave bank** of DOC RAM for all instruments. [ERS 21–22]
- **CPU:** "25–30 % CPU overhead under typical conditions". The 5 ms update interrupt costs about 150 µs. [ERS 3, 6]

## Timing
- Sequence timestamps are **always 96 ticks per quarter note**. [ERS 24–25, "Time-Stamps"]
  - `SetBeat` (`$1923`) only sets the beat call-back interval ("normalized to 96 ticks to a quarter note"). [ERS 55]
  - The header's `ticks_p_beat` (96 in every sample) is passed to `SetBeat`. [SF]
- `SetTempo` (`$1623`) takes **quarter-note BPM − 10**, 0–255, so 10–265 BPM. [ERS 54]
- The sequence-file `tempo` field is in **half-note BPM**:
  - synthLAB displays "60" for 120 quarter-note BPM [synthLAB 11];
  - synthfile calls `SetTempo((tempo << 1) − 10)` and estimates seconds as `ticks × 30 / (96 × tempo)` [SF].
  - So the file tempo range is 5–132.
- At quarter BPM 125, one tick ≈ 5 ms. [ERS 25]
- **For MI1** (120 ticks per quarter at 120 BPM, so a tick is 4.1667 ms): **keep the ticks unchanged and use file tempo 75** (quarter BPM 150), because 60 / (96 × 150) s = 4.1667 ms. That's exact, with no tick rounding.
- In-sequence tempo and meter changes are done with sequencer commands (see below).

## Sequence file (`$D5`, aux `$0001`) [SF, ERS synthLAB 38–39, DATA]
| offset | size | field |
|---|---|---|
| $000 | 4 | `"MSEQ"` |
| $004 | 2 | version, `$0100` (1.00) |
| $006 | 2 | offset of the sequence data (header size), `$01D2` |
| $008 | 16 | owner: Pascal string, e.g. `"SynthLAB"` |
| $018 | 16 | instrument (bank) file name: Pascal string, e.g. `"COMBO.BNK"`, **immediately followed by the bank's ProDOS file type and aux type** as words (`D6 00 01 00`), then padding. synthLAB writes it this way in every sample [DATA]. So bank names can be at most 11 chars |
| $028 | 16 × 16 | track names: Pascal strings, max 15 chars |
| $128 | 16 × 2 | `c_volume[16]`: initial MIDI volume per **channel/instrument** (synthfile sends CC7 on channel *i* for *i* < bank `num_inst`) |
| $148 | 16 × 2 | `t_channel[16]`: force track→channel (0–15). `$FFFF` = "thru" (keep each event's channel) |
| $168 | 16 × 2 | `t_play[16]`: `$FFFF` = play this track, 0 = muted |
| $188 | 2 | `rec_track`: record track (`$FFFF` = none) |
| $18A | 2 | `tempo`: **half-note** BPM |
| $18C | 2 | beats per measure (display) |
| $18E | 2 | beat value: 0 = half, 1 = quarter, 2 = eighth… (display only) |
| $190 | 2 | ticks per beat (passed to `SetBeat`; 96 = quarter) |
| $192 | 16 × 2 | `t_out[16]`: 0 = MIDI out + synth, 1 = MIDI only, 2 = synth only |
| $1B2 | 16 × 2 | reserved, 0 |
| $1D2 | … | Seq Items, then the end marker |

**Seq Item** (8 bytes) [ERS 24, GSStuff.h]:
| byte | field |
|---|---|
| 0 | track number 0–15 (`$FF` in bytes 0–1 = end of sequence) |
| 1 | timestamp bits 16–23 (high) |
| 2 | timestamp bits 0–7 (low) |
| 3 | timestamp bits 8–15 (mid) |
| 4 | data byte count (0–2) |
| 5 | MIDI status (high nibble = message, low = channel/instrument) |
| 6 | data byte 1 (undefined if count < 1) |
| 7 | data byte 2 (undefined if count < 2) |

- Note the odd byte order: **high, low, mid**. Timestamps are absolute, in ticks; items must be in increasing time order, since the sequencer waits until its clock reaches each item.
- **End of sequence: the two bytes `$FF $FF`** after the last item. [ERS 24, DATA: every sample file ends `ff ff`; `Capri.seq` has leftover junk items after its marker, which players ignore.] synthfile counts items as `(size − header − 2) / 8`.
- Stored data byte counts [DATA]: notes and CC = 2, program change and channel pressure = 1.
- **Sequencer commands** (status `$00`–`$7F`) [ERS 25, SF]:
  - `$00` SeqMarker: calls the app's SMarker call-back; the data bytes are free for app use.
  - `$02` SetSeqBeat: ticks per beat = data1 + data2 × 256.
  - `$04` SetRelTempo: add a signed 16-bit value (data1 low, data2 high) to the tempo.
- `F2 00 00` (Song Position 0) appears at time 0 in `Bee.seq`. [DATA]

### Stock sample sequences [DATA]
All are version 1.00, owner `SynthLAB`, header `$1D2`, 96 ticks per beat, every `t_channel = $FFFF`, `t_out = 0`, every `c_volume = 127`.
| file | bank | tempo (half BPM) | tracks used | notes |
|---|---|---|---|---|
| BEE | COMBO.BNK | 90 | 0–7 | 2/4 time |
| CAPRI | ORCH.BNK | 60 | 0 | has program changes (call-back only), CC, channel pressure |
| FUGUE | PIANO.BNK | 63 | 0–3 | |
| MIDSUMMER | ORCH.BNK | 67 | 0 | CC (volume) |
| RHYTHM | PIANO.BNK | 97 | 0, 1, 7 | |
| SONATA | PIANO.BNK | 74 | 0 | |
| SYNTH | Synth.bnk | 70 | 0 | pitch bend, CC |

## Instrument bank file (`$D6`, aux `$0001`), 5632 bytes [SF, DATA]
| offset | size | field |
|---|---|---|
| $000 | 4 | `"INST"` |
| $004 | 2 | version, `$0100` |
| $006 | 2 | header size, `$0400` |
| $008 | 16 | owner, as plain text (`SynthLAB` stores it without a length byte; the seq file's owner field does have one) |
| $018 | 16 | wave file name: Pascal string, e.g. `"COMBO.WAV"` |
| $028 | 1 | master tuning, semitones |
| $029 | 1 | master fine tune |
| $02A | 1 | (fill) |
| $02B | 1 | master volume (15 in every stock bank) |
| $02C | 1 | number of instruments defined |
| $030 | … | `INST_TOPKEYWV_OFFSET` (purpose unknown) |
| $300 | 16 × 16 | instrument names: Pascal strings |
| $400 | 16 × 288 | `InstrumentRec[16]` |

**InstrumentRec** (288 bytes) [ERS 14, GSStuff.h]: Gen 1 EnvelopeRec (16) + 8 × Gen 1 WavelistRec (16) + Gen 2 EnvelopeRec + 8 × Gen 2 WavelistRec.

**EnvelopeRec** (16 bytes) [ERS 14–17]:
- Field order: AttackLevel, AttackRate, Decay1Level, Decay1Rate, Decay2Level, Decay2Rate, SustainLevel, Decay3Rate, Release1Level, Release1Rate, Release2Level, Release2Rate, Release3Rate, DecayGain, VelocityGain, PitchBendRange.
- Ranges: levels 0–127; rates 0–31 (31 is instant, 0 is 13.08 s full scale); DecayGain 0–9 (higher notes decay faster); VelocityGain 0–10; PitchBendRange 0–12 semitones (0 disables bend and saves CPU).
- Release 3 always ends at 0. A Sustain Level of 0 means the note ends after Decay 3.

**WavelistRec** (16 bytes) [ERS 18–23]:
- Field order: TopKey, OscConfig, Stereo, Detune, WaveAddrA, WaveSizeA, VolumeA, OctaveA, SemitoneA, FineTuneA, then the same six fields for oscillator B.
- **TopKey selection:** the 8 Gen-1 wavelists are scanned in order, and the first with `TopKey >= note` is used. The same-numbered Gen-2 wavelist is used with it. A note above every TopKey is **silent**. This is how multi-sampled instruments and **drum kits** (different samples per key zone) work.
- **OscConfig:**

| value | oscillator A | oscillator B |
|---|---|---|
| 0 | loop | loop |
| 1 | one-shot | loop |
| 2 | one-shot | one-shot |
| 3 | one-shot, then | loop |
| 4 | one-shot, then | one-shot |
| 5 | one-shot ↔ | one-shot, alternating |

- **Wave address** is a DOC page ($00–$FF). **Wave size** 0–7 means 256 × 2ⁿ bytes (256 B … 32 KB), and the wave must be aligned to its size. If the metronome is used, pages $00–$01 are taken by its woodblock.
- **Tuning:** Octave 0–6, Semitone 0–11, Fine 0–63 (1/64 semitone). There's an alternate tuning table via `SetTuningTable`.

## Wave bank file (`$D8`, aux `$0004`), 67840 bytes [SF, DATA]
| offset | size | field |
|---|---|---|
| $000 | 4 | `"WAVE"` |
| $004 | 2 | version, `$0100` |
| $006 | 2 | offset of the wave data, `$0900` |
| $008 | 16 | owner |
| $018 | 1 | number of wave table entries |
| $100 | 64 × 32 | wave table: { Pascal name (16), fill, page address, fill, size code, fill[12] }. Names starting `~` are hidden/"unused" in synthfile's listing (often loop parts of multi-part waves) |
| $900 | 65536 | DOC RAM image, loaded with `WriteRamBlock(ptr, 0, $FFFF)` |

## Stock banks [DATA]
All 5 pairs are version 1.00 by `SynthLAB`, master volume 15. The `ORCH`/`PIANO` banks name their wave file in upper case (`ORCH.WAV`).
| bank | instruments (slot 0–15) |
|---|---|
| COMBO (9) | Drum Kit 1, Slap Bass, Synth Lead, Marimba, Xylophone, Flute, Trombones, Chiff Flute, Dx Marimba, Drum Kit 2 |
| DEMO (16) | Pipes, Voice, Kybrd, Dx Hpsd, Wood Flute, Analog Str, Lead Guitar, Glass, Clarinet, Guitar, Hammond, FeedBack, A Bass, Guitar 5ths, Accordian, Trumpet |
| ORCH (9) | Pan Flute, Clarinet, Harp, Hard Strings, Bassoon, Med Strings, Trombones, –, Soft Strings, Orchestra |
| PIANO (16) | Piano, Honky Tonk, Steel Piano, Piano Pluck, Hpsd, Icnc, Strange |
| SYNTH (9) | Drum Kit 3, Slap Bass, Pan Flute, Flute 1, E. Piano swell, Hammond, Brass, Trumpet 1, E. Piano, Digital Piano, High Piano, Analog Pad, "Doo" |

The `num_inst` byte doesn't always match the number of named slots: COMBO says 9 but names 10.

## Playing a sequence (what synthfile does) [SF `LoadAndPlay`, ERS 5]
1. SoundStartUp, then MIDI Synth `MSStartUp` (AppleTalk must be off).
2. Read the bank. `WriteRamBlock(wavebank + $900, 0, $FFFF)` with interrupts disabled.
3. `SetInstrument(&rec[i], i)` for i = 0–15.
4. `SetMetro(0,0,NULL)`, `SetMIDIMode(2)` (Multi), `SetBeat(ticks_p_beat)`, `SetMIDIPort(0,0)`, `SetTempo(tempo×2 − 10)`.
5. For each track: `TrackToChannel(t, t_channel[t])`, `SetPlayTrack(t, t_play[t])`, `SetTrackOut(t, t_out[t])`.
6. `KillAllNotes`, then `MIDIMessage` CC7 = `c_volume[i]` on channel i for each defined instrument.
7. `SeqPlayer` with `PbufStart` = the item buffer and `SeqFlags = $0100` (play).

## Disks and installation [DATA]
- System 6.0.4's `System.Disk` does **not** contain `Tool035`. The synthLAB disk carries it in its own `System:Tools:Tool035`. `just disks` installs it into the working copy of the boot disk (`gs/disks/SYSTEM.DISK.PO`).
- The synthLAB disk layout: `/synthLAB` (S16 `$DB02`), `Icons`, `Reference` (a GWP+ document), `System/Tools/Tool035`, `System/CDevs/MIDI`, `Seq.and.Instr/` (the banks, waves and sample sequences).
- `just gsdisk` builds `gs/disks/MI.SYNTHLAB.PO`: the synthLAB disk with `Seq.and.Instr` replaced by our `MInnn.SEQ` files and the bank/wave files they use.
- File types are set with CiderPress II NAPS suffixes (`MI160.SEQ#d50001`, `COMBO.BNK#d60001`, `COMBO.WAV#d80004`), then `cp2 add`. `cp2 list` paths use `:` separators; `cp2 delete` takes `/`.
- The stored-byte junk we ignore: leftover memory in Pascal-string padding, and unused data bytes in 1-byte messages. Semantic round trips of every sample pass (`tests/test_midisynth.py`).

## Our converter (`src/migs/mid2seq.py`, `just seq`)
- Input: `music/midi/gm/*.mid`. Output: `gs/seq/MInnn.SEQ`. Mapping: `gs/instruments.toml`.
- **Channel → slot:** each source channel's first GM program picks a bank slot (or a per-song override). Every event on that channel is rewritten onto the slot's channel. Each source channel gets its own seq track, named `"<ch> <instrument>"`.
- **Rhythm:** GM drum keys are remapped to keys inside the kit's zones (`[banks.X.drums]`); unmapped keys are dropped.
- **Kept:** notes, CC7, CC64, pitch bend. **Dropped:** program change, pan, reverb, modulation, SysEx. `c_volume[slot]` = the first CC7 value.
- **Timing:** source ticks are kept and the tempo chosen to match (MI1: tempo 75). Other sources fall back to rescaling to 96 per quarter.
- **Reports:** peak polyphony vs. the 7 voices; channels sharing a slot (they share CC7 volume and can cut each other's notes); notes outside the instrument's key zones, which play silent (none for MI1/COMBO).
- **Stock-bank limits:** COMBO has no organ, choir, strings, pad or piano. Those are mapped to Synth Lead or Dx Marimba for now; a custom MI bank is the real fix.

## Tools
- **synthfile 1.5** (Tribby): dumps all three formats and plays them under GNO/ORCA. Its C source is our reference implementation.
- **midi2gs**: SMF → sequence converter. Its output must be retyped to `$D5`/`$0001`. [GL] synthfile's `GSStuff.h` mentions `midi2gs.c`, so C source existed; we haven't located it (a cross-check candidate).
- **MidiMate** (1990, Lindsay Hough): SMF (format 1 only) → synthLAB converter.
- **MIDI Surgeon** (GS+ magazine): converter and editor.
- **CiderPress II** (`vendor/cp2/cp2`): extracts and adds files on ProDOS images with file types.

## IIGS test results (2026-11-06, GSSquared, `just emu-music`, COMBO bank)
- synthLAB opens our `MInnn.SEQ` files from `Seq.and.Instr` and plays them; it loads COMBO.BNK/.WAV from the same folder. It does **not** open a sequence by itself at startup; use File → Open.
- **Timing and tempo are correct.** Tempo 75 with unchanged 120-PPQN ticks is confirmed, and so is the half-note tempo field.
- Voice stealing is only a noticeable problem in sound 160 (16 simultaneous notes peak).
- **The first drum mapping was poor.** See "Drum kits" below.

## Drum kits [DATA]
- A kit is an ordinary instrument whose Gen-1 TopKey zones each play a different one-shot sample (`OscConfig` 2). **Within a zone the sample is pitched by the key**, so each zone has a narrow range of keys where it sounds right.
- Stock kit zones (Gen 1):
  - COMBO Drum Kit 1: Kick 0–48, Snare 49–56, Toms 57–72, Cymbal 73–78, Hi-hat 79–84, Rim 85–127.
  - COMBO Drum Kit 2: Toms 0–48 (+ Kick on B), Snare 49–56, Rim/Toms 57–72, Cymbal 73–78, Hi-hat 79–84, Cymbal/Rim 85–127.
  - SYNTH Drum Kit 3: Kick 2 0–48, Snare 2 49–56, Toms 2 57–72, Clap 73–78, Hi-hat 2 79–96.
- **Apple's own kit usage** (`SYNTH.SEQ`, channel 0 = Drum Kit 3, note-on counts): 43 ×120 (kick), 52 ×123 (snare), 60 ×6 (tom), 77 ×30 (clap), 85 ×291, 87 ×162, 89 ×3 (hi-hat). These are the best-known-good keys.
- The stock kits have no conga, bongo, claves, cabasa or agogo samples. MI1 uses those heavily: side stick 676 hits, congas 1207, claves 422, cabasa 188.
- `just drum-audition` builds `AUDITnn.SEQ` (nn = kit slot). It plays one hit per beat across a key range, so synthLAB's measure:beat counter identifies the key. `gs/seq/AUDITnn.txt` lists measure:beat → key.

## Open questions
- What the bank header bytes at `$030`–`$2FF` (`INST_TOPKEYWV_OFFSET`) hold.
- Whether MIDI Synth honours `t_channel` from the file, or only synthLAB/synthfile apply it via `TrackToChan`. (Our files will put events on the right channels directly, so it doesn't matter.)
- The voice-stealing priority rules (the ERS only says "least noticeable").
