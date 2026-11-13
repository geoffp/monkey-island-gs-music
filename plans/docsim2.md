# docsim 2.0: an auditory measure for the planner (proposed 2026-11-13)

Background, measurements and sources: `docs/similarity-measures.md`. Prototype: `plans/docsim2_prototype.py`. Nothing here is built
yet. Planner changes are trialled as side-by-side build modes on a branch, as usual.

## The measure (`src/migs/docsim2.py`, new; `docsim.py` stays for the default builds)
- Loudness pattern of a note at DOC_RATE: Hann STFT (1024 / hop 512) → Terhardt ear
  weighting → 0.5-Bark bands (Traunmüller) → Schroeder spreading → Zwicker specific loudness
  `(1 + E/E_t)^0.23 − 1`, at a nominal 70 dB SPL after RMS normalisation. Prototype:
  `loudness_pattern_fast` (2.0 ms per 2 s note on an idle CPU; a planner comparison 2.5 ms
  against `docsim`'s 1.4 ms).
- Two phase-free terms over the whole held note:
  - `seg`: relative loudness-pattern difference of segment means (attack 0.03–0.15 s, early
    0.15–0.6, mid 0.6–1.5, late 1.5–note-off), segments weighted equally;
  - `mod`: per-band modulation depth in 0.5–2, 2–4, 4–8 and 8–16 Hz over the sustain,
    |depth_got − depth_ref| weighted by the reference's band loudness.
  - melodic: `seg + mod`; drums (one-shots): `seg` only (a decay is not movement).
- Its own cache (`build/cache/docsim2.json`, own VERSION) so `docsim.json` and the default
  builds stay byte-identical.

## Where the planner would use it (one trial mode per step, each listened to)
Order agreed with the user 2026-11-13: loop lengths first, where today's measure is blind.
1. **Score the shipped construction**: attack + loop as built (E′+ or raw) with the build's
   row envelope (`_row_envelope`), not the stored render. This is what lets `mod` hear a raw
   loop's movement. It needs the candidate sample built per (key, rate, loop size): measure
   that cost first, and cache by construction inputs.
2. **Loop budget** (`plan_loops`): price each doubling (512 → 2 K → … → 16 K) by measured gain
   per DOC page, as drums already are (`drum_steps`), instead of growing by usage. Weight by
   held note-seconds per zone.
   - References held ≥ 3 s, so a 16 KB loop (0.78 s at 21 kHz) is heard repeating.
   - `raw_loop_ok` stays as a gate: seam clicks are sub-frame, and repeat buzz at
     20–30 Hz is above the modulation bands.
   - If `mod`'s four bands misrank a rotary wobble against a loop-repeat pump, use finer
     bands first.
   - Test cases: MI163 Elec Organ key 71 (512 B, `seg` 0.66 vs 0.22 with its 8 KB loop),
     MI160's 16 KB organ loop, the Chorale and French Horn loops.
3. **Pitch plan** (`ntpmod._pitch_plans`): `seg` over the whole held note instead of the
   first 0.6 s (the Syn Brass case). Expected to add a zone near MI153 key 54 and raise the
   low organ's rate (the buzz report).
4. **Priced merges** (the `no-merges` follow-up): per song, a merge costs
   `d(program B's notes from A's sample) − d(B's own sample)`. Keep it when the pages it
   frees buy more elsewhere (loops, zones, drum rates). This needs the cross-program
   reference (B's Munt render, A's sample), which `NoteError` can't do yet. Measured so far,
   relative to the "train wreck": Trombone 1 −0.01, Toms 0.06, Fretless 2 0.08, Syn Brass4
   0.20 (heard as harsh). So the threshold lies somewhere in 0.08–0.20.
5. **Build check**: per played key, inharmonic excess over Munt (> 6 dB warns), whatever the
   cause (zone too wide, low rate, sample content). Cheap; flags MI153's buzz keys today.

## Tests
Fast vs reference loudness pattern equal (1e-6); level-free (×0.1 input); phase-free
(shifted AM ≈ 0, removed AM > 0); monotonic in added noise and in low-pass cut-off;
`seg` = 0 and `mod` = 0 for identical input; drums use `seg` only.

## Dependencies
None for the build (numpy). The judges are research-only: mosqito (Zwicker, Apache-2.0)
and visqol-python (Apache-2.0) as a `uv add --group research` group for a `just` research
recipe, never in the default install.

## Open
- The `seg`/`mod` weighting is untuned (equal, both are fractions of loudness). Five
  listening verdicts can't fit it; more A/B verdicts first, especially on loops.
- Per-row volume steps (Cxx is 6-bit) and the mix (masking by other parts) are not modelled.
