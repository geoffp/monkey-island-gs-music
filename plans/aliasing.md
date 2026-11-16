# Aliasing and "crunch": make docsim2 hear it (tabled 2026-11-15)

Started from "why is there free DOC RAM?" and ended at the user's ear: some samples are
crunchy. Nothing here changed the build, except the `rates (Hz)` log line (below).
Scratch scripts from the session are not kept; the findings are.

## Where the free DOC RAM goes (inst-per-song, 2026-11-15)
- 5-39 KB free per song, 4 KB of it the reserve. MI150, MI166 ~39 KB, MI162 ~29 KB, most others 5-14 KB.
- Not an accident: 25 of 83 steady sounds score *worse* with every raw loop than with 512 B
  (docsim2); ~10 loops are `loop blocked` (next size is 30 or 62 pages and no cheaper upgrade
  frees them); loop sizes are powers of two, so leftovers strand. Unspent RAM costs nothing.

## Higher melodic rate (pitch_max_rate 22 -> 26.32 kHz): not worth it alone
- Trial (scratch config, planner only, not converted): mean gain 0.18 dB (time-weighted), max
  1.5 dB (MI154 Panpipes); 44 zones moved to 22-25 kHz. Costs it doesn't price: attacks 15 %
  shorter (2040 B), raw loops unchanged. The earlier "rate above 22 kHz: dropped" and "attack
  growth" trials (docs/ninjatrackerplus.md) found the same and the user heard no difference.
- E'+ rates are f x 2^n (power-of-two samples per cycle), so a 1024 B loop adds no new rates.
  22 of 132 zones sit at <= 13.3 kHz and could double.

## Why docsim2 barely hears aliasing
- `seg` is the summed |loudness difference| over the reference's summed loudness: Zwicker's
  0.23 power and the HF threshold shrink a -25 dB image band to a sliver. Doubling a rate
  cuts images ~14 dB but scores 1-3 dB.
- No fine structure: frames hop 512 (19 ms), modulation bands stop at 16 Hz; roughness
  (30-150 Hz beating inside a band) is invisible.
- `docsim2.inharmonic_db` only looks below 5 kHz, and only prints warnings.
- Measured (ideal sample, no loop, sampled key): DOC output vs Munt, 9-12 kHz band, 12.5 -> 25 kHz:
  Chorale +28 -> +14 dB, Strings +30 -> +15, Elec Organ +34 -> +19. MT-32 has ~-54 dB there.

## Prototype alias term (worked as a detector, uncalibrated)
Per 0.5-Bark band above 3 kHz and per frame: max(0, dB(DOC excitation) - dB(MT-32 excitation)),
each floored at the threshold in quiet (E = 1 after the ear weighting); mean in dB. Built from
`docsim2._model` up to the excitation, before the power law.
- 8-bit quantisation sets a floor of 2-4 dB even at the DOC's own 26.3 kHz.
- Not monotonic in rate: 19 kHz often scored worse than 12.5 kHz (Chorale 6.7 vs 5.4). Hypothesis,
  untested: the stepping ratio rate / 26 320 matters (1/2 clean, near 1 almost 1:1, between = jitter).
- Drums (as built): MI162/164 Conga 9.9 kHz 19.8, Tom 5.9 kHz 17.3, Mute Conga 7.8 kHz 12.7,
  Kick 14.8 kHz 7.4. At low rates float ~ 8-bit (images, rate-limited); at 24.8 kHz about half
  of what's left is quantisation (Conga 15.3 vs 8.0 float, Kick 6.4 vs 1.4).
- The rate is set by fitting the decay in the block (bytes / decay); the tom is a long ring
  (-11 dB at 300 ms) already cut at 347 ms in 2 KB.
- **Known miss: MI164's Closed Hat.** The user hears it crunchy; the term reads 0.00 (26 273 Hz,
  bands within 0.3 dB of Munt). Its MOD volume is 7, 39 ms, 1 KB. Unexplained: whatever it is,
  the term doesn't model it.
- MI164 Elec Organ zone 67-69: 512 B at 13 290 Hz (8192 B loop blocked, 30 pages, 0.0041/page).
  The rate trial moves it to key 67 @ 25 088 Hz (docsim2 -12.0 -> -12.3; alias -1 to -1.5 dB).
  The user hears aliasing in that organ but can't say where; the two 16 KB raw-loop zones
  (18.8 kHz, raw loops at their own rates) were not scored with the term.

## If picked up (user's direction: systematic, no per-instrument settings)
1. Read-only alias report over every built sample of every song at the keys actually played,
   weighted by note seconds, images split from quantisation (float vs 8-bit at the same rate),
   like `docram.html`. Check it against the user's verdicts: 162 drums, 164 congas + hats,
   164 organ. The hat decides whether the term is complete.
2. If it ranks right, add the term to `docsim2.distance` (weight 0 by default, in the cache key,
   VERSION bump) so pitchplan, `drum_steps` and the loop budget all trade it against pages.
3. Drum rate is the one thing the planner doesn't search (bandwidth rule + block fill): make it a
   search over period-table rates that fit the block, tail length as the cost.
4. Roughness (30-150 Hz fluctuation per band, 5 ms frames) as a second term.
Declined for now: a per-drum `drum_min_rate_by` dict (Conga 14 kHz, Tom 11 kHz), and a rate-cap trial mode.

## Done in the session
- `rate_summary` in `src/migs/ntpsamples.py`: ntpmod logs `MInnn: rates (Hz) name:rate ...` per
  song (and `shared rates (Hz)`), so rates are in the build log. Test in `tests/test_ntpsamples.py`.
