# NinjaTrackerPlus (NTP): everything we know

NinjaTrackerPlus is Ninjaforce's (Jesse Blue) Apple IIGS tracker player. It's a rewrite of NinjaTracker, which played SoundSmith-format files. It plays music converted from Protracker MOD files.

Sources (fetched by `just fetch-ntp` into `vendor/ntp/`):
- [NTP] `ntpsources.zip`: `file_format.txt`, `FTN.D5.08.TXT`, `ninjatrackerplus.s` (the player, Merlin 32), `ntpconverter.php` + `ntpconverter_lib.php` (MOD → NTP), `call_player_sample.s`, `make_tables.php`.
- [PAGE] https://www.ninjaforce.com/html/products_ninjatrackerplus.php
- [DEMO] `ntp.2mg`: a ProDOS 8 demo disk (BASIC `MENU`, `DEMOSTUFF/ntpplayer`, `ntpcall`, example `.NTP` files).
- [SENSEI] SenseiPlay (digarok, NinjaForce): https://github.com/digarok/senseiplay. A ProDOS 8 NTP jukebox using the same engine; the release ships an 800K disk.

## Why consider it instead of MIDI Synth
- **Many tracks:** 1–31, "TL;DR 15" [PAGE]. Each track uses 1 or 2 of the 32 DOC oscillators; 2 when looped samples need swap mode, 4 with "channel doubling" (which limits a song to 7 tracks). Compare MIDI Synth's **7 voices × 4 oscillators**.
- **Static allocation:** each tracker channel owns its oscillators, so there's **no voice stealing**. The converter assigns notes to tracks itself.
- **Effects:** 30 of 31 Protracker effects (not E0 filter). Effect 9 (sample offset) only works on streamed samples [PAGE].
- **No envelopes:** loudness over time is done with volume effects (C set volume, A volume slide, EC note cut), i.e. a primitive ADSR.
- **Samples:** up to 255 instruments in NTP. The MOD route limits that to **31** samples. 8-bit mono. 64 KB DOC RAM, aligned to power-of-two sizes (256 B–32 KB).
- **Streaming:** samples over 32 KB − 8 bytes (or any, by option) are streamed from main RAM through a 512-byte DOC buffer per streaming track. This costs CPU; "the normal 4 streamed tracks work fine" [PAGE].
- **Timing** runs off the DOC (sound) interrupt. Blocking interrupts too long makes the music hang [PAGE].
- **Game-friendly API** (`call_player_sample.s`): the player is loaded at a fixed address (example `$0F0000`), with a jump table:
  - `NTPprepare` (+0): X/Y = song pointer, A = 1 to try channel doubling; C set on error, A = 3 means doubling isn't possible
  - `NTPplay` (+3): A = 1 plays once
  - `NTPstop` (+6)
  - `NTPgetvuptr` (+9)
  - `NTPgete8ptr` (+12)
  - `NTPforcesongpos` (+15)
  - `NTPgetsongpos` (+18)
  - `NTPsetplayvolume` (+21): A = volume, e.g. 180
  - `NTPstreamsound` (+24): play a sound effect from main RAM

  Free DOC RAM can hold sound effects too, but you write the code to play them.

## File format (`.NTP`, ProDOS type `$D5`, aux `$0008`) [NTP file_format.txt]
Versions 0, 1 and 2. The converter writes version 1 ("since 1.3"); song and instrument names are stored "since 1.4".
```
header:      "nfc!", version, #tracks (1-31), #instruments (1-255), #patterns (1-255),
             order length (1-255), [v2: Pascal song name]
per track:   output channel (0 FL, 1 FR, 2 SL, 3 SR, 4..7 height/back);
             [v1+] stream-buffer DOC page (512 B buffers);
             [v2] %x00yyyyy: x = 2 oscillators, yyyyy = first oscillator
per instr.:  type %0000zxyy (z streamed; x ideal power-of-2 size;
                  yy 0 not looped, 1 whole sample is the loop, 2 loop header (next instr. is type 3), 3 the loop),
             length (3 bytes LE; v0: 2), [v1+] repeat position (3), repeat length (3),
             volume 0-64, finetune -8..7, DOC page (0 if streamed), DOC wave size/resolution register,
             [v2] Pascal name
order:       pattern numbers
patterns:    64 rows x tracks x 4 bytes: note (0 = none, else 1 + index into the period table),
             instrument (0 = none), effect 0-15, parameter
samples:     raw bytes, already DOC-ready (no 0 bytes; "stopper bytes" appended where needed)
```

## Converter rules (ntpconverter_lib.php)
- **Accepted MOD IDs:** `M.K.` (4 channels), `nCHN` (1–9), `nnCH`/`nnCN` (01–31).
- **Notes:** the MOD period is matched (exactly, else nearest) against a **6-octave Protracker table**, 72 notes: periods 1712 … 28 (C-1 … B-6 in the table's comments). NTP note = index + 1.
- **Samples:**
  - 8 stopper bytes are added unless the size is an ideal power of two.
  - Looped samples are split into a loop header (type 2) and the loop (type 3).
  - Very short, non-ideal loops are repeated up to 512 bytes.
  - Over 32760 bytes, or used with effect 9, means **streamed** (if streaming is allowed).
- **DOC RAM:** samples are placed largest first, with stream buffers added only for tracks that play a streamed sample. The player handles oscillator use per track ("one-oscillator tracks" since Jan 2022).
- Running it: `php ntpconverter.php SONG.MOD` (needs PHP CLI), or Ninjaforce's online converter.

## MI1 timing (measured 2026-11-06)
MI1's MT-32 data is **performed, not quantized**. Note-ons sit on a quarter-beat grid only 5% of the time, even with the best per-song offset. Inter-onset gaps cluster around 33–36 ticks (at 120 PPQN) with 0–4 ticks of jitter. A tracker conversion therefore uses a fine grid:
- MOD tempo 240, so one tracker tick = 2.5/240 s ≈ 10.4 ms (2.5 MI1 ticks);
- speed 3, so one row = 31.25 ms;
- `EDx` note delay for the in-row tick.

Timing error ≤ ~5 ms. MI160 (94 s) ≈ 3000 rows ≈ 47 patterns.

### Underlying grid per song (circular fit of note-ons to a grid unit; R = 1 means perfectly on the grid)
| song | unit (ticks / ms) | R | median / max deviation (ticks) | BPM if unit = 16th |
|---|---|---|---|---|
| 150 | 36.90 / 154 | 0.81 | 2.3 / 12.9 | 97.6 |
| 151 | 39.65 / 165 | 0.58 | 4.4 / 19.3 | 90.8 |
| 152 | 56.85 / 237 | 0.56 | 7.0 / 28.4 | 63.3 |
| 153 | 73.95 / 308 | 0.71 | 3.9 / 36.3 | 48.7 |
| 154 | 51.75 / 216 | 0.88 | 2.7 / 20.2 | 69.6 |
| 158 | 56.10 / 234 | 0.64 | 5.4 / 27.8 | 64.2 |
| 159 | 33.25 / 139 | 0.70 | 2.0 / 15.7 | 108.3 |
| 160 | 40.60 / 169 | 0.51 | 4.5 / 20.3 | 88.7 |
| 161 | 33.95 / 142 | 0.52 | 4.5 / 15.9 | 106.0 |
| 162 | 37.90 / 158 | 0.77 | 2.7 / 15.0 | 95.0 |
| 163 | 26.30 / 110 | 0.59 | 3.2 / 12.6 | 136.9 |
| 164 | 34.95 / 146 | 0.61 | 3.1 / 17.5 | 103.0 |
| 165 | 35.80 / 149 | 0.82 | 1.5 / 17.5 | 100.6 |
| 166 | 47.45 / 198 | 0.66 | 4.1 / 23.7 | 75.9 |
| 167 | 53.55 / 223 | 0.56 | 5.7 / 26.6 | 67.2 |
| 168 | 53.65 / 224 | 0.61 | 5.4 / 25.9 | 67.1 |

So the songs were played to a beat, but the outliers (50–150 ms: drift, grace notes, rolled chords) rule out a naive snap.

**Plan (hybrid quantization):**
- Rows sit on each song's detected unit. MOD speed 6, and the MOD tempo is set per song so that one row = one unit.
- Each note goes in the row at or before its time, with `EDx` (0–5 ticks) keeping its micro-timing: error ≤ half a tracker tick, ~14 ms at MI160's tempo.
- An optional hard quantize drops the `EDx` delays.
- Pattern RAM matters for a game. MI160 on a 31 ms grid ≈ 47 patterns × 15 tracks × 64 × 4 B ≈ 180 KB; on its unit grid ≈ 9 patterns ≈ 35 KB.

## Our MOD-first pipeline (`src/migs/ntpmod.py`, `just ntpmod` → `just ntp`)
The MOD is the primary artefact: it renders on the desktop (ffmpeg's libopenmpt → `music/ntp/wav/`), and Ninjaforce's converter (`php-cli`, `STREAM_FORBIDDEN`) turns it into `.NTP`. Settings live in `gs/ntp.toml`; sounds come from `gs/mt32bank.toml` (the same 14 melodic and 14 drum sounds as MI1.BNK).

- **Samples:**
  - **Melodic:** the raw MT-32 attack (2040 B, so with NTP's 8 stopper bytes it fills a 2 KB block), then a 512 B loop holding k sustain cycles. The rate is set by 512 B = k periods. The note base + finetune (16 ProTracker finetune tables read from `make_tables.php`) tune it to within ±4 cents. NTP rate = PAL 3 546 894.6 / period.
    - The loop is **one averaged cycle tiled k times** (`periodic_loop`), so it is exactly periodic. Its RMS is matched to the source.
    - The loop's cycles are taken from the **steady sustain** (`sustain_s`, default 0.4 s after the onset; earlier if the sound has decayed below 20% of its peak), not from the attack's end at about 0.12 s.
    - Rows that play during the attack use **gain 1**, since the attack is raw audio with its own contour. The attack→loop handover time scales with pitch (lower notes play the attack slower).
    - The attack is resampled with a margin, so the FFT resampler doesn't ring at its end. Its last 2 cycles crossfade into the loop's periodic continuation (`attack_and_loop`).
  - **Drums:** one-shots. The rate follows each drum's bandwidth (`drum_rate`): 2.2 × the frequency below which 95% of its energy lies, in 6–26 kHz (`drum_min_rate`/`drum_max_rate`). If the bytes can't hold 95% of the energy at that rate, it's lowered by at most half. Results:
    - hats and agogo 26.3 kHz;
    - cabasa 17.6, rim 15.7, snare 11.1, quijada 10.5 kHz;
    - kick, toms, congas, bongos, timbale, claves 5.9 kHz.

    Pitched pairs are offset by whole notes.
  - **Memory:** sized and packed by NTP's rules (stopper bytes, loop split, power-of-two blocks, largest at page 0), giving **242 of 256 DOC pages** for 28 samples.
- **Timing:** rows sit on each song's detected grid unit / `rows_per_unit` (default 32nds), speed 3, MOD tempo per song (48–136). `EDx` keeps micro-timing (`quantize = 0`). The measured mean error is 4.6–12 ms.
  - **Tempo dithering** (`tempo_commands`): MOD tempos are integers, so a single tempo misses the ideal row length by up to 0.65%. Notes are placed on the ideal grid, and `Fxx` commands switch between the two neighbouring integer tempos whenever the playback clock drifts more than `tempo_tolerance_ms` (default 2 ms) from it. They go in any free effect cell. This takes 1–106 commands per song.
- **Levels (measured, `just ntpmod` prints the CC7 curve):** a note's loudness = sample peak (Munt, velocity 100, CC7 100) × its **velocity response** × **key response** (melodic) or per-key rhythm level (drum covers) × the MT-32 **CC7 curve**. All three are measured in Munt (`velocity_response`, `key_response`, `cc7_curve`). Each song is normalised so its loudest moment (99.5th percentile, including sounds that swell after the attack) is volume 64 (`level_ref` in `gs/ntp.toml` pins it).
- **Velocity placement:**
  - in the note's own cell when it needs no delay;
  - else, on a drum track that has gone silent, a `Cxx` in the preceding cell plus an instrument-less note (NTP keeps the track volume; `setup_effect` in `ninjatrackerplus.s`);
  - else the instrument's default volume (the sample's median volume in that song), corrected by the next row's envelope `Cxx`.
- **Envelope:** per-row `Cxx` follows the MT-32 loudness contour after the attack, including swells above the attack level (strings and organ reach about 2×).
  - The contour is smoothed in the **log domain over 250 ms**: tremolo and partial beating average out, and exponential decays stay unbiased.
  - Each row's `Cxx` is the contour's **mean over that row** (`env_at`), not a point sample. Release is a ramp of up to 0.3 s, then `C00`.
- **NTP-only sounds:** `[[melodic]]` entries in `gs/ntp.toml` split programs out of the shared bank's merges (`ntp_spec`), using DOC RAM the MIDI Synth bank doesn't have.
  - `keys = [lo, hi]` makes a **key split** (`sample_for`, `melodic_entry`).
  - `ntp_attack_bytes` overrides the loop-header length.
  - Currently Pipe Org 2 (MI153): "Pipe Organ" for keys 52+, sampled at key 60, and "Pipe Organ Lo" for keys ≤ 51, sampled at key 41. Both use 1016 B attacks (organs speak in about 50 ms). Total: 254/256 pages, 30 samples.
- **Tracks:** created on demand within NTP's **31-oscillator** budget (drum tracks 1, looped melodic tracks 2). When the budget is full, a track that's only releasing is stolen. There's no separate control track: speed and tempo sit in row 0 (the lead-in), and the loop `B00` goes in a free cell of the last row.
- **Channel tag:** `nCHN` below 10 channels, `nnCH` from 10. libopenmpt didn't recognise `09CH` (it rendered silence), so the format matters.

### First build (2026-11-06)
- 9–19 tracks per song, using up to 31 oscillators. Dropped notes are 0–16 per song, except MI167 (89).
- **MI160:** 797 releases stolen, 16 notes dropped.
- About 60% of notes need an `EDx` delay. Most of those start at the default volume for one row (70–150 ms) before the envelope corrects them.

### First listening (2026-11-06)
The user listened to the desktop previews (`music/ntp/wav/`): **the pipeline works**, but there are problems still to fix. The `.NTP` conversion and SenseiPlay on the IIGS are wired up (`just ntp`, `just emu-music`) but haven't been run yet; they need `php-cli`.

Known problems and open work, from the build log and analysis:
- **MI167 drops 89 notes** (MI152 and MI160 drop 16 each). Not investigated yet. Candidates: more simultaneous held notes than the 31-oscillator budget allows, or long releases that aren't stolen early enough.
- **Velocity on delayed notes:** about 60% of notes need `EDx`. Most of those start at the instrument's default volume for one row (70–150 ms) until the next row's `Cxx` corrects it. This may sound like uneven attacks. Fix options: put velocity in the preceding cell more often (needs an idle track), or pick the default volume per song from the median velocity.
- **Drums are low-fi**: one-shots at about 5.9–8.3 kHz, to fit 64 KB.
- **Level:** previews peak around −10 dB.
- **Pattern size and player CPU** haven't been measured on the IIGS. Rows are 32nds of the detected grid unit (`rows_per_unit = 2`). `gs/ntp.toml` per-song overrides can coarsen this.

User's listening notes:
1. **MI160's drums were almost inaudible.**
2. **Sustains buzzed and didn't sound tonal**, as if the loops weren't aligned.

### Fixes (2026-11-06, second build)
Diagnosed by rendering the same notes through Munt and through the MOD (ffmpeg/libopenmpt) and comparing energy, one note per sound and one channel at a time:
- **Buzzing sustains:** the loop was k raw source cycles. MT-32 tones drift from cycle to cycle (chorus, detuned partials, vibrato), so the loop's end didn't meet its start. Seam jumps were up to 70× a normal sample step, and only 26–57% of the loop's energy was harmonic for organ, strings and shakuhachi, so it buzzed at the loop rate (rate/512, about 20–40 Hz). The attack→loop junction also jumped, because the attack and the loop were FFT-resampled separately.
  - **Fix:** a tiled averaged cycle and a crossfaded junction (above). All 14 loops are now >99.9% harmonic.
  - **Trade-off:** chorus and ensemble movement inside the sustain are gone. A future option: a longer loop, or two detuned tracks.
- **Wrong mix: ±7 dB per sound in single-note tests** (one note per sound, compared with Munt). Causes:
  - **CC7 was ignored.** MI160 sets the drums to 120 and melodic parts to 52–127. The MT-32 curve is steep. Measured in Munt, gain relative to CC7 100, the same on melodic and rhythm parts: 127 → 1.83, 120 → 1.61, 90 → 0.77, 77 → 0.55, 64 → 0.35, 52 → 0.21, 40 → 0.11, 20 → 0.02.
  - **Velocity was treated as linear.** The MT-32's velocity response differs per timbre. Velocity 127 vs 100: closed hat ×2.49, strings and snare ×1.76, kick ×1.54, bass ×1.41, horn ×1.23. Velocity 40: hat 0.13, bass 0.44.
  - **Levels came from RMS, with a √ compression**, not from the sample's peak. MOD output = volume × normalised data, so volume ∝ source peak reproduces the MT-32's level exactly.
  - **Drums at 5.9–8.3 kHz:** the old rule stretched each drum over its whole −46 dB tail. Hats, cabasa and agogo have 90% of their energy at 11–15 kHz but only 40–60 ms of real energy, so a 3 kHz Nyquist removed nearly all of them: 6–14 dB too quiet.
  - **"Headroom" scaled swelling sounds down.** It multiplied the whole note, not just the attack, so organ, strings, shakuhachi and Fantasy were 6–9 dB too quiet. Fix: the envelope uses the true gain and normalisation includes the swell.

  **After the fixes**, every sound is within ±2.5 dB of the MT-32 in single-note tests. In MI160, drums sit 6.8 dB under the melodic parts (MT-32: 6.1 dB); in MI150, 7.8 dB (MT-32: 6.7 dB).
- **Preview pitfall:** libopenmpt scales its MOD mix by channel count. Compare stems only at equal channel counts. This doesn't affect the mix within one song.

### Grid analysis for quantization (2026-11-06)
Measured on the onsets of all 16 songs (scripts were ad hoc; method below).
- **The detected unit is the music's real subdivision; finer grids only catch jitter.** Median snap error to unit/2 is barely below unit/1 (e.g. MI150 9.8 → 9.8 ms; uniformly random onsets would give u/4 → u/8, 38 → 19 ms). unit/3 and unit/4 give what random onsets would. So there are no systematic 32nds, and no triplets inside duple songs.
- **Triplet feel is already captured by the unit.** Circular concentration at 3 × unit is high (R 0.22–0.47) for 159, 161–165, 167 and 168: there the unit is a triplet 8th, a beat is 3 units. For the others the beat is 2 or 4 units (153: R 0.46 at 4u).
- **Tempo drifts.** A local phase (fitted over ±4 units) wanders 22–85 ms (5–95th percentile) across a song. Following it cuts the median deviation sharply for some songs (MI160 18.9 → 7.9 ms, MI163 13.2 → 4.2, MI150 9.8 → 5.9), so a fixed grid is wrong by up to ~40 ms in places.
- **Some songs barely sit on any grid** (152, 158: the median deviation is about half of random even with a local phase). Expect rubato, or a unit to recheck.
- **Pattern cost:** NTP patterns are uncompressed: 64 rows × tracks × 4 B = 256 B per track per pattern. MI160 now: 18 patterns × 17 tracks ≈ 78 KB. 32 rows per beat would be ~4× the current 8 (≈ 310 KB) and force speed 1 (a new row every tick: more player CPU).
- **Grid drift from tempo rounding** (fixed by tempo dithering, above). With one fixed integer tempo, the row-length error and the drift it accumulates over each song were:
  - MI160 −0.37% (−347 ms, 4.1 rows); MI152 +0.52% (+375 ms, 3.1 rows); MI153 −0.65% (−368 ms, 2.4 rows);
  - MI150, 165 and 167 about ±0.4% (1.7 rows);
  - 161–164 negligible.

  This came on top of the performance's own drift. After the fix, delayed (`EDx`) notes fell, e.g. MI150 586 → 280, MI153 97 → 30, MI159 291 → 98, MI165 245 → 79, MI160 1962 → 1540.

### MI153: velocity "jumping" (2026-11-06)
User report: velocity jumps up and down unnaturally in MI153, which is a single Pipe Org 2 part.
- **Cause 1, the wrong sound's envelope.** MI1.BNK merges Pipe Org 2 into Elec Org 2's sample. Measured in Munt, Elec Org 2 has a deep, irregular rotary-style wobble: level 0.24–1.0, peak modulation about 1.8 Hz, depth 36%. Pipe Org 2 is steady: about 0.6 Hz and 13% depth. The two have the same velocity response (127/100 ≈ 1.45) and similar key response (within about 30%).
- **Cause 2, aliasing.** Per-row `Cxx` point-sampled that wobble every 154 ms (MI153's row length), producing volume sequences like C0A → C25 → C13 → C0E → C28.
- **Fix:** an NTP-only Pipe Organ sample, log-domain smoothing, and row-mean envelope sampling (above). The organ now follows Pipe Org 2's real slow swell (volume 29 → 40 → 29 over about 1 s).

## On the IIGS (verified in GSSquared, 2026-11-06)
- **`just emu-ntp`** boots `gs/disks/MI.NTP.PO` (`just ntpdisk`). This is a ProDOS 8 BazFast disk made from SenseiPlay's own `PRODOS` + `sensei.system` + `ntpplayer` + `dgr.radio.dgr`, with our songs in `/songs/PART1` and `/songs/PART2`. It boots straight into SenseiPlay. `just emu-music` also has them, under `/SenseiPlay/MI/PARTn`.
- **Checked in the emulator** by driving it through gs2-mcp (`tap_key`, `video_text`, `state_get`, `read_mem ENSONIQ`):
  - MI150, MI160 and MI165 play, with 10–17 oscillators running and frequencies and volumes changing;
  - DOC RAM holds every sample byte for byte at the page the `.NTP` header gives;
  - the whole file is consumed by the header layout in `file_format.txt`.

  Not yet listened to by ear.
- **Converter loop rotation** (`split_rotate_loop`, since Nov 2019, "to reduce the swap bug"): each loop is rotated to start at its first sample nearest zero (unsigned 128), and the skipped bytes are appended to the loop header. It has no switch. With our 2040 B headers that made 2094 B + 8 stopper bytes, i.e. a 4 KB block per melodic sound, so DOC RAM overflowed ("Too large"). Fix: our loops start at a rising zero crossing with `loop[0] = 0` exactly, so the rotation is a no-op. The attack window shifts up to one period earlier to meet it.
- **SenseiPlay v2.0 directory bug:** it skips the first entry of each directory block after the first. A ProDOS directory's first block holds the header + 12 entries, so the 13th file is the first entry of block 2. With all 16 songs in one folder, MI165 was missing from the list. Workaround: at most 12 songs per folder (`ntp_folders` in the justfile).
- **Navigation:** Down/Return move through the browser (".." comes first); Return on a song plays it.

### MI153: low notes "not coming across" (2026-11-07)
User report: heard on the IIgs.
- **Cause 1, the loop was taken from the attack.** It was taken right where the 2040 B attack ends (about 0.12 s), before Pipe Org 2's fundamental has built up. Even at the sampled key (60), the MT-32 has 61% of its sustained energy in the fundamental; the loop had 17%, with 34% in the 2nd harmonic. So the whole organ was thin.
  - **Fix:** take the loop from the steady sustain, with a 4-cycle raised-cosine crossfade from the attack. To keep the converter's rotation a no-op, the loop still starts at a rising zero crossing; of those, it picks the one whose phase best matches the attack's tail.
  - **After:** harmonic profiles at each sound's sampled key are within 0.12 (half the L1 distance over 8 harmonics) for 12 of the 14 shared sounds. Strings are at 0.19: averaging a chorused ensemble thins its upper harmonics.
- **Cause 2, the MT-32 voices Pipe Org 2's low register differently.** Fundamental share by key: 36 → 5%, 40 → 2%, 43 → 29%, 48 → 25%, 55 → 52%, 60 → 61%. Below key 52, most of the energy is in the 2nd–4th harmonics. Transposing the key-60 sample down put about 50% of the energy at 65–98 Hz, where small speakers have little output.
  - **Fix:** the key-split low sample (above). Keys 40–51 now match: about 26% fundamental, 31% 2nd harmonic.
  - **Remaining:** keys 36–39, where the MT-32 is 3rd-harmonic heavy, would need a third sample. Only 2 DOC pages are left.
  - In-song balance: low notes (below key 52) sit 8.6 dB under the upper notes in the MOD, 7.1 dB on the MT-32.
- **Side bug found:** with the loop taken later, rows inside the attack were scaled by the loop-relative gain. That made decaying sounds' attacks too loud (Taiko +5 dB in its first 100 ms). Fixed with gain 1 during the attack.

## Sample-set modes and long loops (2026-11-07)
NTP files carry their own samples, and `NTPprepare` copies them into DOC RAM for each song. So the MIDI Synth "one shared bank" constraint doesn't apply. Both modes are built side by side (`ntp_modes` in the justfile):
- **`per-song`** (default `samples` in `gs/ntp.toml`): each song carries only the sounds it plays (`sample_set`). Its free DOC RAM, after `reserve_pages` (16 pages = 4 KB for game sound effects), goes to longer loops (`plan_loops`). The planner repeatedly doubles the loop with the most sustained playing time per byte, while everything still packs the way NTP packs it, up to `max_loop_bytes` (16 KB) and the recording's length. Example: MI153 carries 3 organ samples with 16 KB loops (204 pages).
- **`shared`**: one set for every song (union of sounds and key ranges; loops planned over all songs' usage). `[modes.shared]` sets `reserve_pages = 0`, because the full set needs 256/256 pages with 512 B loops. NTP-only sounds can be limited with `modes = [...]` (Pipe Organ Lo2 is per-song only; in shared mode keys 36–39 fall back to the nearest split).
- **Long loops** (`raw_loop`) are for **steady** sounds only: the level must hold within 6 dB from the sustain start to note-off (`is_steady`). They hold k recorded periods (chorus, beating and vibrato kept), with a crossfade seam: the last 25% fades into the audio just before the loop start. Decaying sounds (bass, marimba, vibes, xylophone, horn) keep the 512 B averaged-cycle loop, because a raw loop would bake the decay in and pump.
- **The loop start is still exactly zero** (`zero_start`): a fractional FFT rotation of the periodic loop. Forcing the start sample to 0 left a step of up to 11× a normal sample step at steep crossings, a click every pass (heard as a buzz in MI153, about 25 Hz).

### Ninjaforce converter: oscillator assignment bug
`determine_osc_per_track` assigns oscillators first-fit in track order over 0–30, with 2-oscillator tracks starting on even oscillators. Interleaved 1- and 2-oscillator tracks strand odd oscillators, so tracks can get no oscillator even with ≤ 31 in total. The converter reports no error: it writes 0 (PHP "Undefined array key 17/18" at `ntpconverter_lib.php:1613`), so those tracks use oscillator 0, which clashes with track 1.
- **Fix:** `convert_song` emits all melodic (2-oscillator) tracks first, then drums. `ntp_oscillators` replays the converter's assignment, and `main` fails the build if it wouldn't fit.

### Disk folder order
SenseiPlay lists entries in directory order, and `cp2 add` of a tree follows the host's readdir order: `SHARED` came before `PER.SONG`, and `PART2` before `PART1`. `ntpdisk` and `hdimage` now `cp2 mkdir` the folders in sorted order, then add each folder's files sorted.
