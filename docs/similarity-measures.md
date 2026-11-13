# Similarity measures: which one should the planner use? (bake-off, 2026-11-13)

**Question.** The planner chooses sampled keys, zones, rates, loop sizes and (soon) merges by
asking "how close does this sample, played by the DOC, come to the MT-32?". Today that is
`docsim.note_error_db` (`docs/ninjatrackerplus.md`, "Sampled pitch and rate"). Is there a
better, psychoacoustic measure that is still fast enough for the planner?

**Answer (recommendation).** A new measure, *docsim 2.0*: Zwicker-style specific loudness
computed in numpy, compared phase-free over the whole held note, with a modulation term.
Its auditory front end agrees with the ISO 532-1 Zwicker model (rank correlation 0.90 over
132 notes). It agrees with the listening verdicts where `docsim` doesn't. It costs about
1.8× `docsim` per planner comparison (2.5 ms vs 1.4 ms) while hearing 3.3× as much of each note. The external
packages are too slow for the planner and serve as judges. Implementation plan:
`plans/docsim2.md`. Nothing is built yet.

**Built (2026-11-13)** as `src/migs/docsim2.py`, with trial build variants `ds2-loops`,
`ds2-pitch`, `ds2-merges` (results: `docs/ninjatrackerplus.md`, "docsim 2.0 trials").
Changes from the bake-off prototype:
- **Drums:** the whole hit's mean with 25 ms forward masking, fitted to ISO 532-1
  time-varying loudness on Conga, Kick, Tom and Snare (within 0.05).
- **Modulation weight:** 3 in the trials. The five verdicts hold for weights 1–6 (margins
  recomputed from the bake-off's notes); above 4, Syn Brass4 falls below Fretless 2.
- **Merges:** priced with each sample at its best loop.

## Ground truth: the user's listening verdicts
Only these count as ground truth. Everything else in the tables is unjudged.

| case | verdict (user, 2026-11-12/13) |
|---|---|
| MI165 Syn Brass4 played from the Trombone sample (merge) | harsh; its own sample is better |
| MI160 Elec Organ, `inst-per-song` 16 KB raw loop vs `no-merges` 512 B | the long loop matters |
| Pipe Org 2 played from the Elec Organ sample (merge) | "a train wreck" |
| Violin 2 played from the Str Sect 3 sample (merge) | quite different; merge hurts |
| Toms sharing one sample (merge) | doesn't matter much |
| MI153 opening drone (keys 70/74): `inst-per-song` 16 KB loop vs `ds2-loops` 512 B | the 512 B loop is "flat, buzzy" (2026-11-13) |
| MI160 Elec Organ: `ds2-loops` 16 KB vs `ds2-pitch` 512 B (with more zones) | losing the long loop is bad: the organ is a first impression, with little else in the mix |
| MI159 Elec Organ: `ds2-loops` 16 KB vs `inst-per-song` 512 B | the long loop keeps its tremolo |
| MI165 Shakuhachi: `ds2-loops` 16 KB vs `inst-per-song` 512 B | the trade for longer loops is good |
| MI165 in `ds2-merges` (Syn Brass4, Elec Bass1 own samples) | "dramatically more faithful" |
| MI163, MI167: `ds2-pitch` vs `ds2-loops` (zones moved) | about the same |
| MI162 Low Floor Tom vs High Floor Tom (own samples each) | exactly the same; all toms likely one sound at several pitches (revises the toms row above). Measured: 41/43, 45/47, 48/50 bit-identical; 41 → 45 +5.5 st (r 0.97), 45 → 48 +5.9 st (r 0.91) |
| MI161 Violin 2 own sample, `ds2-merges` | sounds great |
| MI165 `ds2-merges` with the Shakuhachi's long loops given to Syn Brass4 | quite good, but losing the Shakuhachi loop is "a bummer" |
| MI165 A/B, same pages: A Syn Brass4 two 8 KB loops vs B Shakuhachi 76 16 KB | A, narrowly ("the brass loops matter more than I thought"; "sort of a toss-up"). The measure agrees: 0.0185 and 0.0116 vs 0.0076 dB/page |

Five verdicts can rule a measure out but can't fit one. The weights below are therefore not
fitted, and more A/B verdicts (especially on loops) are the next input.

## Harness (scratch scripts, not tracked)
Each case plays the same MT-32 notes from two shipped samples (`build/monkey/ntp/<variant>/
MIxxx.mod`), A against B, and scores both against Munt's render of each note. Notes are held
3 s, velocity 100 (`ntpsamples.cached_render`).
- **Playback:** `loopbuild.play` (the DOC's drop-sample phase accumulator at 26 320 Hz),
  attack then loop as stored in the MOD. Drums: `docsim.doc_play` at the rate their MOD
  pattern plays (PAL / period).
- **Per-row volume:** the build's own contour (`ntpsamples.sustain_contour`, `is_steady`:
  250 ms, or ≥ 0.8 s and the raw loop's length for steady sounds).
- **Tuning:** a MOD stores no sample rate, and a raw loop's rate isn't the zone rate in the
  build log. So each sample is tuned once, at its own sampled key, against Munt's render of
  the program it was taken from (log-spectrum correlation, 2-cent grid), as the build tunes it.
- **Preflight** (≈ 20 s, every note, cheap checks: pitch within 20 cents, level, shipped
  attack matches Munt's render at the tuned rate, score vs build log) before any slow run.

Two harness bugs were found and fixed before the results below, and both invalidated
earlier runs:
1. **Pitch:** using the log's zone rate played Violin 2 5.5 semitones flat and Pipe Organ a
   semitone sharp. That gave "≈ 0 dB" docsim scores and void results for every measure.
2. **Envelope:** a re-implemented 60 ms per-row volume doubled raw loops' tremolo (the build
   smooths it, see above) and inverted the loop verdicts.

**Unexpected finding:** the toms. The MT-32 plays GM keys 41/43, 45/47 and 48/50 as three
identical pairs (renders bit-identical), so the six tom keys are three sounds. In `no-merges`
each tom got 2 KB at 5872 Hz (the minimum rate); the merged Tom gets 15 KB at about 15 kHz. A
second case, "Toms (merge alone)", gives each tom its own render at the merged sample's rate
and size, so it measures the merge alone.

## Candidates
| measure | what it is | installs | cost (s per s of audio) |
|---|---|---|---|
| `docsim` | today's: A-weighted harmonic and between-harmonic distortion, time-averaged, 0.03–0.6 s | in repo | 0.002–0.006 |
| `docsim_long` | the same over the whole held note | in repo | 0.003 |
| ERB (`erb_time`/`erb_avg`) | gammatone filterbank, 64 ERB bands, power^0.3, frame by frame / averaged | `gammatone` (pip) | 0.2–0.4 |
| Zwicker (`zwicker`) | ISO 532-1 time-varying specific loudness, frame-by-frame difference | `mosqito` 1.2.1 (Apache-2.0) | 4.8 |
| ViSQOL audio | Google's ViSQOL v3 audio mode, pure-Python port | `visqol-python` 3.8.0 (Apache-2.0) | 2.2 |
| **docsim 2.0** (`ds2*`) | see below | numpy only | **0.0025** (≈ 2 ms per 2 s note, idle CPU) |

Dropped without running:
- `timbral_models` (AudioCommons): fails to build (depends on the deprecated `sklearn` alias)
  and is unmaintained.
- PEAQ: no maintained Python package; patent-encumbered.
- CDPAM: PyTorch plus 98 MB, and non-deterministic on GPUs (our builds must reproduce).
- HAAQI (`pyclarity`): about 60 s per clip.
- Zimtohrli (Google): no PyPI package (source build with Go); a candidate only if the pip
  ones had failed.

### docsim 2.0 (prototype: scratch `ds2.py`; plan: `plans/docsim2.md`)
1. **STFT:** both signals at DOC_RATE, RMS-normalised to a nominal 70 dB SPL. Hann window,
   1024 samples (39 ms), hop 512.
2. **Ear weighting:** outer/middle-ear weighting by the threshold in quiet (Terhardt 1979; also
   PEAQ's basic model).
3. **Bands:** power summed into 0.5-Bark bands (Traunmüller 1990), 40 Hz–12 kHz.
4. **Masking:** spreading across bands (Schroeder, Atal & Hall 1979).
5. **Loudness:** specific loudness `N' = (1 + E/E_t)^0.23 − 1` (Zwicker's power law with
   threshold; Zwicker & Fastl, *Psychoacoustics*).
6. **Comparison**, in three forms:
   - `ds2`: frame by frame, Σ|N'got − N'ref| / ΣN'ref;
   - `ds2_seg` (phase-free): the same on segment means (attack 0.03–0.15 s, early 0.15–0.6,
     mid 0.6–1.5, late 1.5–end), segments weighted equally;
   - `ds2_mod` (phase-free): per band, the loudness envelope's modulation depth in 0.5–2,
     2–4, 4–8 and 8–16 Hz over the sustain, |depth_got − depth_ref| weighted by the band's
     loudness. This is the modulation-spectrum idea of Dau, Kollmeier & Kohlrausch (1997),
     reduced to four bands. Checked on test tones: 5 Hz AM lands in 4–8 Hz and scales with
     depth; the same AM at another phase scores 0.0003, removing it 0.05.
   - `ds2_segmod = ds2_seg + ds2_mod` (equal weights: both are fractions of loudness).

Why phase-free: two renders of a wobbling sound differ in the wobble's phase, not in
quality. That is why `docsim` averages over time (a frame-by-frame comparison scored a
perfect organ sample −14 dB instead of −25). But averaging also throws the wobble away, so
`docsim` can't hear what raw loops are for. Segment means keep the coarse time shape (attack,
decay); the modulation term keeps how much each band moves, not when.

## Results
Margins are ln(A/B) of the case means (for docsim, the dB difference as ln of a power
ratio), shown relative to the "train wreck" case (Pipe Org 2 = 1). **+** means B (own sample,
or `no-merges`) is closer to Munt; **−** means A is.

| case (verdict) | docsim | docsim_long | ds2 | ds2_seg | ds2_mod | ds2_segmod | erb_avg | zwicker |
|---|---|---|---|---|---|---|---|---|
| Syn Brass4 (B better) | **−0.09** ✗ | +0.24 | +0.22 | +0.26 | +0.00 | +0.20 | +0.04 | +0.15 |
| Elec Organ 160 (A better) | −0.09 | −0.09 | −0.04 | −0.04 | **−0.44** | −0.07 | +0.04 ✗ | −0.05 |
| Pipe Org 2 (B much better) | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Violin 2 (B better) | +1.34 | +1.18 | +0.72 | +0.65 | +1.24 | +0.65 | +0.82 | +0.91 |
| Toms, merge alone (≈ tie) | +0.33 | +0.31 | +0.11 | **+0.04** | +0.84 ✗ | +0.06 | −0.01 | +0.20 |
| *unjudged:* Elec Bass1 | +0.48 | +0.42 | +0.56 | +0.56 | +0.33 | +0.58 | +1.01 | +0.23 |
| Ice Rain | +0.49 | +0.33 | +0.26 | +0.52 | −0.06 | +0.39 | +0.75 | +0.09 |
| Fretless 2 | +0.66 | +0.46 | +0.05 | +0.07 | +0.68 | +0.08 | +0.25 | −0.12 |
| Trombone 1 | −0.16 | −0.19 | −0.04 | −0.03 | +0.18 | −0.01 | −0.44 | −0.10 |
| Elec Organ 163 | −0.58 | −0.53 | −0.26 | −0.33 | −0.46 | −0.33 | −0.01 | −0.25 |

- **Syn Brass:** `docsim` prefers the merged brass you heard as harsh: its 0.6 s window ends
  before the sustain where the harshness is. Over the whole note (`docsim_long`) it gets it
  right, and so does every loudness measure.
- **Toms, merge alone:** the loudness measures call it a near tie (`ds2_seg` 0.04 of a train
  wreck), as you heard; `docsim`'s drum measure gives a third of one. The modulation term
  fails here: a drum's decay counts as slow "movement", and a pitched-up tom decays
  faster. **So drums (one-shots) use `seg` only.**
- **Toms as shipped** (2 KB at 5.9 kHz each in `no-merges`): every measure calls those much
  worse (−0.4 to −0.8 of a train wreck), yet you heard little difference in the songs.
  Isolated-note measures can't see masking by the rest of the mix. The planner's hit weights
  account for importance, not for masking.
- **Merge ranking** (`ds2_segmod`, relative to the train wreck):
  - near-ties: Trombone 1 (−0.01), Toms alone (0.06), Fretless 2 (0.08);
  - audible: Syn Brass4 (0.20, which you heard);
  - larger: Ice Rain (0.39), Elec Bass1 (0.58), Violin 2 (0.65), Pipe Org 2 (1).

  That suggests a merge threshold somewhere between 0.08 and 0.20 for priced merges, to be
  set by listening.

### Loops: the modulation term is what hears them
Zone by zone, where one build had a raw loop and the other 512 B (`ds2_seg` / `ds2_mod`,
lower is closer to Munt):

| zone | raw loop | 512 B loop | raw better on |
|---|---|---|---|
| MI160 Elec Organ keys 59, 72 (16 KB) | 0.26 / 0.03, 0.20 / 0.03 | 0.27 / 0.08, 0.23 / 0.08 | seg, mod |
| MI160 Elec Organ keys 43, 57 (8 KB) | 0.43 / 0.04, 0.32 / 0.06 | 0.38 / 0.05, 0.30 / 0.08 | mod |
| MI159 Chorale 68, 70 (8 KB) | 0.14 / 0.03, 0.15 / 0.02 | 0.11 / 0.05, 0.14 / 0.05 | mod |
| MI167 French Horn 69, 71 (8 KB) | 0.20 / 0.05, 0.22 / 0.05 | 0.22 / 0.07, 0.23 / 0.08 | seg, mod |
| MI165 Shakuhachi 69–72 (8 KB) | 0.13 / 0.04 (mean) | 0.15 / 0.07 | seg, mod |
| MI163 Elec Organ 73, 76 (8 KB) | 0.22 / 0.03, 0.25 / 0.04 | **0.66 / 0.09, 0.71 / 0.08** | seg, mod |

- **The modulation term prefers the raw loop in 6 of 6 zones; `seg` in 4 of 6.**
  `docsim_long` mostly prefers the 512 B loop: a 512 B loop is one averaged sustain cycle,
  close to the time-averaged spectrum `docsim` compares by construction.
- **MI163, `no-merges`:** the 8 KB loop went to zone 66, and key 71 was left with a 512 B
  loop whose harmonic balance is badly off. Against Munt, at key 73:
  - fundamental +10 dB;
  - 2nd harmonic −7 dB;
  - 3rd and 4th harmonics +12 dB.

  The build log scores that zone −31.8 dB, because it scores an ideal sample (Munt's render
  stored whole), not the attack + 512 B loop that ships. `docsim` 2.0 should score the
  shipped construction (plan step 2).

### Agreement with Zwicker, note by note (132 melodic notes)
| measure | Spearman ρ with Zwicker | same side as Zwicker (A vs B per note) |
|---|---|---|
| docsim | 0.10 | 29 / 49 |
| docsim_long | 0.17 | 36 / 49 |
| **ds2** (frame by frame) | **0.90** | **44 / 49** |
| ds2_seg | 0.76 | 36 / 49 |
| ds2_segmod | 0.80 | 30 / 49 |
| erb_time | 0.85 | 29 / 49 |

The Zwicker comparison here is frame by frame, so it shares the flaw the phase-free terms
avoid (out-of-phase movement counts as error). The 0.90 validates docsim 2.0's auditory front
end against the standard model. Choosing a phase-free comparison over it rests on the loop
evidence above, not on Zwicker. `docsim`'s ρ = 0.10 means its ranking of notes barely
relates to the loudness model.

### Cost
A planner-style comparison (stored sample → DOC playback → score), reference cached per note:

| measure | per comparison |
|---|---|
| docsim (0.6 s) | 1.4 ms idle (3.7 ms with all 8 CPUs busy) |
| docsim 2.0 `seg + mod` (whole 2 s note) | 2.5 ms idle (6.4 ms busy) |

Reference patterns: 7 ms per note, once (the Munt render's resampling dominates).
The optimised loudness pattern (`loudness_pattern_fast`: sliding windows, float32,
`np.add.reduceat` band sums) equals the prototype to 1e-7 and is 8× faster. Zwicker
(mosqito) and ViSQOL are about 1900× and 900× docsim 2.0, usable as judges only.

## MI153 pipe-organ buzz (user report, 2026-11-13)
"A tiny amount of buzz … significant at certain pitches." Measured per played key: the
inharmonic energy (between the note's harmonics and its sub-octave's, 20 Hz–5 kHz,
A-weighted, sustain 0.3–1.8 s) of the DOC playback, of the same sample ideally resampled,
and of Munt's own note:
- **Not the loops.**
  - The 512 B loops are exactly periodic (E′+ tiles one cycle; no cycle-to-cycle
    difference).
  - At each raw loop's join, spectral change is 1.01–1.10× the loop's own movement and the
    level step is within it.
  - A baseline (level steps elsewhere in each loop) also clears the larger joins in other
    songs. Elec Organ 70/71 step 4.2–4.5 dB at the join but 6 dB at the 95th percentile
    elsewhere, from the rotary wobble.
  - Slight spectral outliers: Strings 70 (1.42 vs p95 1.32), French Horn 69, Chorale 62.
- **Keys 52–54** (the bottom of the key-60 zone, 6–8 semitones down): 7–20 dB more
  inharmonic energy than Munt (key 52: −16.4 vs −34.4 dB). DOC and ideal resampling give the
  same figure (within 0.4 dB), so the excess is in the sample: key 60's own beating, carried
  down onto keys where the MT-32 is clean. Partials beating at tens of Hz sound as roughness.
  From key 55 up, Munt's note has as much inharmonic energy itself.
- **Keys 43–51** (Pipe Organ Lo):
  - 43 and 45 are 512 B loops at 12.6 and 14.1 kHz. The loops are clean (−57 dB when
    resampled ideally), but the DOC's drop-sample stepping folds images back at −26 dB,
    +6.5 dB over Munt.
  - 46–51 (8 KB raw loop) are +7–9 dB over Munt, part sample, part DOC.

So the buzz is a zone and rate matter. docsim 2.0's `seg` term sees it; a zone near key 54,
and a higher rate (with a 1 KB loop, so a faster rate still holds whole cycles) for the low
organ, are what the planner would be expected to choose. A cheap build-time check, "per
played key, inharmonic excess over Munt > 6 dB", would flag this kind of problem whatever
its cause. A/B file: scratch `listen/MI153_organ_keys_munt_vs_doc.wav` (Munt, then DOC, for
keys 52, 53, 54, 57, 60, 43, 45, 46, 48, 36, 38).

## Revision after listening: the live term (2026-11-13)
The first trials (mod weight 3) called MI153's opening drone a tie: 16 KB loop −17.4 dB,
512 B −18.6 dB (3 s; −17.3 and −18.7 at 10 s), but the user heard the 512 B loop as flat and
buzzy. Modulation depth, averaged over the auditory bands, of the steady part of a 10 s note
(scratch `slowmod.py`):

| note | version | 0.5–2 Hz | 2–8 Hz | 8–16 Hz |
|---|---|---|---|---|
| Pipe Organ 74 | Munt | 0.005 | 0.078 | 0.022 |
| | 16 KB loop | 0.059 | 0.106 | 0.021 |
| | 512 B loop | 0.001 | 0.004 | 0.002 |
| Elec Organ 64 (MI160) | Munt | 0.118 | 0.169 | 0.108 |
| | 16 KB loop | 0.153 | 0.159 | 0.112 |
| | 512 B loop | 0.026 | 0.015 | 0.006 |

The 512 B organ loop loses nearly all of Munt's 2–8 Hz shimmer. The 16 KB loop keeps it but
adds a ~1 Hz pulse (its own repeat). The `mod` term compares depth linearly, per auditory
band, so the missing shimmer and the added pulse cost about the same. Depth discrimination is
roughly constant in dB (Wakefield & Viemeister 1990), so the new `live` term compares the band-averaged
depth per modulation band as a ratio: mean |log10((g + f) / (r + f))|, with floor f = 0.01
(near the AM detection threshold, in loudness units).

All eight melodic verdicts above, scored at 3 s (scratch `verdicts.py` → `vanal.py`; margin =
worse side's dB − better side's, a positive margin = right):

| setting | right | margins (153, 160 pitch, 160 no-merges, 159, 165 Shakuhachi, Syn Brass4, Pipe Org 2, Violin 2) |
|---|---|---|
| mod 3 (first trials) | 7/8 | −0.1 +3.2 +3.2 +2.4 +1.6 +1.5 +9.4 +7.7 |
| mod 1, live 0.2, floor 0.01 (**chosen**) | 8/8 | +1.9 +4.2 +4.2 +2.9 +2.4 +1.6 +9.8 +6.1 |
| mod 0, live 0.1–1 | 8/8 | smallest margin 0.8–1.8 |
| mod 3, live 0.1–0.2 | 8/8 | smallest margin 0.6–1.3 |

The verdicts hold for every setting tried with live ≥ 0.1, with both floors (0.003, 0.01) and
with modulation measured from 0.15 or 0.6 s, so the choice is not a fit to these cases.
Mod 1 with live 0.2 keeps the widest smallest margin.

## Limitations
- Munt is the reference, not a real MT-32. The DOC model is the build's (no analogue stage,
  no 6-bit Cxx volume steps).
- Isolated notes: no masking by the mix (the toms).
- Five verdicts; `seg`/`mod` weighting untuned; ViSQOL per case only.

## ViSQOL (per case, MOS: higher is closer; notes joined into one phrase per side)
| case (verdict) | A | B | prefers |
|---|---|---|---|
| Syn Brass4 (B) | 2.15 | 2.20 | B ✓ |
| Elec Organ 160 (A) | 2.17 | 2.16 | A ✓ (barely) |
| Pipe Org 2 (B, train wreck) | **2.70** | 2.39 | **A ✗** |
| Violin 2 (B) | 2.75 | 2.95 | B ✓ |
| Toms, merge alone (tie) | 2.94 | 2.92 | tie ✓ |
| *unjudged:* Chorale / French Horn / Shakuhachi / Elec Organ 163 loops | | | A (the raw loops) |
| Elec Bass1, Ice Rain, Fretless 2, Trombone 1 | | | mixed |

ViSQOL misses the clearest verdict (the train wreck scores better than the right sample),
and every score falls in 2.0–3.1. It was designed for small codec impairments, and these
are all large. It is ruled out as a judge for this.

## Sources
- Terhardt, E. (1979). Calculating virtual pitch. *Hearing Research* 1, 155–182 (threshold in quiet formula).
- Traunmüller, H. (1990). Analytical expressions for the tonotopic sensory scale. *JASA* 88, 97–100.
- Schroeder, M. R., Atal, B. S., Hall, J. L. (1979). Optimizing digital speech coders by exploiting masking properties of the human ear. *JASA* 66, 1647–1652.
- Zwicker, E., Fastl, H. *Psychoacoustics: Facts and Models* (specific loudness, power 0.23); ISO 532-1:2017.
- Dau, T., Kollmeier, B., Kohlrausch, A. (1997). Modeling auditory processing of amplitude modulation. I. *JASA* 102, 2892–2905.
- Wakefield, G. H., Viemeister, N. F. (1990). Discrimination of modulation depth of sinusoidal amplitude modulation (SAM) noise. *JASA* 88, 1367–1373 (depth discrimination roughly constant in 20 log m: the `live` term's ratio).
- ITU-R BS.1387 (PEAQ), https://www.itu.int/rec/R-REC-BS.1387
- MoSQITo, https://github.com/Eomys/MoSQITo (PyPI `mosqito`, Apache-2.0)
- visqol-python, https://pypi.org/project/visqol-python/ (port of https://github.com/google/visqol)
- gammatone, https://pypi.org/project/gammatone
- Zimtohrli, https://github.com/google/zimtohrli; paper https://arxiv.org/abs/2509.26133
- CDPAM, https://pypi.org/project/cdpam/ (paper https://arxiv.org/abs/2102.05109)
- pyclarity (HAAQI), https://pypi.org/project/pyclarity; timing from https://arxiv.org/pdf/2401.01145
- timbral_models, https://pypi.org/project/timbral_models
- AQUA-Tk (PEAQ in Python), https://github.com/Ashvala/AQUA-Tk
