# Backlog

Open work, most recent context first. Finished items live in git history and in the docs they
point to. This file began as the 2026-11-07 overnight work list (`plans/TODO.md`), pruned
2026-11-11. Done since then:
- shared mode grows raw loops into free pages (`plan_loops` runs for both modes);
- shortened shared attacks by measured cost: tried as the attack-fit trial, rejected
  (`docs/ninjatrackerplus.md`, "Attacks sized to their measured length").

- [ ] **docsim 2.0** (auditory, phase-free measure for the planner; loop budget first, then priced merges): `plans/docsim2.md`, evidence in `docs/similarity-measures.md`.
- [ ] **MI153 pipe-organ buzz** (user, 2026-11-13): not the loops. Keys 52–54 carry key 60's beating (+7–20 dB inharmonic over Munt), and Pipe Organ Lo 43/45 alias at 12.6/14.1 kHz (+6.5 dB). A zone near 54 and a higher low-organ rate; a build check "inharmonic excess over Munt per key". `docs/similarity-measures.md`.
- [ ] **MI2 / Fate of Atlantis song names, and MI2 songs the iMUSE reader cuts short** (113 guard kiosk, 175): `plans/song-names-other-games.md`.
- [ ] **Per-song raw-loop seams with deep modulation** (try looplab's seam search C + whole beat periods). Spectral flux shows a jump at the loop seam for Elec Organ (rotary wobble, ~1.8 Hz, 36% depth) and Shakuhachi, about 1 s in: the crossfade joins two different wobble phases. Candidates: choose the loop length/position so the seam falls at the same modulation phase (autocorrelation of the envelope), or a longer crossfade for those sounds.
- [ ] **Still failing E′+:** Acou Bass / Marimba handover (decaying spectra), Pipe Organ Lo (slow multi-partial beat). `docs/sample-construction.md`: handover spectral jump Acou Bass 2.6, Pipe Organ Lo 2.0, Marimba 1.7 dB.
- [ ] **Steals are abrupt on the DOC** (no declick): soften or avoid stealing releases (MI160 had ~620–780; since 2026-11-08 most fall on a release that has already reached 0). `docs/ninjatrackerplus.md`, "Still open (theory 2)".
- [ ] **Quantization / row packing:** see `plans/ntp-quantization.md` (on hold; its tradeoff table comes first).
