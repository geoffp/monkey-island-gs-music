# Attack detection and loop construction for the NTP samples

Research and measurements, 2026-11-07 (branch `loop-research`). Tool: `src/migs/looplab.py`, run with `just looplab [mode]` (output in `gs/build/looplab/`: `report.md`, `wav/`, `plots/`). Tests: `tests/test_looplab.py`.

**Goal (user):** a reliable, RAM-efficient and good-sounding way to build the attack and sustain-loop sections:
- no buzz, no audible seam and no handover jump, for every sound, without tuning each sound by hand;
- still sounding like the MT-32;
- bytes spent only where they can be heard.

The shared bank must still fit in 240/256 DOC pages (16 pages are kept for sound effects).

## Research

### Finding loop points (the literature and tools)
1. **Zero crossing + whole periods** ([Soodek capstone, Northeastern][soodek]). Take the first zero crossing after the attack, and end the loop a whole number of f0 periods later. It is seamless only if the cycles really repeat. MT-32 tones don't: their partials are detuned and chorused.
2. **Slope-gated candidates + windowed correlation** ([LoopAuditioneer][la-guide], [source][la-src], GPL-3, made for GrandOrgue pipe-organ samples).
   - Candidate points are where |x′| is below a threshold times its maximum: flat spots.
   - Start/end pairs are scored over a few samples around each point. The longest loops under a quality threshold are kept.
3. **Normalised cross-correlation with a level penalty, then a wrap crossfade.** This is the common commercial pattern ([KVR thread][kvr]: Endless WAV, SampleRobot; [seam finder][seam]): find a near-perfect pair, then polish it with a crossfade.
4. **Spectral-difference seam choice** ([US patent 10643594][patent]). Choose the loop position whose start and end frames have the smallest spectral distance, then hide what's left with a dynamic crossfade and a time-varying filter.
5. **Correlation-adaptive crossfade.** The gains satisfy a² + b² + 2ab·r = 1, where r is the correlation of the two sides. That is linear for r = 1 and equal-power for r = 0, so there is no level dip on decorrelated (chorused) material. Implemented in `loop_search`.
6. **Construct instead of search.** Build one cycle and tile it, so the loop is exactly periodic by construction. Power-averaging the harmonics across the sustain (instead of averaging the waveform) keeps detuned partials' mean power instead of cancelling them.

### Detecting the attack
- **Envelope thresholds:** 20 %/90 % of the peak, and Peeters' "weakest effort" method ([Peeters 2004, CUIDADO][peeters]; [Timbre Toolbox / pytimbre][pytimbre]). Reported to be unreliable on slow or noisy attacks ([ISMA 2017][isma]).
- **Attack / transition / steady-state segmentation** from amplitude and spectral-centroid trajectories, the ACT model ([Caetano, Burred & Rodet, DAFx 2010][caetano]). Its point: an amplitude-only ADSR doesn't fit every instrument.
- **What we do (the user's idea, separating the components with an FFT):** a **pitch-synchronous STFT** (`harmonic_tracks`).
  - Each frame is 4 periods (from `refine_period`), resampled to 64 points per period, under a *periodic* Hann window, with a 5 ms hop. Harmonic h then lands exactly on bin 4h, and the window's leakage never reaches the next harmonic.
  - The symmetric `np.hanning` leaks at about −60 dB, enough to fake spectral flux. Don't use it.
  - The result is one amplitude track per harmonic.
- **Settling** (`settle`), measured on 6 s renders so a slow beat isn't mistaken for a decay:
  - **Steady sounds:** the attack is over once every harmonic that matters (> −30 dB of the total) stays inside its own min..max range (±1.5 dB) of the sustain. The sustain is the last 60 % of the render. The range includes the beat's nulls, so beating counts as sustain, not attack.
  - **Decaying sounds** (beat-averaged level falling faster than 2 dB/s): the attack is over once every harmonic's dB track is a straight line, i.e. an exponential decay.
  - **Beat or wobble period:** the autocorrelation of the level track. The level is then averaged in **power**, not dB: beating nulls dominate a dB average.
  - **Known limit:** very deep beating (Strings, ±4 dB at 2.3 Hz) makes the ranges so wide that the attack end comes out early (30 ms).

## What the sampled MT-32 sounds actually do
From the 6 s Munt renders (harmonic plots in `gs/build/looplab/plots/`):
- **No slow swells.** Every steady sound reaches full level within about 0.1–0.2 s. What looked like 0.6–1.6 s "swells" in the level is **deep periodic beating between detuned partials**:

  | sound | beat period | depth |
  |---|---|---|
  | Strings | 0.44 s | 8 dB |
  | French Horn | 0.57 s | 13 dB |
  | Shakuhachi | 0.41 s | 11 dB |
  | Fantasy | 0.42 s | 10 dB |
  | Chorale | 0.87 s | 5 dB |
  | Elec Organ | 0.53 s | 13 dB (rotary) |

  In the Pipe Organs each harmonic beats at its own rate (h2 ≈ 0.55 s, the others slower).
- **Decays:** Acou Bass, Marimba and Vibes decay at −8 to −10 dB/s. **Xylophone** is 40 dB down at 0.5 s and **Taiko** at 1.1 s. Today they loop near-silence: Xylophone's loop is 2.6 LSB RMS.
- **Steady without beating:** Panpipes, Trombone, Sax. Their measured attacks run 0.1–0.7 s, because weak upper harmonics keep drifting slightly.

## Where the buzz came from (shared bank, measured)
**Ninjaforce's converter is not the cause.** Every NTP header and loop equals the MOD bytes + 128, compared byte for byte in MI150.

1. **8-bit rounding of loops with a fractional number of samples per cycle.**
   - The averaged loop is k identical cycles in float: −64 to −117 dB off-harmonic, which is why the earlier ">99.9 % harmonic" check passed.
   - But at 512 B / k = 34.13 samples per cycle, each cycle is sampled at a different phase, so each rounds differently. The rounding error repeats only once per loop, which is a **buzz at the loop rate** (≈ 30 Hz).
   - Measured: **−37 to −48 dB in 10 of the 16 sounds**, and −21 dB for Xylophone (loop at 2.6 LSB).
   - The loops with a whole number of samples per cycle (Acou Bass, Trombone, Taiko, Pipe Organ: k = 2, 8, 4, 8) had none.
2. **`zero_start`'s forced `loop[0] = 0`.**
   - It puts the *linear-interpolation* crossing at index 0, then forces that sample to 0.
   - The band-limited waveform isn't 0 there: −0.012 at 32 samples per cycle for Strings, i.e. 1 LSB. So one sample per pass is wrong, which is again loop-rate buzz.
   - Fix: `zero_start_exact`, Newton steps on the Fourier series to the true crossing.
3. **Raw or searched loops can't be seamless at 512 B.**
   - A raw 30–60 ms slice of a beating sound changes from cycle to cycle, so its line spectrum has −3 to −47 dB off-harmonic energy.
   - Searching for the best seam (C) helps (Strings −22 → −42 dB, Elec Organ −3 → −14 dB) but can't fix it.
   - At these sizes, **construct, don't search.** The seam search (C) and modulation-phase lengths stay relevant for long per-song loops.

## Candidates and results (shared bank; full table in `report.md`)
**The candidates:**
- **A:** today's averaged snapshot at 0.4 s (reproduces the built bank byte for byte).
- **A′:** A with whole cycles.
- **B:** today's raw loop.
- **C:** searched raw loop with an adaptive crossfade.
- **E:** a power-averaged harmonic cycle.
- **E′:** E with whole cycles and the exact zero crossing.
- **E′+:** E′ with the attack moved into phase with the loop (below).

**Pass criteria:**
- off-harmonic energy < −50 dB;
- wrap kink ≤ 2× the loop's 99th percentile;
- seam spectral jump < 0.5 dB;
- handover level step < 1 dB;
- handover spectral jump < 1.5 dB.

| | A (today) | E′+ |
|---|---|---|
| loop-rate buzz (8-bit) | −37 … −48 dB in 10 sounds, −21 in Xylophone | none (exactly periodic) in all 16 |
| timbre vs the MT-32's sustain (dB RMS over harmonics) | up to 5.9 (Strings), 3.1 (Shakuhachi), 2.6 (Pipe Organ Lo) | ≤ 0.6 (Xylophone aside) |
| handover level step | up to +2.9 dB (Marimba), +2.3 (Sax) | ≤ 0.3 dB (Taiko 1.9) |
| handover spectral jump | up to 3.8 dB | ≤ 1.3 dB, except Acou Bass 2.6, Pipe Organ Lo 2.0, Marimba 1.7 |
| passes all criteria | 1 / 16 | 11 / 16 (the others are listed below) |
| tuning error | ≤ 1.1 cents | ≤ 2.6 cents, Pipe Organ Lo +3.5, Xylophone −3.8 |

How E′+ is built:
- **Whole samples per cycle:** k must divide the loop length (`tune(..., whole_cycles=True)`), so k is a power of two. Within the 6–26 kHz rate range there is always one, since the range spans more than an octave. One cycle is band-limited once and tiled, so the k cycles stay identical after rounding.
- **Power-averaged harmonics:** each harmonic's RMS amplitude over the sustain (from the attack end to 1.9 s of the 2 s recording). For decaying sounds it is taken over the 0.1 s after the handover instead. The phases come from the period where the attack ends.
- **Exact zero crossing:** at `loop[0]`, so Ninjaforce's rotation still has nothing to move.
- **Phase-aligned attack** (`join_aligned`):
  - The loop no longer rotates to suit the attack. Instead, the attack's source start moves earlier by 0–1 period, which adds at most one period (≤ 4 ms) of pre-onset silence, so that the attack *ends* in phase with `loop[0]`.
  - The 4-cycle crossfade then joins two aligned waveforms, with no partial cancellation.
  - The level step went from up to 2.9 dB to ≤ 0.3 dB.

**What it can't do:** a 512 B loop is 25–50 ms and the beats are 0.4–1.2 s, so the shared loops are static, whatever the method. E′+ replaces "the beat phase that happened to be at 0.4 s" (A) with the beat's *average* timbre. The beating itself would need loops of at least one beat period, about 3.5 KB at 8 kHz or more, i.e. per-song mode.

**Still failing E′+:**
- **Acou Bass and Marimba (decays):** the spectrum keeps changing during the decay, and the static loop can't follow it.
- **Pipe Organ Lo:** a slow multi-partial beat.
- **Xylophone and Taiko:** one-shot candidates.

## Attack sizes (RAM)
`report.md` "Attack sizing" measures every attack block (504/1016/2040/4088 B) for every sound with E′+. Attack error is the spectral-shape distance of the played note from Munt over the first 0.5 s.

- **Covering the whole detected attack would cost 172 pages, against 138 today.** The shared bank can't afford it, and it barely helps: most of the "attack error" is the beating a static loop can't hold, not the attack.
- A greedy re-allocation within today's budget, by error saved per page, gives only 18.5 → 18.1 dB summed. **Today's attack sizes are near the optimum; keep them.**
- The RAM to win is elsewhere:
  - **Xylophone as a one-shot:** done (see "One-shots").
  - **Taiko:** it doesn't qualify. It reaches the 8-bit floor only at 1.62 s.

## One-shots (in the build since 2026-11-07)
A looped sound that is already below 8-bit resolution when its loop starts only loops quantisation noise. Xylophone's loop was 2.6 LSB RMS. The rule (`ntpmod.floor_time`, `ntpmod.one_shot_tuning`, config `one_shots`, `one_shot_floor_lsb`, `one_shot_max_rate_drop`) is deterministic, from the same Munt render the sample is cut from.

A melodic sound becomes a one-shot when all of these hold:
1. **It decays.** It isn't `is_steady`.
2. **It reaches the 8-bit floor while the recording lasts.** That is the time after which its 20 ms RMS stays below 1.5 LSB of a peak-normalised sample (−38.5 dB re peak).
3. **It fits the space its attack + loop took.** The whole sound up to that point fits in one DOC block no larger than its attack + loop blocks, at a rate at most 10 % below the looped rate.

The one-shot then plays at an **exact** period-table rate: with no loop to fit, there is no tuning error.

**Loops are planned first,** with every sound looped, and one-shots are decided afterwards (`sample_set`). So what a one-shot saves stays free, rather than growing another loop. Measured: re-spending the 2 freed pages would have turned shared Pipe Organ's buzz-free 512 B loop into a 1024 B raw loop with −9.8 dB loop-rate content. The user chose to keep them free (2026-11-07).

**Result:**

| sound | 8-bit floor | decision |
|---|---|---|
| Xylophone | 0.21 s | one-shot: 2027 B at 9509 Hz in a 2 KB block, exactly in tune (was −1.0 cents, and a 2.6 LSB loop) |
| Taiko | 1.62 s | stays looped (a one-shot would need ~27 KB) |
| Acou Bass, Marimba, Vibes | after note-off (2 s) | stay looped |

- **Shared bank:** 240 → 238 pages, every other sample byte-identical.
- **Track allocation:** melodic one-shots use melodic tracks like before, but free them when the sample ends instead of holding them through note-off and release. MI162 now drops 0 notes, against 8. MI160's steals fall from 864 to 777 (per-song) and from 694 to 622 (shared), with its 16 drops unchanged.
- **Rejected:** putting them on 1-oscillator drum tracks. It starved the percussion (MI160: 16 → 31–77 dropped notes), measured with the new per-sound drop line in the build log.

## Short raw loops add a tremolo (2026-11-07)
A raw loop shorter than the sound's own wobble replays a slice of it at the loop rate. In shared Elec Organ (1024 B) that was a 6.3 dB tremolo at 15 Hz. The build now measures every raw loop size (`ntpmod.raw_loop_ok`: it must repeat at ≤ 2 Hz, or have ≤ 1 dB of level range and ≤ −50 dB of loop-rate content) and fails if any loop doesn't pass. `report.md` has a tremolo column. Details are in `docs/ninjatrackerplus.md`, "Tremolo from short raw loops".

## Recommendation
**For the 512 B loops** (all shared sounds, and short per-song loops), build E′+:
1. whole samples per cycle;
2. power-averaged harmonic cycle;
3. exact band-limited zero crossing;
4. attack shifted into phase with the loop.

Keep today's attack sizes. (Xylophone is now a one-shot, see above; Taiko doesn't qualify.)

**For long per-song raw loops,** use the seam search (C) with the adaptive crossfade. Choose the length at a whole number of beat periods (the Elec Organ / Shakuhachi follow-up in `plans/TODO.md`). Measure them with the same metrics.

**Integration** into `ntpmod.build_samples` waits for the user's listening verdict: `gs/build/looplab/wav/<sound>.wav` plays the Munt source, then A (today), then E′+. Each is played at the sampled key, the lowest song key and the highest, through a DOC-style no-interpolation player.

[soodek]: https://music-tech-archive-2024.netlify.app/files/soodek/Soodek_Capstone_Paper_Final.pdf
[la-guide]: https://loopauditioneer.sourceforge.io/userguide.html
[la-src]: https://github.com/GrandOrgue/LoopAuditioneer
[kvr]: https://www.kvraudio.com/forum/viewtopic.php?p=7283948
[seam]: https://elysiatools.com/ai/en/tools/audio-loop-seam-finder.md
[patent]: https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10643594
[peeters]: https://code.soundsoftware.ac.uk/hg/afontology/raw-file/3a6fda9dee4f/papers/feature%20classification/A%20large%20set%20of%20audio%20features%20for%20sound%20description-Peeters-2004.pdf
[pytimbre]: https://pytimbre.readthedocs.io/en/fonttools-fix/_autosummary/pytimbre.spectral.timbral_model.timbral_util.calculate_attack_time.html
[isma]: https://isma2017.cirmmt.mcgill.ca/proceedings/pdf/ISMA_2017_paper_65.pdf
[caetano]: https://dafx.de/paper-archive/details/rebEOOsnFNJRK6njmpGplA
