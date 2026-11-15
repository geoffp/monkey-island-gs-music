# DOC volume sweep recordings

Recordings of the DOC volume sweep (`just docsweep`; docs/es5503.md) made on real Apple IIgs
hardware. They are the measurement: they can't be regenerated, so they are kept here, and the
build computes from them (`just doc-curve` → `build/docsweep/curve.toml`, read by the NTP option
`doc_volume = "measured"`). Each recording sits beside the step list it was played with
(`*.steps.json`, written by `doc-sweep` when the disk was built), so a later change to the test
song can't misalign it.

The test song is a sine tone and a pilot tone from `src/migs/docsweep.py`: no game material.

## 2026-11-15-iigs-audio-animator

- **Machine:** the user's Apple IIgs (ROM version not recorded), with an Applied Engineering
  Audio Animator stereo card.
- **Player:** SenseiPlay v2.0 / NinjaTrackerPlus, playing `DOCSWEEP.NTP` from
  `build/docsweep/DOCSWEEP.PO` on a Floppy Emu (4 MB image), defaults (Double Channels off).
- **Chain:** Audio Animator Line Out (fed from the DOC output on J25, so the Control Panel volume
  plays no part) → small digital mixer (flat) → GarageBand on iOS → exported WAV, 44.1 kHz,
  24-bit stereo; peak 0.99, no clipping. Stored here as FLAC (lossless, as exported).
- **Result:** docs/es5503.md, "Measured on the listening machine".
