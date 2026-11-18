# Backlog

Open work, most recent context first. Finished items live in git history and in the docs they
point to. This file began as the 2026-11-07 overnight work list (`plans/TODO.md`), pruned
2026-11-11. Done since then:
- shared mode grows raw loops into free pages (`plan_loops` runs for both modes);
- shortened shared attacks by measured cost: tried as the attack-fit trial, rejected
  (`docs/ninjatrackerplus.md`, "Attacks sized to their measured length").

- [ ] **ProTracker effects** (2026-11-17, research by side agents at the user's request): what NTP does with every effect (`docs/ntp-effects.md`) and which could help, ranked, with trial designs (`plans/protracker-effects.md`). In flight: release slides (trial `slides-per-song`). Next candidates: sustain-row slides in place of `env_max_rise_db`, pitch bends in the MODs (1xy/2xy/E1x/E2x; the MIDI already has them), 4xy/6xy vibrato in place of whole-cycle loops (saves DOC pages), ECx cuts. First measure the player's CPU per tick at the trial's higher tick rates.
- [ ] **Pitch bends in the NTP build** (the extraction has them since 2026-11-17, `docs/imuse-format.md` "Pitch bend"): count excursions per program and song, then decide (`plans/protracker-effects.md` #2).
- [ ] **Long releases** (M2160 Soundtrack: `release_max` 0.3 s cuts a release that takes seconds): a longer `release_max`, per sound or global, priced against the steals it costs (`docs/ninjatrackerplus.md`, the open faults under "Sets that take back zones").
- [ ] **docsim 2.0** (auditory, phase-free measure for the planner; loop budget first, then priced merges): `plans/docsim2.md`, evidence in `docs/similarity-measures.md`.
- [ ] **Aliasing / "crunch"** (user, 2026-11-15; tabled): docsim2 barely hears images and 8-bit steps; prototype alias term, drum and organ findings, MI164 hat unexplained: `plans/aliasing.md`.
- [ ] **MI153 pipe-organ buzz** (user, 2026-11-13): not the loops. Keys 52–54 carry key 60's beating (+7–20 dB inharmonic over Munt), and Pipe Organ Lo 43/45 alias at 12.6/14.1 kHz (+6.5 dB). A zone near 54 and a higher low-organ rate; a build check "inharmonic excess over Munt per key". `docs/similarity-measures.md`.
- [ ] **MI2 / Fate of Atlantis song names, and MI2 songs the iMUSE reader cuts short** (113 guard kiosk, 175): `plans/song-names-other-games.md`.
- [ ] **Per-song raw-loop seams with deep modulation** (try looplab's seam search C + whole beat periods). Spectral flux shows a jump at the loop seam for Elec Organ (rotary wobble, ~1.8 Hz, 36% depth) and Shakuhachi, about 1 s in: the crossfade joins two different wobble phases. Candidates: choose the loop length/position so the seam falls at the same modulation phase (autocorrelation of the envelope), or a longer crossfade for those sounds.
- [ ] **Still failing E′+:** Acou Bass / Marimba handover (decaying spectra), Pipe Organ Lo (slow multi-partial beat). `docs/sample-construction.md`: handover spectral jump Acou Bass 2.6, Pipe Organ Lo 2.0, Marimba 1.7 dB.
- [ ] **Steals are abrupt on the DOC** (no declick): soften or avoid stealing releases (MI160 had ~620–780; since 2026-11-08 most fall on a release that has already reached 0). `docs/ninjatrackerplus.md`, "Still open (theory 2)".
- [ ] **Quantization / row packing:** see `plans/ntp-quantization.md` (on hold; its tradeoff table comes first).
