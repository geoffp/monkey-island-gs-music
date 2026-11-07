# TODO

Overnight work list, 2026-11-07, in order. Commit after each item.

0. [x] **Finish and commit the current work:** per-song and shared sample-set modes built side by side, long raw loops, `zero_start`, the converter oscillator-order fix, sorted disk folders, docs.

1. [x] **Shared set as a resident instrument bank.** Goal: a game loads the instruments into DOC RAM once, then loads only each song's sequence data from disk. We don't write that loader; we make the shared-mode output compatible with it.
   - Inventory every song's patches: each MT-32 program on each channel, and each rhythm key. Confirm each maps to a sample in the shared set, and list anything dropped or unmapped.
   - Make every shared `.NTP` carry byte-identical instrument definitions, sample data and DOC page placement. Default volumes must be the same in every song, not a per-song median. That way the instrument block plus samples can be loaded once and only header and patterns per song.
   - Verify it in the build: compare the instrument and sample sections of all shared `.NTP` files, and fail if they differ.
   - **Always leave 4 KB (16 pages) for sound effects**, in shared mode too. The full set currently needs 256/256 pages, so trim where it costs the least fidelity, measured, not guessed.
   - Document the layout a game loader would use.

2. [x] **Pipeline speed.** Profile `just ntpmod` / `just ntp` / `just ntpdisk`. Speed up without losing fidelity or quality. Candidates:
   - O(n·w) `np.convolve` smoothing with long windows: use cumulative sums;
   - repeated `build_samples` per song: memoize identical sound builds;
   - serial work across songs and modes: parallelize ffmpeg, php and per-song conversion;
   - redundant rebuilds in the recipe chain.

   Outputs must stay byte-identical (check with checksums before and after).

3. [x] **Smoother sustains in the shared version.** Sustain loops there are "still a little choppy". Find the cause by measurement:
   - per-row `Cxx` volume steps (zipper);
   - short 512 B averaged loops;
   - envelope smoothing;
   - attack→loop handover.

   Then fix it. Candidates: `Axy` volume-slide interpolation between rows, longer loops within the 4 KB-reserve budget, better crossfades.

## Loop research (branch `loop-research`, 2026-11-07)
- [x] Attack detection + loop-construction research, `just looplab`, `docs/loop-detection.md`.
- [x] E′+ as trial variants `per-song-e` / `shared-e` (`loop_construction = "eprime"`), side by side on the disk.
- [ ] After listening: make E′+ the default (and move its code from looplab into ntpmod), or drop it.
- [x] Deterministic one-shot rule (8-bit floor within the attack + loop budget): Xylophone; Taiko doesn't qualify.
- [ ] Still failing E′+: Acou Bass / Marimba handover (decaying spectra), Pipe Organ Lo (slow multi-partial beat).

- [x] Tremolo from short raw loops (shared Elec Organ 1024 B: 6.3 dB at 15 Hz): measured gate `raw_loop_ok` + build check.

- [x] Ghost notes in SenseiPlay (EDx cell's instrument volume applied at tick 0 revives the silent loop): `halt_released_loops` rest one-shot; confirmed and verified in GSSquared.
- [ ] Steals are abrupt on the DOC (no declick): soften or avoid stealing releases (MI160 ~620-780 steals).

## Follow-ups found along the way
- [ ] **Per-song raw-loop seams with deep modulation** (try looplab's seam search C + whole beat periods). Spectral flux shows a jump at the loop seam for Elec Organ (rotary wobble, ~1.8 Hz, 36% depth) and Shakuhachi, about 1 s in: the crossfade joins two different wobble phases. Candidates: choose the loop length/position so the seam falls at the same modulation phase (autocorrelation of the envelope), or a longer crossfade for those sounds.
- [x] **MI167 drops 82 notes** (all percussion): track-type fragmentation; drums now borrow idle melodic tracks (`drum_borrow`). MI167 0, totals 105 -> 13 (per-song) / 11 (shared).
- [ ] Polyphony: loop-only (1-oscillator) Elec Organ for MI160/MI152 overload; per-sound release lengths; steal the quietest release.
- [ ] **Quantization / row packing:** see `plans/ntp-quantization.md` (deferred by the user).
