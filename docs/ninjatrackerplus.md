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
The MOD is the primary artefact: it renders on the desktop (ffmpeg's libopenmpt → `build/ntp/wav/`), and Ninjaforce's converter (`php-cli`, `STREAM_FORBIDDEN`) turns it into `.NTP`. Settings live in `config/ntp.toml`; sounds come from `config/mt32bank.toml` (the same 14 melodic and 14 drum sounds as MI1.BNK).

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
- **Levels (measured, `just ntpmod` prints the CC7 curve):** a note's loudness = sample peak (Munt, velocity 100, CC7 100) × its **velocity response** × **key response** (melodic) or per-key rhythm level (drum covers) × the MT-32 **CC7 curve**. All three are measured in Munt (`velocity_response`, `key_response`, `cc7_curve`). Each song is normalised so its loudest moment (99.5th percentile, including sounds that swell after the attack) is volume 64 (`level_ref` in `config/ntp.toml` pins it).
- **Velocity placement:**
  - in the note's own cell when it needs no delay;
  - else, on a drum track that has gone silent, a `Cxx` in the preceding cell plus an instrument-less note (NTP keeps the track volume; `setup_effect` in `ninjatrackerplus.s`);
  - else the instrument's default volume (the sample's median volume in that song), corrected by the next row's envelope `Cxx`.
- **Envelope:** per-row `Cxx` follows the MT-32 loudness contour after the attack, including swells above the attack level (strings and organ reach about 2×).
  - The contour is smoothed in the **log domain**: over 250 ms for decaying sounds (decays stay unbiased), and over 0.8 s (at least the raw loop's length) for steady sounds, so the rows carry only the slow contour (see "Smoother sustains" below).
  - Each row's `Cxx` is the contour's **mean over that row** (`env_at`), not a point sample. Release is a ramp of up to 0.3 s, then `C00`.
- **NTP-only sounds:** `[[melodic]]` entries in `config/ntp.toml` split programs out of the shared bank's merges (`ntp_spec`), using DOC RAM the MIDI Synth bank doesn't have.
  - `keys = [lo, hi]` makes a **key split** (`sample_for`, `melodic_entry`).
  - `ntp_attack_bytes` overrides the loop-header length.
  - Currently Pipe Org 2 (MI153): "Pipe Organ" for keys 52+, sampled at key 60, and "Pipe Organ Lo" for keys ≤ 51, sampled at key 41. Both use 1016 B attacks (organs speak in about 50 ms). Total: 254/256 pages, 30 samples.
- **Tracks:** created on demand within NTP's **31-oscillator** budget; see "Track allocation" below for the full rules. There's no separate control track: speed and tempo sit in row 0 (the lead-in), and the loop `B00` goes in a free cell of the last row.
- **Channel tag:** `nCHN` below 10 channels, `nnCH` from 10. libopenmpt didn't recognise `09CH` (it rendered silence), so the format matters.

### First build (2026-11-06)
- 9–19 tracks per song, using up to 31 oscillators. Dropped notes are 0–16 per song, except MI167 (89).
- **MI160:** 797 releases stolen, 16 notes dropped.
- About 60% of notes need an `EDx` delay. Most of those start at the default volume for one row (70–150 ms) before the envelope corrects them.

### First listening (2026-11-06)
The user listened to the desktop previews (`build/ntp/wav/`): **the pipeline works**, but there are problems still to fix. The `.NTP` conversion and SenseiPlay on the IIGS are wired up (`just ntp`, `just emu-music`) but haven't been run yet; they need `php-cli`.

Known problems and open work, from the build log and analysis:
- **MI167 drops 89 notes** (MI152 and MI160 drop 16 each). *Resolved 2026-11-07:* see "Track allocation" (drum tracks borrowing idle melodic tracks).
- **Velocity on delayed notes:** about 60% of notes need `EDx`. Most of those start at the instrument's default volume for one row (70–150 ms) until the next row's `Cxx` corrects it. This may sound like uneven attacks. Fix options: put velocity in the preceding cell more often (needs an idle track), or pick the default volume per song from the median velocity.
- **Drums are low-fi**: one-shots at about 5.9–8.3 kHz, to fit 64 KB.
- **Level:** previews peak around −10 dB.
- **Pattern size and player CPU** haven't been measured on the IIGS. Rows are 32nds of the detected grid unit (`rows_per_unit = 2`). `config/ntp.toml` per-song overrides can coarsen this.

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
- **`just emu-ntp`** boots `build/disks/MI.NTP.PO` (`just ntpdisk`). This is a ProDOS 8 BazFast disk made from SenseiPlay's own `PRODOS` + `sensei.system` + `ntpplayer` + `dgr.radio.dgr`, with our songs in `/songs/<MODE>` (`PER.SONG`, `SHARED`, plus any trial variants), all 16 in one folder. It boots straight into SenseiPlay. `just emu-music` also has them, under `/SenseiPlay/MI/<MODE>`.
- **Checked in the emulator** by driving it through gs2-mcp (`tap_key`, `video_text`, `state_get`, `read_mem ENSONIQ`):
  - MI150, MI160 and MI165 play, with 10–17 oscillators running and frequencies and volumes changing;
  - DOC RAM holds every sample byte for byte at the page the `.NTP` header gives;
  - the whole file is consumed by the header layout in `file_format.txt`.

  Not yet listened to by ear.
- **Converter loop rotation** (`split_rotate_loop`, since Nov 2019, "to reduce the swap bug"): each loop is rotated to start at its first sample nearest zero (unsigned 128), and the skipped bytes are appended to the loop header. It has no switch. With our 2040 B headers that made 2094 B + 8 stopper bytes, i.e. a 4 KB block per melodic sound, so DOC RAM overflowed ("Too large"). Fix: our loops start at a rising zero crossing with `loop[0] = 0` exactly, so the rotation is a no-op. The attack window shifts up to one period earlier to meet it.
- **SenseiPlay v2.0 directory bug:** it skips the first entry of each directory block after the first. A ProDOS directory's first block holds the header + 12 entries, so the 13th file is the first entry of block 2. With all 16 songs in one folder, MI165 was missing from the list.
  - **Cause** (SenseiPlay `src/p8lib.s`, tag v2.0): after each directory-block READ it points at offset 4 and steps one entry length before the first read. That skips the header in block 1, but in every later block it skips a real file. Entries 13, 26, 39, … of a folder are never shown.
  - **Workaround** (since 2026-11-07; before that, `PART1`/`PART2` folders of at most 12): `p8_tree` in the justfile adds a folder's files in sorted order and puts a small text file, `GAP.<n>`, in each skipped slot. `cp2 add` sorts the files of one call, so each run between placeholders is added separately. Verified in GSSquared: `PER.SONG` lists all 16 songs, with no `GAP.13`.
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
- **`per-song`** (default `samples` in `config/ntp.toml`): each song carries only the sounds it plays (`sample_set`). Its free DOC RAM, after `reserve_pages` (16 pages = 4 KB for game sound effects), goes to longer loops (`plan_loops`). The planner repeatedly doubles the loop with the most sustained playing time per byte, while everything still packs the way NTP packs it, up to `max_loop_bytes` (16 KB) and the recording's length. Example: MI153 carries 3 organ samples with 16 KB loops (204 pages).
- **`shared`**: one set for every song (union of sounds and key ranges; loops planned over all songs' usage). `[modes.shared]` sets `reserve_pages = 0`, because the full set needs 256/256 pages with 512 B loops. NTP-only sounds can be limited with `modes = [...]` (Pipe Organ Lo2 is per-song only; in shared mode keys 36–39 fall back to the nearest split).
- **Long loops** (`raw_loop`) are for **steady** sounds only: the level must hold within 6 dB from the sustain start to note-off (`is_steady`). They hold k recorded periods (chorus, beating and vibrato kept), with a crossfade seam: the last 25% fades into the audio just before the loop start. Decaying sounds (bass, marimba, vibes, xylophone, horn) keep the 512 B averaged-cycle loop, because a raw loop would bake the decay in and pump.
- **The loop start is still exactly zero** (`zero_start`): a fractional FFT rotation of the periodic loop. Forcing the start sample to 0 left a step of up to 11× a normal sample step at steep crossings, a click every pass (heard as a buzz in MI153, about 25 Hz).

### Ninjaforce converter: oscillator assignment bug
`determine_osc_per_track` assigns oscillators first-fit in track order over 0–30, with 2-oscillator tracks starting on even oscillators. Interleaved 1- and 2-oscillator tracks strand odd oscillators, so tracks can get no oscillator even with ≤ 31 in total. The converter reports no error: it writes 0 (PHP "Undefined array key 17/18" at `ntpconverter_lib.php:1613`), so those tracks use oscillator 0, which clashes with track 1.
- **Fix:** `convert_song` emits all melodic (2-oscillator) tracks first, then drums. `ntp_oscillators` replays the converter's assignment, and `main` fails the build if it wouldn't fit.

### Disk folder order
SenseiPlay lists entries in directory order, and `cp2 add` of a tree follows the host's readdir order: `SHARED` came before `PER.SONG`, and `PART2` before `PART1`. `ntpdisk` and `hdimage` now `cp2 mkdir` the folders in sorted order, then add each folder's files sorted (`p8_tree`).

## Shared set as a resident instrument bank (2026-11-07)
Goal: a game loads the instruments once and leaves them in DOC RAM, then loads only each song's sequence data from disk. We don't write that loader; the shared-mode output is built to make it possible.

**Patch inventory** (all 16 songs, every channel, every note):
- No song changes program mid-song, so recording the first program per channel (`read_notes`) is exact.
- Every program on every channel maps to a shared-set sample (`melodic_entry`), and every rhythm key but one maps to a drum sample.
- The exception is rhythm key 76, one hit in MI166. It's silent on a stock MT-32 (Munt renders nothing) and is dropped, as in the GM conversion (`docs/mt32-to-gm.md`).

**Byte-identical bank in every shared `.NTP`:**
- **Samples and DOC placement** were already identical: the same sample set, same order, packed by the converter the same way.
- **Default volumes** used to differ per song. Shared mode now uses one loudness reference for all songs (`level_ref` = the loudest song's) and sets each sample's default volume to its median note-on volume over all songs. A side effect is consistent levels between songs.
- `just ntp` runs `ntpcheck --shared` (`src/migs/ntpfile.py`), which fails if any shared song's instrument records or sample data differ.

**4 KB sound-effect reserve, always** (`reserve_pages = 16` in both modes):
- The full shared set needed 254 pages with 512 B loops. `[modes.shared] ntp_attack_bytes` gives 1016 B attacks to the four sounds where that costs least, by mean log-spectral distance vs Munt over the first 0.6 s at the sampled key:

  | sound | cost |
  |---|---|
  | Elec Organ | −0.28 dB |
  | Sax | −0.10 dB |
  | Acou Bass | +0.04 dB |
  | Vibes | +0.06 dB |

  Others cost more: Pipe Organ +0.57, Panpipes +1.00, and Xylophone +28 (its whole sound is the attack).
- Result: 240/256 pages, highest page used 239, so pages 240–255 are free in every song. Per-song mode keeps the same reserve (e.g. MI153 uses pages 0–203).

**A game loader's view** (`ntpfile.NTP`):
- **Bank, loaded once** (62,444 B): the `instruments` section (14-byte records + names: type, length, loop, volume, finetune, DOC page, DOC size) and the `samples` section. Copy each sample to its DOC page. The instrument records stay in RAM for the player.
- **Per song** (7–78 KB; 534 KB for all 16): `header` + `tracks` + `order` + `patterns` (`NTP.song_part`).
- The stock `NTPprepare` expects one contiguous file. A resident-bank game would rebuild that image in RAM (song header + resident instrument records + song order and patterns), skipping the sample copy. Alternatively it could patch the player to keep its instrument table and skip `NTPprepare`'s DOC upload. That's outside this project's scope; `ninjatrackerplus.s` is the reference.

## Build speed (2026-11-07)
`just ntpdisk` (both modes, previews, `.NTP`, bank check, boot disk) went from about 4 min to **22 s**. All 64 MOD/NTP outputs are byte-identical to the build before the changes (sha256).
| cause (cProfile) | fix |
|---|---|
| `smooth()`: `np.convolve` with 250 ms (8000-tap) windows, O(n·w); 36 of 56 s in shared mode | cumulative-sum moving average, O(n), the same window as `np.convolve(..., "same")` (max deviation ~1e-12) |
| velocity/key responses re-measured from the same WAVs for every song (3177 `rms_envelope` calls, 3000 WAV reads) | in-process `_render` cache (read-only arrays) plus `_peak` memo. `mt32bank` is untouched, so the MIDI Synth bank can't change |
| `_best_tuning`: Python triple loop (k × finetune × note) | NumPy over (k, finetune, note). `argmin` picks the first minimum in the same scan order, so the same tuning is chosen (tested against the loop) |
| MIDI parsing and grid detection repeated per pass | `read_notes` cached by path + mtime, `detect_grid` by onsets |
| recipes serial | modes build concurrently; ffmpeg previews and PHP conversions run on all cores (`xargs -P $(nproc)`) |

`ntpmod` itself: per-song mode 169 s → 10 s, shared mode 49 s → 10 s.

## Smoother sustains (2026-11-07)
User report: shared-version sustains "still a little choppy". Measured from the MOD patterns, on held notes: note-volume level, ±1 flicker reversals, and step sizes. Then fixed in four steps:
1. **Volume resolution (shared only).** The common loudness reference from the resident-bank work left quiet songs at MOD volume ~4 (MI153: median 4, 29.5% flicker, 2.5 dB per step; per-song: 32, 0.1%). **Fix:** shared mode normalises each song to its own loudest moment again, like per-song. Bank-compatible default volumes are each sample's median over the per-song-normalised volumes. The MT-32's song-to-song balance goes to `build/ntp/shared/levels.toml` (`gain_db`, `play_volume_scale`) for a game's `NTPsetplayvolume`.
2. **Rounding flicker.** A slowly moving target near x.5 flipped the volume 4→5→4. **Fix:** `volume_hysteresis` (0.75): a row changes volume only when the target has moved ≥ 0.75 of a step from the current value.
3. **Handover step.** The loop was level-matched to the source at the sustain point (0.4 s), not where the attack ends. Every note stepped at the attack→loop handover, against an MT-32 that doesn't: Strings +3.1 dB vs +1.2, French Horn −3.1 vs −0.4, Fantasy +1.7 vs −1.4. **Fix:** the loop is level-matched to the attack's tail, and the envelope eases from gain 1 to the contour over `handover_s` (150 ms, linear in dB). Handover changes are now within ±0.7 dB, following the MT-32's direction (Shakuhachi's real −4.4 dB fade is eased to −2.2).
4. **Stepped tremolo.** Steady sounds' chorus/beating (Chorale: −1…−8 dB at 2.5 Hz) passed the 250 ms smoothing as ±1-step row flips: a stepped tremolo on a static loop. A raw loop already contains that modulation, so the rows doubled it. **Fix:** steady sounds smooth their contour over `env_smooth_steady_s` (0.8 s), and at least the raw loop's length.

Results (flicker % of held-note volume triples; median note volume):

| song | shared before | shared after | per-song after |
|---|---|---|---|
| MI153 | 4 vol, 29.5% | 32 vol, 3.7% (±1 at vol 32 ≈ 0.3 dB) | 30 vol, 5.7% |
| MI159 | 4 vol, 7.3% | 10 vol, 2.2% | 10 vol, 0.2% |
| MI160 | 10 vol, 3.5% | 24 vol, 0.2% | 24 vol, 0.5% |
| MI161 | 7 vol, 3.4% | 20 vol, 1.4% | 20 vol, 0.4% |

Single-note levels vs Munt are unchanged (all within about ±3 dB, as before). The shared bank check still passes.

Not done, noted for later: a longer attack→loop crossfade (12 vs 4 cycles) made no measurable difference to the handover spectral flux.

## Loop lab: why the shared loops still buzzed (2026-11-07)
Measured with `just looplab`; details, sources and the full method comparison are in `docs/loop-detection.md`.
- **The converter is innocent:** NTP header and loop bytes equal the MOD bytes + 128.
- **8-bit rounding:** the averaged-cycle loops were exactly periodic only in float. With a fractional number of samples per cycle (e.g. 512 B / 15 = 34.13), each cycle rounds differently. The error repeats once per loop: a loop-rate buzz at −37 to −48 dB in 10 of the 16 shared sounds (Xylophone −21 dB). Loops with a whole number of samples per cycle had none.
- **`zero_start` forced `loop[0] = 0`** at a linear-interpolation crossing. The band-limited wave isn't 0 there (up to 1 LSB), so one sample per pass was wrong.
- **What fixes both:**
  - k must divide the loop length;
  - band-limit one cycle and tile it;
  - put an exact (Newton) band-limited zero crossing at `loop[0]`;
  - shift the attack by up to one period so it ends in phase with the loop.

  With a power-averaged harmonic cycle as well, there is no buzz in any sound, the timbre is within 0.6 dB of the MT-32's sustain average, and handover steps are ≤ 0.3 dB. Not integrated yet: waiting for the user's listening verdict.
- **Not swells but beats:** the "slow swells" in Strings, Horn, Fantasy, Shakuhachi and Chorale are deep beating between detuned partials, at 0.4–0.9 s and 5–13 dB. A 512 B loop can't hold it.

### One-shots (2026-11-07)
Decaying melodic sounds that reach the 8-bit floor within the DOC RAM their attack + loop would take are stored whole, with exact tuning. Today that is only Xylophone. Rule and measurements: `docs/loop-detection.md`, "One-shots". The freed pages stay free (the shared bank is now 238/256). The build log now lists dropped notes per sound (`MInnn dropped (no oscillator): ...`).

### Tremolo from short raw loops (2026-11-07)
User report: a noticeable, unpleasant tremolo on a prominent instrument in shared MI158 and MI160 (the organ), absent from per-song and from the MT-32.

**Cause, measured.** Shared Elec Organ was the one raw loop left in the shared bank: 1024 B, 65 ms, 17 cycles. That is a slice of the organ's rotary wobble (≈ 0.53 s, 12.6 dB), so its cycle levels span **6.3 dB**, replayed **15.3 times a second**. In per-song the loop is 8192 B (1.1 Hz) and holds about two wobble periods, so it moves the way the MT-32 does. The `Cxx` contour isn't the cause: every averaged loop has ≤ 0.2 dB of level range. The same defect was in per-song MI160: Fantasy 2048 B, 6.1 dB at 5.6 Hz; Shakuhachi 2048 B, 2.5 dB at 5.4 Hz.

**Fix: a measured gate** (`raw_loop_ok`; config `raw_loop_slow_hz`, `raw_loop_max_tremolo_db`, `raw_loop_max_buzz_db`). Every raw loop size a steady sound might grow to is built and measured, and only passing sizes go into `raw_ok`. The loop planner grows only through those, skipping sizes that fail.

A size passes when either:
- **it repeats slowly** (≤ 2 Hz), so its movement reads as the sound's own wobble; or
- **it is as clean as the averaged loop it replaces:** the level range across its cycles is ≤ 1 dB (`loop_tremolo_db`) *and* its loop-rate content is ≤ −50 dB (`loop_buzz_db`). For comparison, Trombone at 1024 B had no tremolo but −39 dB of buzz.

**Build check:** the build fails if any loop in either mode fails the gate (`loop_problems`).

**Result:**
- **Shared:** every sound now has a 512 B averaged loop, with no raw loops. Elec Organ has 0.1 dB of level range (was 6.3 dB). The bank is 236/256 pages, and the freed pages stay free.
- **Per-song:**
  - MI160: Fantasy 2048 B → 512 B averaged; Shakuhachi 2048 B → 8192 B (1.4 Hz).
  - MI163: Sax 4096 B → 512 B averaged, and Trombone grows to 16384 B.

  Note statistics are unchanged, except 9 more steals in shared MI160.

### E′+ loops: trialled, then the standard (2026-11-07)
E′+ was first built as trial variants, `per-song-e` and `shared-e` (`[modes.<name>]` in `config/ntp.toml`: `base` + `loop_construction = "eprime"`), next to `per-song` and `shared`. After listening (user, 2026-11-07), it became the default:
- `loop_construction = "eprime"` is set in `[defaults]`.
- The variants were dropped, and the new `per-song` / `shared` are byte-identical to the old `-e` builds (64 MOD/NTP checksums).
- The original averaged-cycle loops stay selectable with `loop_construction = "averaged"`. A variant using them, `base = "shared"` + `"averaged"`, reproduces the old `shared` byte for byte.
- The code is in `src/migs/loopbuild.py`, moved from the research harness `looplab`.
- **What changes:** only the short (512 B) loops, which are built as E′+ (`ntpmod.eprime_attack_and_loop`, using `loopbuild`):
  - whole samples per cycle (`_best_tuning(whole_cycles=True)`);
  - a power-averaged harmonic cycle over the sustain detected by `looplab.settle`;
  - an exact band-limited zero crossing at the loop start;
  - an attack aligned in phase with the loop.

  Long raw loops are unchanged, and still pass through the tremolo/buzz gate.
- **Measured on the built `shared-e` bank:** the 8-bit loop-rate buzz is gone in all 15 looped sounds. In `shared`, 10 have it, at −37 to −48 dB. Tuning error grows to at most 3.5 cents (Pipe Organ Lo).
- **When the variants were added,** the original builds were unchanged: `per-song` and `shared` were byte-identical to before (64 MOD/NTP checksums). Both shared banks passed `ntpcheck --shared`, at 236/256 pages.

## Ghosts: delayed notes revive the previous note's loop (2026-11-07)
User report: SenseiPlay has abrupt note endings that sound like evictions, absent when VLC plays the same MODs.

**Cause** (NinjaTrackerPlus source, `vendor/ntp/src/ninjatrackerplus.s`, `setup_effect` and `setup_note_delay`):
- A cell's instrument number is applied at **tick 0**, and so is its default volume (`what_to_do |= 1`, written to the DOC that tick).
- `EDx` postpones only the **note** (`note_delay_note`). The old oscillators are stopped and restarted only at tick x.
- Our note-offs fade to `C00`, and a looped sound keeps running silently after that.

So on every delayed note, NTP first turned the *previous* note's loop up to the new instrument's default volume, for 1–2 ticks (23–56 ms), then cut it. Desktop players delay the whole cell (libmodplug, libopenmpt), so they never play it.

There were about 3,500 such ghosts per build. Examples: MI152 ≈ 600, MI160 ≈ 500–580, MI163 ≈ 490.

**Confirmed in GSSquared:**
- Test MOD: a looped organ note, `C00`, then `ED2` notes with an instrument number. SenseiPlay was driven through `gs2-mcp` (keys plus `video_text`), and the DOC registers were polled at 60 Hz.
- At the delayed row, the old loop oscillator (still at the old pitch, freq 327) went from volume 0 to 160. It ran for 100 ms (2 ticks at tempo 50), then the new note (freq 489) started.
- A non-delayed control note had no ghost.
- libopenmpt renders the same window as silence (−180 dB).

**Fix** (`halt_released_loops`, on in every variant):
- `build_samples` adds a silent 248 B one-shot, `rest`: 1 page and 1 sample slot. Shared now has 31 samples, the MOD maximum, and 237/256 pages.
- Where a release reaches volume 0, `convert_song` writes `C00` *plus* a rest note into that cell. Triggering the rest stops the loop's oscillators, and the rest halts itself on its stopper bytes. MOD 0 = DOC 128 = silence; DOC 0 would stop it at once.
- In GSSquared, at the same delayed rows the volume still rises at tick 0, but every oscillator is halted, so nothing sounds.
- The built MODs now have 0 ghosts in all four variants built then (per-song, shared and their E′+ trials), out of about 5,400 delayed notes each. The build log's `halt` column counts the halted releases.

**Desktop output:** unchanged apart from inaudible mixer residue (−68 to −71 dB, at most about 20 LSB), as long as the rest doesn't alter track choice: `trk_inst` keeps the note's sample. Marking released tracks as "rest" had moved notes between the panned channels.

**One cost:** per-song MI167 was exactly at its page limit, so the rest's page halved its Strings loop (16384 → 8192 B, about one beat long; it passes the tremolo gate).

**Still open (theory 2):** steals, i.e. a releasing note cut for a new one, are abrupt on the DOC. NTP halts oscillators outright, with no declick ramp, where desktop players ramp over a few milliseconds. MI160 had about 620–780 of them; since 2026-11-08 most fall on a release that has already reached 0 (see "Track allocation", step 4).

## Build options: tradeoffs (2026-11-08)
What each option buys and costs, as measured. Sizes are totals over all 16 songs. Pattern data is 256 B per track per 64-row pattern, so allocation options change it through the track count; sample options change the sample data. Where a cost hasn't been measured, the entry says so.

**Sample sets**
- **`samples = "per-song"` vs `"shared"`.** *Per-song:* each `.NTP` carries only its own sounds, and free DOC RAM grows steady sounds into long raw loops (better sustains; per-song Elec Organ's 8192 B loop holds its rotary wobble). *Shared:* one bank for every song, byte-identical in each file, so a game can keep it resident; every sound gets a 512 B loop, and four attacks are shortened to fit (below). *Size:* per-song 1142 KB of `.NTP`, shared 1480 KB, because every shared file carries the whole ~60 KB bank; with a resident bank a game loads only header + patterns per song (shared patterns 516 KB). *Polyphony:* about the same (shared drops 2, audible steals 180; per-song 3, 280).
- **`ntp_attack_bytes` (shared: Elec Organ, Sax, Acou Bass, Vibes at 1016 B).** Keeps the 16-page sound-effect reserve in the shared bank (−16 pages), at the least measured cost: log-spectral distance vs Munt over the first 0.6 s changes −0.28…+0.06 dB.
- **`reserve_pages = 16`.** 4 KB of DOC kept for game sound effects; it limits loop growth (per-song) and forces the shortened shared attacks.

**Sounds**
- **`one_shots`** (default on). A decaying sound that fades into 8-bit noise within its attack + loop budget is stored whole: only Xylophone qualifies. *Gains:* exactly in tune (was −1.0 cents), no loop of quantisation noise, 1 oscillator, frees its track when the sample ends (MI162 drops 8 → 0). *Cost:* none measured; shared bank 240 → 238 pages.
- **`loop_only`** (trial `per-song-lo` / `shared-lo`, Elec Organ). *Gains:* 1 oscillator instead of 2: drops 11 → 1, audible steals 262 → 195, and with the track plan and one-shot routing 184 → 39 (shared). *Costs:* the attack is lost. Over the first 100 ms the tone is about 2 dB further from Munt, and the first 0.2–0.3 s is quieter: the 5.4 dB onset peak and the gain-1 handover are gone. **Listening: clearly audible and significantly quieter, much more so in shared.** *Size:* patterns +29…+30 KB (more 1-oscillator tracks), samples −16…−20 KB (no attack headers), `.NTP` +10…+13 KB; DOC −4 pages (shared 237 → 233). Undecided.
- **Song-sized one-shots** (rejected 2026-11-08): +100 KB of samples (+9 %) for no polyphony gain and little audible difference; see `docs/loop-detection.md`.
- **`halt_released_loops`** (default on). Ends each release with a silent one-shot that stops the loop. *Gains:* no ghost of the previous note before a delayed note. *Cost:* one 256 B rest sample per set; per-song MI167 was at its page limit, so its Strings loop halved (16384 → 8192 B).

**Allocation** (`convert_song`, "Track allocation" below)
- **Quietest-release steals** (always on). *Gains:* MI160 audible steals 612 → 244 (shared), cut volume median 9 → 0, p90 25 → 3. *Costs:* none; same tracks, same size.
- **`steal_protect = 2`.** Steals spare parts with ≤ 2 active notes (bass lines, solos, duets): such cuts in MI160 89 → 0, Acou Bass 47 → 11. *Cost:* chord voices take the cuts, slightly louder (p90 11 → 17, max 23 → 27). Size unchanged. Listening: "a great improvement".
- **`track_plan`** (default on). Per song, the 2-/1-oscillator split and the borrowing order with the least loss, then the fewest tracks. *Gains:* loop-only MI160 steals 195 → 81 (split); pattern data −2.5…−3.5 % everywhere at equal loss (shared 529 → 516 KB, per-song 527 → 516, loop-only 566 → 546; borrowing order). *Cost:* build time; the cap search alone took the 4-mode build from 52 to 58 s, and the borrowing-order search repeats it (not timed separately).
- **`melodic_one_shots = "any"`** (default). One-shots may use 1-oscillator tracks. *Gains:* MI160 shared drops 9 → 0, steals 244 → 167; loop-only 81 → 39. *Costs:* MI160 +1 track (+4.5 KB patterns); its loudest cut 27 → 29. Under first come (without the plan) it was worse: drops 16 → 31–77.
- **`drum_borrow = "last"`.** Drums open a 1-oscillator track while the budget allows, then borrow a free melodic one. Measured before the plan: "first" tied up melodic tracks (MI152 drops 1 → 14). The plan now tries both and keeps "first" where it loses nothing and saves tracks (5–9 songs per variant).

**Velocity of delayed notes**
- **`delay_snap_db = 4.0`** (default since 2026-11-08; remove it to keep every delay). A note needing an `EDx` micro-delay can't carry its velocity `Cxx` in the same cell, so it starts at the sample's default volume until the next row (median 1.6 dB off; shared: 1246 notes > 3 dB, 321 > 6 dB, worst 16.7 dB). When that start would be more than `delay_snap_db` off and the preceding cell can't carry the velocity, the note moves to the nearest row (one tick at speed 3, 18–51 ms) with its velocity in-cell. **Guard:** no move that would put the note on the same tick as another onset of its part, so flams, rolls and grace notes keep their spacing (unguarded, 112 moves collapsed onto a neighbour; guarded: 0 collapsed, 0 reordered). Shared sweep:

  | snap above | notes moved | left > 3 dB | left > 6 dB | mean timing error |
  |---|---|---|---|---|
  | off | 0 | 1246 | 321 | 7.7 ms |
  | 6 dB | 262 (2.2 %) | 985 | 62 | 8.1 ms |
  | **4 dB** | 699 (5.9 %) | 591 | 52 | 8.7 ms |
  | 3 dB | 1255 (10.5 %) | 189 | 52 | 9.6 ms |

  The 52 left above 6 dB are the guarded flams and rolls. Per-song at 4 dB moves only 237 notes (its default volumes are each song's own median, closer to its notes); timing error 7.67 → 8.07 ms. *Size:* unchanged (shared `.NTP` 1480 → 1479 KB). *Polyphony:* unchanged (shared drops 2 → 1, steals 180 → 181). **Listening (user): an improvement**; made the default.

**Timing grid** (`rows_per_unit`, `speed`, `quantize`; full table in `plans/ntp-quantization.md`)
- **Current 2 rows per unit, speed 3:** 45.7 % of notes start at the default volume for 1–2 ticks (median 1.6 dB off), timing error 7.7 ms, patterns 527 KB (before the plan).
- **Finer rows** cure velocity loss (6 rows/unit: 0 %) but cost 2.8× the pattern data. **Row packing** (1 row/unit, speed 6) halves it (295 KB) at the same velocity and timing, but more than doubles steals. **More ticks per row** (2, 6) halves timing error and loses more velocity (67 %). Quantization is on hold.

**Current totals:**

| variant | `.NTP` total | patterns | samples + header | largest |
|---|---|---|---|---|
| per-song | 1142 KB | 516 KB | 626 KB | MI160 136 KB |
| shared | 1480 KB | 516 KB | 964 KB | MI160 141 KB |
| per-song-lo | 1152 KB | 546 KB | 606 KB | MI160 143 KB |
| shared-lo | 1493 KB | 545 KB | 948 KB | MI160 149 KB |

## Track allocation (`convert_song`; current rules, 2026-11-07)
NTP has no voice allocation: each MOD track owns its oscillators for the whole song (`determine_osc_per_track`). So `convert_song` assigns every note to a track itself, once, in onset order.

**Track types and budget.** A track's type is fixed when it is created:
- **melodic** (2 oscillators): plays looped samples (attack header + loop, in swap mode);
- **drum** (1 oscillator): plays one-shots only.

Tracks are created on demand while the oscillators used stay within `oscillators` (31: the DOC's 32 minus NTP's timer). Melodic one-shots (Xylophone) and loop-only sounds need 1 oscillator. With `melodic_one_shots = "any"` (default since 2026-11-08), one-shots take the drum path below: a free 1-oscillator track first, else a free melodic one. Under first come that starved the percussion (MI160: 16 → 31–77 dropped notes), because Xylophone hits fixed tracks as 1-oscillator; the track plan now bounds them (MI160 shared: drops 9 → 0, audible steals 244 → 167, 17 → 18 tracks). `"melodic"` keeps them on 2-oscillator tracks.

**Busy.** A track is *busy* through the last row it needs:
- **looped notes:** the note row, the envelope `Cxx` rows up to note-off, and the release ramp (`release_max`, 0.3 s) down to volume 0;
- **melodic one-shots:** until their sample ends, or their note-off plus release if that comes first;
- **drums:** only their note row, or the next row if it carries a delayed note's velocity fix. A new hit may land on a drum track whose last sample is still ringing: it cuts it, as on a drum machine.

A track is *free* at row r when it isn't busy and its cell there is empty.

**Choosing a track for a note, in order:**
1. **A free track of the note's type.** Prefer one whose last sample is the same, then the lowest index.
2. **A new track of that type,** if the budget allows.
3. **Drums only: a free melodic track** (`drum_borrow = "last"`). A 2-oscillator track can play a one-shot, and the track keeps its type. Before this, MI167's early melodic passages created 15 melodic tracks and left a single drum track, so it dropped 82 percussion notes at a peak demand of only 28 of 31 oscillators.
   - Borrowing *before* opening a new drum track (`"first"`) ties up melodic tracks needed later: MI152 went 1 → 14 drops.

   | | dropped notes, never → last → first | steals |
   |---|---|---|
   | per-song total | 105 → **13** → 27 | 877 → 880 → 855 |
   | shared total | 105 → **11** → 22 | 726 → 729 → 706 |

   12 of 16 songs are byte-identical to before. MI167 82 → 0, MI151 4 → 0, MI150 1 → 0, MI160 16 → 9–11.
4. **Steal:** a track of the same type that is only releasing (note-off passed). Take a silent one first, then one from a large part (below), then the **quietest** at this row (its latest `Cxx`), then the one nearest the end of its release, then the earliest note-off. Delete its remaining release rows and play the new note there. Steals are abrupt on the DOC, which has no declick (open item).
   - *Changed 2026-11-08* from "the earliest note-off". Measured, that cut audible tails even when another candidate had already ramped to 0 and held only its final silence row (true in 71–82 % of steals in MI151, MI152, MI160). Volume of the stolen release at the cut, shared MI160: median 9 → **0**, p90 25 → **3**, max 55 → **23**; 90 % of cuts are now ≤ 2 of 64. Pattern size unchanged.
   - The build log's `steal` counts only audible cuts (volume > 0); `st.vol` is the loudest cut in the song.
   - **Spare small parts** (`steal_protect = 2`, 2026-11-08): among audible releases, prefer one whose part (MIDI channel) has more than 2 notes active (held or releasing, the victim included). A chord loses one voice of many; a bass line or a solo/duet keeps its tail. Order: silent release → part > `steal_protect` → quietest → nearest its end → earliest note-off. Shared MI160, audible cuts:

     | | parts of 1–2 notes | Acou Bass | Panpipes | Fantasy | Elec Organ | Marimba | p90 / max cut volume |
     |---|---|---|---|---|---|---|---|
     | quietest only (0) | 89 | 47 | 39 | 11 | 120 | 21 | 11 / 23 |
     | **spare ≤ 2 (2)** | **0** | 11 | 22 | 0 | 138 | 46 | 17 / 27 |

     The cost moves to chord voices, cut slightly louder. The 11 bass cuts left are a fast line whose overlapping tails make 3+ active notes. Counting only *held* notes (plus the victim) was worse (Acou Bass 16, 78 cuts left on small parts): chord voices release together, so a releasing chord stops counting as large exactly when its tails are the ones to take.
5. **Drop** the note. The build log lists drops per sound (`MInnn dropped (no oscillator): ...`).

**Track plan** (`plan_tracks`, `track_plan = true`, 2026-11-08). A track's type is fixed when it is created, so first come lets a song's early notes fix the 2-/1-oscillator split for the whole song. The planner instead caps the melodic tracks at M (`melodic_tracks`; the 1-oscillator tracks get the remaining `oscillators − 2M`) and picks M by cost = 64 × dropped notes + the summed MOD volume of the audible steals. It walks from the first-come count while the cost falls (the cost has one valley per song, measured over M = 0–15). Ties go to fewer tracks (less pattern data), and first come stays if nothing beats it. Songs with no loss skip the cap search. The whole search runs for both drum-path borrowing orders (`drum_borrow` "last" and "first"), and the plan keeps the least loss, then the fewest tracks: at equal loss, reusing idle 2-oscillator tracks often needs fewer tracks. Measured over all songs, same loss everywhere: pattern data shared 529 → 516 KB, per-song 527 → 516, loop-only 566 → 546; "first" wins in 5–9 songs per variant. The `plan` column adds `f` when "first" was chosen. Under a cap, a drum that finds no free track may open one of the planned melodic tracks the song hasn't created yet (otherwise caps dropped early drums). The build log's `plan` column is the chosen M (`-`: first come).

| | first come → planned |
|---|---|
| shared / per-song | first come was already optimal: 14 melodic + 3 drum tracks in MI160, MI152 and MI151 (every other split costs more). MI168 3 → 2 steals; MI166 the same result on one track fewer. The other 14 songs are byte-identical. |
| shared-lo, MI160 | loop-only organ notes had opened 11 1-oscillator tracks and left 10 melodic: audible steals 195 → **81**, loudest cut 30 → 27 (M = 12) |
| per-song-lo | MI160 196 → **105**; MI152 2 → 0 |
| + one-shots on either track type | MI160: shared 244 → **167** steals, 9 → **0** drops (M = 13); per-song 344 → 260, 11 → 1; shared-lo 81 → **39**; per-song-lo 105 → 59 |

**What a note writes on its track:**
- **Note cell:** note, instrument and `Cxx` velocity. If the note needs a micro-delay, `EDx` takes the effect column instead; velocity handling for those is described in the pipeline section.
- **Looped notes:** envelope `Cxx` rows, then a release ramp. Where it reaches 0, `C00` plus the silent `rest` one-shot, which stops the loop's oscillators. Otherwise NTP would bring the loop back on the next delayed note (see "Ghosts").

**After allocation:**
- **Track order:** all melodic tracks are emitted before the drum tracks. Ninjaforce's converter assigns oscillators first-fit, pairs on even oscillators, and interleaving strands odd oscillators (see "Ninjaforce converter: oscillator assignment bug"). `ntp_oscillators` replays its assignment, and the build fails if it wouldn't fit.
- **Minimum:** at least 2 tracks.
- **Control commands:** speed and tempo go in row 0, and `B00` in the last row.

**What the numbers mean:** `trk` and `osc` in the build log are the tracks and oscillators used. Overload remains where demand really exceeds 31: MI160 peaks at 44 oscillators, over budget for 9.6 % of the song, measured with notes ringing to note-off plus release. That's where its steals and 9–11 dropped Claves come from. Loop-only (1-oscillator) organ samples would cut that to 2.3 %; that's a proposal, not built.

### Loop-only sounds (trial `per-song-lo` / `shared-lo`, 2026-11-08)
`loop_only` (sound names, per mode) drops a sound's attack header, so it needs **1 oscillator** instead of 2. Ninjaforce's converter gives a looped MOD sample 1 oscillator when its loop starts at 0 and its length is a power of two (`modinstrument::get_instrument_type`, `ntpinstrument::get_osc_count`, `ntpconverter_lib.php:49`, `:337`). Such notes go on the 1-oscillator (drum) tracks; the envelope rows follow the contour from the onset. Loops are planned with the attack in place and the attack is removed afterwards, so the loop is byte-identical to the base mode and only the attack differs. The freed attack pages stay free (shared 237 → 233 pages).

**What the attack contributes,** measured against Munt over the first 300 ms (10 ms frames; tone distance = log-spectral distance after level-normalising each frame, 0–8 kHz; the 0–100 ms figure for attack + loop is the baseline):

| sound | rise to −3 dB | tone 0–100 ms, attack + loop → loop-only | 100–300 ms |
|---|---|---|---|
| Elec Organ | 10 ms | 6.6 → 8.8 dB | 8.1 → 8.1 |
| Trombone | 20 ms | 6.3 → 8.7 | 6.3 → 6.3 |
| Sax | 30 ms | 7.5 → 9.3 | 7.1 → 7.1 |
| Pipe Organ | 40 ms | 7.0 → 9.7 | 6.8 → 7.0 |
| Chorale | 80 ms | 2.1 → 13.6 | 11.0 → 10.9 |
| Panpipes | 30 ms | 0.9 → 26.6 | 15.2 → 16.9 |

Panpipes, Chorale and Strings (190 ms swell) are out. Elec Organ also starts 5.4 dB above its sustain for about 50 ms, which a loop-only note loses.

**Elec Organ loop-only, all songs:** drops 11 → 1 (shared) and 13 → 1 (per-song); audible steals 262 → 195 and 368 → 198; MI160 drops 9–11 → 0; loudest cut 27 → 30.

**Listening (user, 2026-11-08):** the change is clearly audible, much more so in the shared version (its 512 B averaged loop has none of the per-song raw loop's movement, so the missing attack stands out). It also sounds **significantly quieter**. Kept as a trial; undecided.

**Why quieter (measured, shared Elec Organ):** the loop-only envelope is right (the contour is a steady −4 dB re the loop from the onset; the 0.8 s smoothing isn't pulled down by pre-onset silence). What's lost is the normal note's louder onset:

| | 0–60 ms | 60–300 ms | after 0.3 s |
|---|---|---|---|
| attack + loop | raw attack, with its +5.4 dB onset peak | loop at gain 1, easing to the contour (−4 dB) over `handover_s` (150 ms) | −4…−5 dB |
| loop-only | first row (85 ms) at 0 dB, then −4 dB | −4 dB | same |

Onsets dominate perceived loudness. Possible fix if the trial is kept: copy the normal onset shape, i.e. hold gain 1 through the equivalent handover and lift the first row toward the attack's peak (MOD volume has headroom below 64).
