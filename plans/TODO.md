# TODO

Overnight work list, 2026-11-07, in order. Commit after each item.

0. [ ] **Finish and commit the current work:** per-song and shared sample-set modes built side by side, long raw loops, `zero_start`, the converter oscillator-order fix, sorted disk folders, docs.

1. [ ] **Shared set as a resident instrument bank.** Goal: a game loads the instruments into DOC RAM once, then loads only each song's sequence data from disk. We don't write that loader; we make the shared-mode output compatible with it.
   - Inventory every song's patches: each MT-32 program on each channel, and each rhythm key. Confirm each maps to a sample in the shared set, and list anything dropped or unmapped.
   - Make every shared `.NTP` carry byte-identical instrument definitions, sample data and DOC page placement. Default volumes must be the same in every song, not a per-song median. That way the instrument block plus samples can be loaded once and only header and patterns per song.
   - Verify it in the build: compare the instrument and sample sections of all shared `.NTP` files, and fail if they differ.
   - **Always leave 4 KB (16 pages) for sound effects**, in shared mode too. The full set currently needs 256/256 pages, so trim where it costs the least fidelity, measured, not guessed.
   - Document the layout a game loader would use.

2. [ ] **Pipeline speed.** Profile `just ntpmod` / `just ntp` / `just ntpdisk`. Speed up without losing fidelity or quality. Candidates:
   - O(n·w) `np.convolve` smoothing with long windows: use cumulative sums;
   - repeated `build_samples` per song: memoize identical sound builds;
   - serial work across songs and modes: parallelize ffmpeg, php and per-song conversion;
   - redundant rebuilds in the recipe chain.

   Outputs must stay byte-identical (check with checksums before and after).

3. [ ] **Smoother sustains in the shared version.** Sustain loops there are "still a little choppy". Find the cause by measurement:
   - per-row `Cxx` volume steps (zipper);
   - short 512 B averaged loops;
   - envelope smoothing;
   - attack→loop handover.

   Then fix it. Candidates: `Axy` volume-slide interpolation between rows, longer loops within the 4 KB-reserve budget, better crossfades.
