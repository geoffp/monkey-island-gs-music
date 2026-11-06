# Apple IIGS MIDI Synth formats

Status: **research incomplete**. No free online copy of the official spec has turned up yet.

## Known so far
- MIDI Synth is Apple's integrated music system, introduced with System Software 6.0. synthLAB is the player/recorder bundled with System 6.0.
- Playback needs three files:
  - **sequence**: the notes, in a format "similar to, but just a little different from" an SMF. Each sequence names its instrument file.
  - **instrument file**: up to 16 instruments. Names its wave file.
  - **wave file**: 64 KB of DOC waveform data. Instrument and wave files are usually closely paired.
- Sequence filetype **$D5, aux type $0001**. midi2gs output had to be retyped to this before MIDI Surgeon or synthLAB would recognize it. Instrument and wave filetypes: see below.
- Existing tools:
  - **synthfile 1.5** (Dave Tribby, 1994; GNO/ORCA shell): dumps sequence, instrument and wave files as text, and `-p` plays. **Source included**: the best spec available.
  - **midi2gs**: SMF → sequence converter.
  - **MIDI Surgeon** (GS+ magazine).
  - **waveLAB**: instrument/wave editor, System 6.0.1.

## Verified from `System6.0.4/SYNTHLAB.PO` (2026-11-06)
| filetype | aux | meaning | size |
|---|---|---|---|
| $D5 | $0001 | sequence | varies |
| $D6 | $0001 | instrument bank | 5632 (all stock banks) |
| $D8 | $0004 | wave bank | 67840 (all stock banks) |

- Stock bank/wave pairs in `/SEQ.AND.INSTR/`: SYNTH, COMBO, DEMO, ORCH, PIANO (`*.BNK` + `*.WAV`).
- Sample sequences: SYNTH, BEE, CAPRI, FUGUE, MIDSUMMER, RHYTHM, SONATA (`*.SEQ`). Use them as reference input for format work.
- The MIDI Synth tool set is `/SYSTEM/TOOLS/TOOL035` (tool #35).

## To do
- Get the synthfile 1.5 source from the GNO / Caltech `/apple2/music/synthlab/` archives (see sources.md) and document the sequence, instrument and wave layouts here.
- Find the System 6.0 Programmer's Reference MIDI Synth chapter for tool calls and data structures.
- DOC limits: how many oscillators/voices are available at once under MIDI Synth, and how channels map to instruments.
- Pick a stock instrument and wave set (from synthLAB) as the first target.
