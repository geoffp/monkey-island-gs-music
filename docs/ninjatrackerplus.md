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
Versions 0, 1 and 2. The converter writes version 2 (`save_ntp`, `ntpconverter_lib.php`: `chr(2)`, then the MOD's title as the Pascal song name; checked in `MI160.ntp`, 2026-11-12); song and instrument names are stored "since 1.4". Our MOD titles are the song names (`song_names.toml`), else `MI1 150`. SenseiPlay shows the name, in quotes, when you press `i` while a song plays (GSSquared, `MI160.ntp` "Opening Theme" and the full 20 characters of `MI164.ntp` "Following Shopkeeper", 2026-11-12).
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

## Our MOD-first pipeline (`src/migs/ntpmod.py` with `ntpsamples.py`, `ntpsong.py`, `modfile.py`; `just ntpmod` → `just ntp`)
The MOD is the primary artefact: it renders on the desktop (ffmpeg's libopenmpt → `build/monkey/ntp/wav/`), and Ninjaforce's converter (`php-cli`, `STREAM_FORBIDDEN`) turns it into `.NTP`. Settings live in `config/games/monkey/ntp.toml`; sounds come from `config/games/monkey/mt32sounds.toml` (the same 14 melodic and 14 drum sounds as MI1.BNK).

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

    **Length (2026-11-08, `drum_floor_lsb = 1.5`):** a drum ends at the 8-bit floor (`drum_end`): where its decay first falls below 1.5 LSB RMS (−38.5 dB re peak, the `one_shot_floor_lsb` melodic one-shots use), after its last point above twice that. Before, it ran to its −46 dB point, and most of that tail was below what 8 bits carry (seen by the user in a tracker; measured on the stored MI160 samples): bongo 80 %, mute conga 65 %, conga 43 %, snare 39 %, claves 34 %, rim 29 %. The kick and snare stop abruptly in the MT-32 render (kick 7 → 1 LSB, snare 11 → 1.5 LSB within 128 bytes), then carry ~1 KB of junk around 1–2 LSB with bursts above the floor. The user heard it on the snare. A plain last-crossing rule (`floor_time`) keeps that junk (snare 2477 B), hence the 2× rule. Stored lengths (B): kick 2038 → 788, snare 4086 → 1506, bongo 1014 → 218, mute conga 1014 → 358, conga 2038 → 1150, rim 1014 → 766, claves 502 → 330. The hats, cabasa, toms, timbale, agogo and quijada are unchanged: they still have energy at their byte limit.
  - **Memory:** sized and packed by NTP's rules (stopper bytes, loop split, power-of-two blocks, largest at page 0), giving **242 of 256 DOC pages** for 28 samples.
- **Timing:** rows sit on each song's detected grid unit / `rows_per_unit` (default 32nds), speed 3, MOD tempo per song (48–136). `EDx` keeps micro-timing (`quantize = 0`). The measured mean error is 4.6–12 ms.
  - **Tempo dithering** (`tempo_commands`): MOD tempos are integers, so a single tempo misses the ideal row length by up to 0.65%. Notes are placed on the ideal grid, and `Fxx` commands switch between the two neighbouring integer tempos whenever the playback clock drifts more than `tempo_tolerance_ms` (default 2 ms) from it. They go in any free effect cell. This takes 1–106 commands per song.
- **Levels (measured, `just ntpmod` prints the CC7 curve):** a note's loudness = sample peak (Munt, velocity 100, CC7 100) × its **velocity response** × **key response** (melodic) or per-key rhythm level (drum covers) × the MT-32 **CC7 curve**. All three are measured in Munt (`velocity_response`, `key_response`, `cc7_curve`). Each song is normalised so its loudest moment (99.5th percentile, including sounds that swell after the attack) is volume 64 (`level_ref` in `config/games/monkey/ntp.toml` pins it).
- **Velocity placement:**
  - in the note's own cell when it needs no delay;
  - else, on a drum track that has gone silent, a `Cxx` in the preceding cell plus an instrument-less note (NTP keeps the track volume; `setup_effect` in `ninjatrackerplus.s`);
  - else the instrument's default volume (the sample's median volume in that song), corrected by the next row's envelope `Cxx`.
- **Envelope:** per-row `Cxx` follows the MT-32 loudness contour after the attack, including swells above the attack level (strings and organ reach about 2×).
  - The contour is smoothed in the **log domain**: over 250 ms for decaying sounds (decays stay unbiased), and over 0.8 s (at least the raw loop's length) for steady sounds, so the rows carry only the slow contour (see "Smoother sustains" below).
  - Each row's `Cxx` is the contour's **mean over that row** (`env_at`), not a point sample. Release is a ramp of up to 0.3 s, then `C00`.
  - A sustain row **rises** at most `env_max_rise_db` (1.5 dB, at least one volume step) above the previous one; falls follow the contour, and the release starts from the level that plays (see "Smoother sustains", step 5).
- **NTP-only sounds:** `[[melodic]]` entries in `config/games/monkey/ntp.toml` split programs out of the shared bank's merges (`ntp_spec`), using DOC RAM the MIDI Synth bank doesn't have.
  - `keys = [lo, hi]` makes a **key split** (`sample_for`, `melodic_entry`).
  - `ntp_attack_bytes` overrides the loop-header length.
  - Currently Pipe Org 2 (MI153): "Pipe Organ" for keys 52+, sampled at key 60, and "Pipe Organ Lo" for keys ≤ 51, sampled at key 41. Both use 1016 B attacks (organs speak in about 50 ms). Total: 254/256 pages, 30 samples.
- **Tracks:** created on demand within NTP's **31-oscillator** budget; see "Track allocation" below for the full rules. There's no separate control track: speed and tempo sit in row 0 (the lead-in). How a song ends follows the game: see "Song ends and loops" below.
- **Channel tag:** `nCHN` below 10 channels, `nnCH` from 10. libopenmpt didn't recognise `09CH` (it rendered silence), so the format matters.

### First build (2026-11-06)
- 9–19 tracks per song, using up to 31 oscillators. Dropped notes are 0–16 per song, except MI167 (89).
- **MI160:** 797 releases stolen, 16 notes dropped.
- About 60% of notes need an `EDx` delay. Most of those start at the default volume for one row (70–150 ms) before the envelope corrects them.

### First listening (2026-11-06)
The user listened to the desktop previews (`build/monkey/ntp/wav/`): **the pipeline works**, but there are problems still to fix. The `.NTP` conversion and SenseiPlay on the IIGS are wired up (`just ntp`, `just emu-synthlab`) but haven't been run yet; they need `php-cli`.

Known problems and open work, from the build log and analysis:
- **MI167 drops 89 notes** (MI152 and MI160 drop 16 each). *Resolved 2026-11-07:* see "Track allocation" (drum tracks borrowing idle melodic tracks).
- **Velocity on delayed notes:** about 60% of notes need `EDx`. Most of those start at the instrument's default volume for one row (70–150 ms) until the next row's `Cxx` corrects it. This may sound like uneven attacks. Fix options: put velocity in the preceding cell more often (needs an idle track), or pick the default volume per song from the median velocity.
- **Drums are low-fi**: one-shots at about 5.9–8.3 kHz, to fit 64 KB.
- **Level:** previews peak around −10 dB.
- **Pattern size and player CPU** haven't been measured on the IIGS. Rows are 32nds of the detected grid unit (`rows_per_unit = 2`). `config/games/monkey/ntp.toml` per-song overrides can coarsen this.

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
- **`just emu`** boots `build/monkey/disks/MI.NTP.PO` (`just disk`). This is a ProDOS 8 BazFast disk made from SenseiPlay's own `PRODOS` + `sensei.system` + `ntpplayer` + `dgr.radio.dgr`, with our songs in `/songs/<VARIANT>` (`INST.PER.SONG`, `INST.SHARED`, plus any trial variants; `PER.SONG` / `SHARED` before 2026-11-09), all 16 in one folder. It boots straight into SenseiPlay. A `README.TXT` at the root says what the two variant folders are: the template `config/games/monkey/disk/README.TXT` filled in from the staged songs (count, file names, placeholders: `src/migs/diskreadme.py`), staged as ProDOS TXT with CR line ends. `just emu-synthlab` also has them, under `/SenseiPlay/MI/<MODE>`.
  - **Size (2026-11-09):** the smallest power of two that holds it: `just disk` builds it at 1, 2, 4, ... 32 MB until every entry fits (4 MB now: about 2.9 MB used, 2363 blocks free). Verified in GSSquared: boots into SenseiPlay and plays MI160. A ProDOS volume can have any number of 512-byte blocks up to 65,535 (32 MB), recorded in its volume directory.
    - **CFFA3000:** mounts `.PO` / `.HDV` / `.2MG` of at least 256 KB and a multiple of 512 bytes as block devices; 140 KB images may be taken as Disk II floppies ([applefritter](https://www.applefritter.com/node/32022); [CFFA3000 Reference Manual](https://manualmachine.com/rdautomation/cffa3000/20407985-reference-manual) for the 32 MB ProDOS limit).
    - **Floppy Emu:** SmartPort hard-disk mode takes `.PO` / `.HDV` / `.2MG`, 32 MB per ProDOS image (more on recent firmware, HFS only). The image must be contiguous on the SD card ("image not contiguous" otherwise), and the 3.5″ modes need real 400K/800K images ([BMOW](https://www.bigmessowires.com/?p=2938), [BMOW](https://www.bigmessowires.com/2015/07/02/2-gb-smartport-hard-drive-for-apple-iigs/)).
    - **Emulators:** GSSquared, KEGS and AppleWin size hard-disk images from the file ([AppleWin help](https://git.applefritter.com/Apple-2-Tools/AppleWin/raw/commit/7f9563b8ea24ce51ecbb5fb18a8ff9fbbee3aa12/help/ddi-harddisk.html)).
  - **Distribution archives:** `just disk` also writes `build/monkey/dist/MI.NTP.zip` (the image, 1.5 MB: for emulators, CFFA3000, Floppy Emu) and `build/monkey/dist/MI.NTP.SHK` (ShrinkIt LZW/2 archive of the volume's files with their ProDOS types, 1.8 MB: unpack with ShrinkIt on a IIgs). cp2 writes `.SDK` ShrinkIt *disk* archives only at floppy sizes. Both are copyrighted derivatives of the game and ROMs, like the rest of `build/`.
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
- **GSSquared stereo is a heuristic (checked 2026-11-09, source `src/devices/es5503/soundglu.cpp` `ensoniq_mirror_mono_to_stereo`, since commit `160721cc`, in our pinned v1.0.0).** DOC channels map odd → left, even → right (TN #19 stereo card). But when every running (not halted) oscillator is on the same side, that side is copied to both speakers, so mono programs aren't heard on one side only. NTP gives each track a fixed side (`$doc_stereo_defaults`: 1 0 0 1 …, per track), so a song flips between true stereo and centred mono whenever the last oscillator of one side halts or one starts (a one-shot ending, a released loop stopped by the rest sample). The centred side is also louder. Our songs flip 0–110 times a minute (MI168: 52; simulated from the MODs). User report on MI168: channels "jumping from left to right or to center" well after note-ons. This is an emulator artefact: a real IIGS sums the channels to mono unless a stereo card is fitted, which then doesn't mirror. Not worked around.
- **GSSquared starts swap-mode loops early (checked 2026-11-09; source `src/devices/es5503/ensoniq.cpp` `generate_samples` / `halt_osc` and `soundglu.cpp` `ensoniq_catch_up`, v1.0.0).** The emulator renders the DOC in batches of up to 256 output samples (≈ 9.7 ms at 26 320 Hz), one oscillator at a time over the whole batch, in oscillator order. When a swap-mode attack oscillator reads its stopper byte, `halt_osc` starts the partner with accumulator 0, and the partner then renders from the *start* of that batch if its number is higher. The converter always gives a 2-oscillator track an even first oscillator (`ntpconverter_lib.php`, track assignment steps by 2), and NTP plays the attack on it and the loop on the next one (`generate_osc_2`). So in GSSquared every loop starts 0–255 samples early and sounds on top of the attack's last milliseconds, at an unrelated phase. A real DOC steps all oscillators every sample, so the loop starts on the sample after the stopper. Simulated on MI153's Pipe Organ (`gs2swap.py`: attack + loop summed with the loop δ = 0..255 samples early): level bumps up to +6 to +9 dB and dips to −5 to −7 dB over a few ms, beyond +3/−6 dB on 38–88 % of swaps, about equally in `shared` (key 60) and `shared-pitch` (key 74). User report (2026-11-09): the shared Pipe Organ loop has "a nasty misalignment on the GS that isn't super evident on Linux MOD players", in both versions, more so with the pitch plan. Every looped sound is affected; sustained organ chords in a slow song expose it most. Our sample data can't avoid it (the overlap length is set by the emulator's batch timing). Not worked around; a fix belongs in the emulator (start the partner at the halting sample's index within the batch).
- **Loops start at their gentlest rising crossing (2026-11-09).** User report, in KEGS (whose DOC starts the partner at the exact sample, `src/doc.c` `doc_sound_end`): shared-pitch Pipe Organ "does still have a noticeable click on each loop". The handover still slips by a fraction of a sample: the attack oscillator spends an output sample reading its 0 stopper byte, and the loop oscillator starts at phase 0. The error that costs is the waveform's slope at the loop's start. `join_aligned` took the first rising crossing of the cycle, which in an organ cycle (several crossings) was often the steepest: shared-pitch Pipe Organ started at band-limited slope 85 (8-bit steps −74 → 0 → +70, its largest anywhere) where 11 was on offer; Pipe Organ Lo 23 for 1; shared Pipe Organ 21 for 13. Now it rotates to the rising crossing of least slope in the first cycle (the attack then moves into phase with it, as before). Crossings are found on the 8× oversampled waveform (`rising_crossings`): the gentlest is often a shallow dip below zero between two samples of the same sign, which a sample sign test misses. Only the first cycle, because a raw loop's level moves: a crossing deep in a 16 KB loop can sit in a wobble trough, and the loop is level-matched to the attack there (searching the whole loop moved per-song-pitch steals 433 → 278 through those level changes). Test: `test_aligned_join_starts_the_loop_at_its_gentlest_rising_crossing`.

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
NTP files carry their own samples, and `NTPprepare` copies them into DOC RAM for each song. So the MIDI Synth "one shared bank" constraint doesn't apply. Both modes are built side by side (`ntp_modes` in the justfile). Since 2026-11-09 the builds are named `inst-per-song` and `inst-shared`: `[modes.*]` variants with these sample-set modes as `base` and nothing overridden (`build/monkey/ntp/inst-*/`, disk folders `INST.PER.SONG`, `INST.SHARED`). Measurements below use the mode names.
- **`per-song`** (default `samples` in `config/games/monkey/ntp.toml`): each song carries only the sounds it plays (`sample_set`). Its free DOC RAM, after `reserve_pages` (16 pages = 4 KB for game sound effects), goes to longer loops (`plan_loops`). The planner repeatedly doubles the loop with the most sustained playing time per byte, while everything still packs the way NTP packs it, up to `max_loop_bytes` (16 KB) and the recording's length. Example: MI153 carries 3 organ samples with 16 KB loops (204 pages).
- **`shared`**: one set for every song (union of sounds and key ranges; loops planned over all songs' usage). `[modes.shared]` sets `reserve_pages = 0`, because the full set needs 256/256 pages with 512 B loops. NTP-only sounds can be limited with `modes = [...]` (Pipe Organ Lo2 is per-song only; in shared mode keys 36–39 fall back to the nearest split).
- **Long loops** (`raw_loop`) are for **steady** sounds only: the level's trend must drop ≤ 6 dB from the sustain start to note-off (`is_steady`). They hold k recorded periods (chorus, beating and vibrato kept), with a crossfade seam: the last 25% fades into the audio just before the loop start. Decaying sounds (bass, marimba, vibes, xylophone) keep the 512 B averaged-cycle loop, because a raw loop would bake the decay in and pump.
  - **Trend, not range (2026-11-09).** `is_steady` used to require the 250 ms-smoothed level's *range* to stay within 6 dB. The MT-32 Elec Organ's rotary wobble swings ±10 dB at every key, so the range test passed or failed by where the wobble fell: steady at keys 60–64, not at 40–58. French Horn (beating) and Strings flipped the same way. It now fits a least-squares line to the dB envelope. Measured trend drops at the sampled key ±12: decaying sounds 10–50 dB, steady ones within ±5 dB, with no sound changing class across keys. French Horn is now steady: in MI167/MI168 it gets an 8192–16384 B raw loop that carries its beating (MI167's Strings loop shrank 16384 → 8192 B to make room). Not yet listened to.
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
- The full shared set needed 254 pages with 512 B loops. `[modes.shared] ntp_attack_bytes` (a decision the build checks since 2026-11-11: `check_fit` fails when the set doesn't fit at its smallest and logs a cut no longer needed) gives 1016 B attacks to the four sounds where that costs least, by mean log-spectral distance vs Munt over the first 0.6 s at the sampled key:

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
`just disk` (both modes, previews, `.NTP`, bank check, boot disk) went from about 4 min to **22 s**. All 64 MOD/NTP outputs are byte-identical to the build before the changes (sha256).
| cause (cProfile) | fix |
|---|---|
| `smooth()`: `np.convolve` with 250 ms (8000-tap) windows, O(n·w); 36 of 56 s in shared mode | cumulative-sum moving average, O(n), the same window as `np.convolve(..., "same")` (max deviation ~1e-12) |
| velocity/key responses re-measured from the same WAVs for every song (3177 `rms_envelope` calls, 3000 WAV reads) | in-process `_render` cache (read-only arrays) plus `_peak` memo. `mt32bank` is untouched, so the MIDI Synth bank can't change |
| `best_tuning` (then `_best_tuning`): Python triple loop (k × finetune × note) | NumPy over (k, finetune, note). `argmin` picks the first minimum in the same scan order, so the same tuning is chosen (tested against the loop) |
| MIDI parsing and grid detection repeated per pass | `read_notes` cached by path + mtime, `detect_grid` by onsets |
| recipes serial | modes build concurrently; ffmpeg previews and PHP conversions run on all cores (`xargs -P $(nproc)`) |

`ntpmod` itself: per-song mode 169 s → 10 s, shared mode 49 s → 10 s.

## Smoother sustains (2026-11-07)
User report: shared-version sustains "still a little choppy". Measured from the MOD patterns, on held notes: note-volume level, ±1 flicker reversals, and step sizes. Then fixed in four steps:
1. **Volume resolution (shared only).** The common loudness reference from the resident-bank work left quiet songs at MOD volume ~4 (MI153: median 4, 29.5% flicker, 2.5 dB per step; per-song: 32, 0.1%). **Fix:** shared mode normalises each song to its own loudest moment again, like per-song. Bank-compatible default volumes are each sample's median over the per-song-normalised volumes. The MT-32's song-to-song balance goes to `build/monkey/ntp/shared/levels.toml` (`gain_db`, `play_volume_scale`) for a game's `NTPsetplayvolume`.
2. **Rounding flicker.** A slowly moving target near x.5 flipped the volume 4→5→4. **Fix:** `volume_hysteresis` (0.75): a row changes volume only when the target has moved ≥ 0.75 of a step from the current value.
3. **Handover step.** The loop was level-matched to the source at the sustain point (0.4 s), not where the attack ends. Every note stepped at the attack→loop handover, against an MT-32 that doesn't: Strings +3.1 dB vs +1.2, French Horn −3.1 vs −0.4, Fantasy +1.7 vs −1.4. **Fix:** the loop is level-matched to the attack's tail, and the envelope eases from gain 1 to the contour over `handover_s` (150 ms, linear in dB). Handover changes are now within ±0.7 dB, following the MT-32's direction (Shakuhachi's real −4.4 dB fade is eased to −2.2).
4. **Stepped tremolo.** Steady sounds' chorus/beating (Chorale: −1…−8 dB at 2.5 Hz) passed the 250 ms smoothing as ±1-step row flips: a stepped tremolo on a static loop. A raw loop already contains that modulation, so the rows doubled it. **Fix:** steady sounds smooth their contour over `env_smooth_steady_s` (0.8 s), and at least the raw loop's length.
5. **Pumping beats (2026-11-09).** User report: MI168 has "unpleasant jumps in volume" well after some note-ons, subtle in a desktop MOD player, pronounced on the IIGS. **Cause, measured from the MOD:** French Horn is a decaying sound (512 B averaged loop, 250 ms smoothing), and its MT-32 beating (0.57 s, 13 dB; "Loop lab" below) survives that smoothing. The rows pump it in 111 ms steps: `29 26 20 23 33 34 25 19 22`. MI168 had 93 mid-note horn steps of 2.5–3.6 dB, 36 of them upwards; every other melodic part stayed under 2 dB. The DOC applies a new volume at once (GSSquared `ensoniq.cpp` uses the register value directly; there is no volume ramp), while libopenmpt ramps volume changes by default, hence "subtle on the desktop". **Fix:** `env_max_rise_db` (1.5): a sustain row rises at most 1.5 dB (at least one volume step) above the previous row, and the release starts from the level that plays, not the unlimited target. **Falls are not limited:** a symmetric limit slowed real decays (Marimba, Vibes fall 3–5 dB a row), which held tracks longer: steals per-song MI160 261 → 278, shared 167 → 223, plus one dropped note. With rises only, notes, drops, steals and delays are identical in every song, in both modes.

   Rises ≥ 2.5 dB on held notes (all parts, drums included): MI168 41 → 5, MI167 50 → 4 (per-song) and 27 → 5 (shared); no other song changes on this measure, and the 5 left are drums. Cost: the limited rows lag the contour where it rises fast: French Horn mean 0.2–0.4 dB per row (p95 1.7–2.1 dB), Strings in per-song MI161/MI167 0.4–0.65 dB (p95 1.9–2.8 dB): their beat and swell are softened. Everything else changes ≤ 0.1 dB on average. Not yet listened to.

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
Measured with `just looplab`; details, sources and the full method comparison are in `docs/sample-construction.md`.
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
Decaying melodic sounds that reach the 8-bit floor within the DOC RAM their attack + loop would take are stored whole, with exact tuning. Today that is only Xylophone. Rule and measurements: `docs/sample-construction.md`, "One-shots". The freed pages stay free (the shared bank is now 238/256). The build log now lists dropped notes per sound (`MInnn dropped (no oscillator): ...`).

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
E′+ was first built as trial variants, `per-song-e` and `shared-e` (`[modes.<name>]` in `config/games/monkey/ntp.toml`: `base` + `loop_construction = "eprime"`), next to `per-song` and `shared`. After listening (user, 2026-11-07), it became the default:
- `loop_construction = "eprime"` is set in `[defaults]`.
- The variants were dropped, and the new `per-song` / `shared` are byte-identical to the old `-e` builds (64 MOD/NTP checksums).
- The original averaged-cycle loops stay selectable with `loop_construction = "averaged"`. A variant using them, `base = "shared"` + `"averaged"`, reproduces the old `shared` byte for byte.
- The code is in `src/migs/loopbuild.py`, moved from the research harness `looplab`.
- **What changes:** only the short (512 B) loops, which are built as E′+ (`ntpsamples.eprime_attack_and_loop`, using `loopbuild`):
  - whole samples per cycle (`ntpsamples.best_tuning(whole_cycles=True)`);
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
- `build_samples` adds a silent 248 B one-shot, `rest`: 1 page and 1 sample slot. Shared now has 31 samples, the MOD maximum, and 237/256 pages. (Since 2026-11-09 the rest is looped, 2 pages, and only 2-oscillator tracks get it: see "Flicker" below.)
- Where a release reaches volume 0, `convert_song` writes `C00` *plus* a rest note into that cell. Triggering the rest stops the loop's oscillators, and the rest halts itself on its stopper bytes. MOD 0 = DOC 128 = silence; DOC 0 would stop it at once.
- In GSSquared, at the same delayed rows the volume still rises at tick 0, but every oscillator is halted, so nothing sounds.
- The built MODs now have 0 ghosts in all four variants built then (per-song, shared and their E′+ trials), out of about 5,400 delayed notes each. The build log's `halt` column counts the halted releases.

**Desktop output:** unchanged apart from inaudible mixer residue (−68 to −71 dB, at most about 20 LSB), as long as the rest doesn't alter track choice: `trk_inst` keeps the note's sample. Marking released tracks as "rest" had moved notes between the panned channels.

**One cost:** per-song MI167 was exactly at its page limit, so the rest's page halved its Strings loop (16384 → 8192 B, about one beat long; it passes the tremolo gate).

**Flicker: delayed notes started their loop at a stale volume (fixed 2026-11-09).**
- **User report:** "some flicker on the notes" early in MI153's pattern 3.
- **Measured** with a GSSquared DOC register log (all 32 oscillators about every 17 ms):
  - a delayed note's attack oscillator started at the new default volume, but its loop oscillator still held the last release level of the track's previous note;
  - track 11 (key 82): attack 120, loop 28. The attack lasts about 35 ms, then the loop played 12.6 dB down for about 65 ms until the next row's `Cxx` (92);
  - track 7 (key 48): 132, then 24, then 104.
- **Cause** (`ninjatrackerplus.s`): at tick 0 the player writes the cell's default volume through the routine for the *current* instrument's type (`talk_to_doc_play`, indexed by `osc_instrument_type`). After a halt that instrument is the rest. As a one-shot, the rest used `talk_to_doc_1osc`, which writes oscillator A only. At tick `x` the note starts with control, pointer and size writes but no volume write (`talk_to_doc_2osc`, `what_to_do` bit 7), so B kept its old value. On a track's very first note there is no instrument yet, with the same result (MI153 track 12: loop at 0).
- **Fix:**
  - **The rest is looped:** a silent 248 B header plus a silent 256 B loop. NTP treats it as a 2-oscillator instrument (converter types `%0010` and `%0111`), so tick 0 writes both volumes. Cost: 2 DOC pages instead of 1 (+1 page in every song; no loop changed), still 1 sample slot.
  - **Only 2-oscillator tracks get it:** on a 1-oscillator track it would make the converter give the track a pair. Cut one-shots there (MI160 and MI162 Xylophone: 179 and 130 halts) now end with `C00` alone. Ghost check on the built MODs: of 345–363 delayed notes on MI160's 1-oscillator tracks and 200–208 on MI162's, none follows a cut one-shot that is still sounding. This also removes a latent bug: `preceding_silent` could put a delayed drum note with no instrument right after a rest, which NTP would have played with the rest.
  - **A melodic track whose first note is delayed** gets the rest on the row before it (a looping song's later passes find the track already 2-oscillator): +1 to +7 halts per song.
- **After** (GSSquared, whole of per-song MI153): 199 attack-to-loop handovers. The only one with the loop more than 3 dB below the attack is a row's `Cxx` landing at the same moment (both oscillators 144 → 96). Every other build number is unchanged in both modes, apart from the halt counts.

**Ghosts on still-sounding tracks (fixed 2026-11-11: `halt_before_delayed`, on by default).**
- **User report:** in MI160, "a pretty audible wacko out-of-place tone in pattern 4 and several in pattern 6. Very brief, high volume." Heard in SenseiPlay on GSSquared and on KEGS, not in VLC.
- **Measured** with a GSSquared DOC register log (all 32 oscillators every ~17 ms, SenseiPlay driven through gs2-mcp; DOC pointers matched to the samples through a dump of DOC RAM):
  - the same tick-0 ghost as above, on tracks `halt_released_loops` doesn't reach: a release still above 0 (DOC volume 4–8, about −30 to −36 dB) or a note cut for this one (a Marimba at 72);
  - the previous note jumped to the delayed note's default volume (228: Acou Bass 57 × 4) for 50–67 ms, +24 to +35 dB (+10 dB for the cut Marimba);
  - patterns 4 and 6 (counted from 0, as SenseiPlay shows them) held 1 and 6 of the loud ones; whole song, per-song: 105 jumps to DOC volume ≥ 100, 336 to ≥ 20.
- **Where:** a scan of the built MODs (a delayed note with an instrument, on a track whose last volume is above 0 and isn't the rest) agreed with the log (14 loud ones in patterns 0–7 in both). Per-song MI160 had 463, MI152 57, other songs 0–3; shared 195 and 41. MI160 reuses its 19 tracks for 2,726 notes (378 steals), and 1,480 of its notes are delayed.
- **Option `halt_before_delayed`** (default on; trialled as modes `per-song-halt`, `shared-halt`): when a delayed note lands on a 2-oscillator track still sounding on the row before (a looped note above volume 0, or a one-shot whose sample hasn't ended), that row gets the rest + `C00`. When that row starts a note there is nothing to halt it with, so the note moves to the nearest row instead (no `EDx`).
- **Result:**
  - the MOD scan finds 0 left in every song, in both sample-set modes;
  - the DOC log of the whole of per-song MI160 has 2 jumps, both on 1-oscillator tracks, which the option doesn't touch (Xylophone, Conga; +6 to +7 dB). The rest would make those tracks 2-oscillator ones;
  - halts and moves per song (per-song): MI160 459 + 5, MI152 57 + 0, MI167 18 + 19, others 0–3;
  - cost: the old note ends up to a row (85 ms) early, at −30 dB or below for the release tails; moved notes shift by 1–2 ticks (MI167's mean timing error 10.3 → 10.9 ms). Notes, drops and steals are unchanged; with the option off the MODs are byte-identical to before it.
- **Listened** (user, 2026-11-11, GSSquared): "sounds really good"; made the default.

**Still open (theory 2):** steals, i.e. a releasing note cut for a new one, are abrupt on the DOC. NTP halts oscillators outright, with no declick ramp, where desktop players ramp over a few milliseconds. MI160 had about 620–780 of them; since 2026-11-08 most fall on a release that has already reached 0 (see "Track allocation", step 4).

## Song ends and loops (2026-11-11)
**In the game.** An `RO` track ends either in `FF` or `00` (`docs/ro-format.md`). ScummVM's `MidiParser_RO` sets `_autoLoop` on `FF`, and `MidiParser` then answers the end of track with `jumpToTick(0)` (`ref/scummvm/audio/midiparser.cpp`, `parseNextEvent`/`onTimer`): the track plays again from its start, at once, with the notes still sounding stopped (`jumpToTick`'s `stopNotes` default), so their releases sound into the next pass. On `00` it stops. The extractor writes `loopStart`/`loopEnd` markers for `FF` tracks (`extract_music._conductor`), and `ntpsong.read_loop` reads them. In MI1, 12 of the 16 songs loop and 4 play once (`docs/games/monkey.md`). Scripts can also restart a sound; that isn't followed here (a byte search of the room scripts for the start-sound opcodes naming 150–168 finds mostly false hits, and decoding v4 scripts is a project of its own).

**Before** every MOD ended with `B00` on the last row of its last *pattern*: every song looped, the play-once ones too, and each pass was padded to a 64-row boundary plus the lead-in row (MI154: 9.1 s of music, 13.7 s a pass).

**Now** (`_Arranger.finish`, `seam`, `fit_pass`):
- **Play-once songs** end on their last row, once the releases and one-shots are done, with `F00` (stop; since 2026-11-12). That row is never a pattern's last: a song that would end there gets one more pattern. MI154 lasts 9.6 s, MI160 94.1 s (was 97.4 s).
  - **The stray note (bug, 2026-11-12).** Until then a play-once song ended with `D00` on its last row, moving past the last order position (or simply ran off its last pattern). NTP then stops in `check_pattern_end_2` (`play_song_once`), but `check_pattern_end` and `handle_pattern_break` reset the row pointer first, and the same tick (`parse_tracks`, after `jsr check_pattern_jump`) goes on to parse row 0 of the last pattern and start its notes, after `stop_playing` has stopped every oscillator and the sound interrupt. Nothing stops them: MI154's last pattern starts with an Elec Organ loop, which droned on after the song (heard by the user); MI152 started a bass note and a conga. `F00` (`setup_setspeed`: `pattern_jump = 6`, `stop_playing`) leaves the row pointer alone, so the row parsed after it is the next row of the same pattern, which is empty.
  - **Desktop players** (libopenmpt) ignore `F00` in a MOD, so a `D00` on that next row ends the song for them one row later (MI154's preview: 9.9 s; without it 13.9 s, to the end of the pattern). NTP has stopped by then; `play` zeroes `pattern_jump` for the next song.
  - **SenseiPlay's "Loop NTP" toggle** no longer repeats a play-once song: `F00` stops whatever the player asked. The game plays these songs once. Test: `test_a_song_that_plays_once_stops_on_its_last_row_with_F00`.
- **Looping songs** repeat by themselves, whatever the player's setting:
  - **Lead-in once.** Row 0 (speed, tempo) is alone in pattern 0, ended at once by `D00`; the song proper starts at pattern 1, and `B01` on the pass's last row jumps back there. NTP's `B` and `D` share one variable (`pattern_jump`), so a single row can't hold both; this layout needs neither in the same row. Cost: one extra pattern.
  - **The pass lasts the game's loop.** The rows follow the song's fitted grid (`detect_grid`), which doesn't divide the loop: rounding left passes 33–59 ms short or long. Fitting the rows to the loop instead cost timing (MI153 mean error 12.1 → 15.7 ms, shared MI164 8.1 → 13.4). So the grid stays, and the pass's last row gets its own tempo (`fit_pass`; tempo 35–166 in MI1), with pass row 1 restoring the start tempo. Every pass is within 2 ms of the game's loop (simulated from the MOD bytes: B/D/F replayed).
  - **Across the seam.** A note the grid would put past the end plays on the last row (`loop_late`: one in MI153). Release cells past the end move to the same place in the next pass (`seam`): onto rows before the track's first note, where nothing of the first pass sounds; from that note on, it takes the track. 1–22 cells per song. The rest that ends a release moves with it, so no loop rings on into the next pass.
- Track allocation, drops and steals are unchanged in both modes; timing error too except MI153 (12.1 → 12.7 ms, the late note).
- **Checked in GSSquared** (SenseiPlay, Loop NTP off): MI154 stops by itself after its 9.6 s; MI158 is still playing after 30 s (its loop is 22.4 s).
- Tests: `test_a_looping_song_repeats_exactly_as_long_as_the_game_loop`, `test_a_song_that_plays_once_ends_after_its_last_row`, `test_releases_that_cross_the_loop_sound_into_the_next_pass`, `test_a_note_the_grid_puts_past_the_loop_end_plays_on_the_last_row`.
- **Loops after an intro** (iMUSE songs, 2026-11-11): the loop starts on the row nearest its start (`loop_rows`). The intro (rows 1 .. start-1) gets patterns of its own from pattern 1, its last one ended by `D00`, and the pass starts on a fresh pattern, which `Bxx` jumps back to. A release crossing the seam moves into the pass only on a track that is silent when the first pass arrives there (else it would change what the intro left sounding); one that can't move ends with the rest on the pass's last row (`loop_halted`). Test: `test_a_loop_after_an_intro_jumps_back_to_a_pattern_start`.

## Songs that change tempo (2026-11-11)
MI1's songs have one tempo; iMUSE songs can change it (MI2: 4 songs, Fate of Atlantis: 17). `read_notes` gives times at the song's first tempo throughout, which is musical time (beats), so the grid detection and the arrangement work as for a one-tempo song, and `read_tempo_map` gives the changes as (time, speed factor). `tempo_commands` then follows a per-row ideal tempo (`_Timing.ideal_at`: the first tempo's times the factor where the row starts) with the same dithering, and a loop's pass is fitted to its real length (`_Timing.real`). Test: `test_a_song_that_changes_tempo_is_arranged_in_beats_and_its_mod_follows_the_tempo` (16 beats at 120 BPM then 16 at 150, every note within 4 ms). Not adjusted: sample envelopes and releases are laid out in beats too, so they run faster or slower by the tempo's factor where it changes.

## Custom timbres and the driver's set-up (iMUSE games, 2026-11-11)
A custom MT-32 timbre (an iMUSE song's Roland upload, `docs/imuse-format.md`) is a sound like a preset: its id (from 128) stands in for a program number throughout (`ntpsong.Note.program`, `sounds.complete`, which names it by the timbre's own name), and Munt renders it by writing the timbre into part 1's temporary area (`munt._note_file`). The game's driver set-up (`game.toml` `[mt32] init`: MI2 maps rhythm keys 24-34) is sent before every render, and `mt32.rhythm_keys` makes those keys sounds. Render and DOC-measure caches name a custom timbre by its content and the set-up by its hash (`munt.sound_tag`, `ntpmod.doc_key`); a preset with no set-up keeps its old name, so MI1's caches and MODs are unchanged. The MT-32's effect presets (programs 118-127) can end before their note-off, and smf2wav then drops the silence after them: `build_melodic` pads such a render to its note-off.

## A song that fails to build (2026-11-12)
A song whose build raises is logged with its traceback and a closing `FAILED <song>: <error>` line, and gets no MOD; the other songs build, and the variant's build succeeds unless every song failed (`ntpmod.main`). Before this, the first failure cancelled the queued songs, which cost a full rebuild per bug found (Fate of Atlantis: 009, then 020). On a disk with two variants, a song that fails in only one of them makes `diskreadme` refuse the disk (its folders must hold the same songs).

## Songs with too many sounds (2026-11-12)
A MOD holds 31 samples, and one of them is the rest; the sounds must also fit DOC RAM at their smallest (`check_fit`). MI1 needs neither rule. MI2's sound 222 plays 17 melodic sounds and 20 drum keys, and sounds that share no sample (`covers`) need one each. So `sample_set` drops the least-played sounds (`drop_least_used`: fewest notes, the later entry first on a tie) until there are at most 30. If the set still doesn't fit DOC RAM, it drops one more each time (222: 8 drum keys of 1-6 notes, which leaves 30 samples and 237/256 pages). The build log lists each dropped sound (`dropped <name> (<n> notes)`). Their notes are counted in the report's dropped notes and go unplayed. This is a stopgap: merging sounds (`covers` in the game's `mt32sounds.toml`) keeps them, but each merge needs listening. Test: `test_too_many_sounds_drop_the_least_played`.
- **Shared sets for iMUSE games.** Across all their songs, MI2 plays 71 melodic sounds and 47 drum keys, and Fate of Atlantis 75 and 32. One shared set of 30 would drop most of them, so the shared variant isn't a fit for these games without many merges.

## Staccato (trials `stacc-per-song` / `rel-per-song`, 2026-11-12; mean release rows the default since)
**Heard:** MI2's 101 lost its extreme staccato, and its drums sounded quieter than in the Munt render.

**Measured.** Every drum note is in the MOD, and a kick against a bass note matches the MT-32 within 0.8 dB. Each drum sample holds 99 % of its MT-32 energy. But per instrument, relative to the drums, the MOD's energy exceeds Munt's: Accordion +8.1 dB, Elec Org 2 +5.0, Acou Bass1 +3.8, Pizzicato +3.3. The drums aren't too quiet; the short notes are held too long. Two causes:
1. **A note lasts at least a row** (`r_off = max(r_off, r + 1)`). 101's grid unit is an 8th at 135.6 BPM (221 ms), so its rows are 110.6 ms, while its organ and accordion notes last 35-50 ms (10th percentile / median). Rows are longer than the 10th-percentile melodic note in 51 of 96 MI2 songs and 9 of 16 MI1 songs (MI152: 118 ms rows, 38 ms notes).
2. **The first release row is at full volume.** A row holds one volume, and the release used its gain at the row's first instant. Elec Org 2 and Accordion fall 20 dB within 10 ms of note-off; averaged (RMS) over a 110 ms row, their release gain is 0.31.

**Options:**
- `row_fit = "short-notes"` (`ntpsong._fit_rows`; off, `"grid"`, by default): `rows_per_unit` doubles while a row is longer than the song's `row_fit_percentile` (10th) melodic note length. Not below `row_fit_min_ms` (30: speed 3 at MOD tempo 250), nor past 120 patterns. A song opts out with its own `row_fit = "grid"`.
- `release_rows = "mean"` (**default since 2026-11-12**; `"start"` before): each release row plays the release's RMS gain over the row. The steps keep the MT-32 release's energy, though not its shape: just after the note-off a row is a little quieter than the MT-32, later a little louder.

**101, energy relative to the drums (MOD minus Munt, dB).** Neither change does much alone; together they come close:

| | now | mean release | 55 ms rows | both |
|---|---|---|---|---|
| Elec Org 2 | +5.0 | +2.9 | +2.9 | +1.0 |
| Accordion | +8.1 | +5.0 | +4.8 | +1.7 |
| Acou Bass1 | +3.8 | +2.6 | +2.9 | +2.3 |
| Pizzicato | +3.3 | +3.0 | +2.3 | +2.4 |
| Swell 1 | -6.7 | -6.8 | -6.6 | -6.6 |

Swell 1 is a separate problem. Its velocities are 5-31, so its MOD volumes are 1-2.

**MI1 builds** (per-song; steals, `.NTP` size):
- `rel-per-song` (mean release rows only): steals fall in three songs (MI160 387 -> 300, MI152 17 -> 12, MI168 1 -> 0); the files are the same size (MI150 2 KB smaller). Releases reach 0 sooner, so tracks free up earlier, and the steals left cut quieter releases.
- `stacc-per-song` (both options): 9 of 16 songs get finer rows (MI153 154 -> 39 ms, MI152 118 -> 59, MI160 85 -> 42). Steals fall further (MI160 -> 220, MI152 -> 1) and the mean timing error drops (MI153 12.7 -> 8.4 ms), but the `.NTP` files grow from 1,436 to 1,688 KB in all (MI153 +67 %, MI160 +55 %, MI152 +38 %).

**Listening (2026-11-12):** `REL.PER.SONG` is "both a fidelity improvement and a stealing improvement"; `STACC.PER.SONG` "sounds a tiny bit better, but it doesn't pay for the inflated file size". So mean release rows became the default for every game and mode, and both trial modes were removed. `row_fit` stays available per song (`[songs.<id>] row_fit = "short-notes"`) for a song whose rows are far longer than its notes (MI2's 101: 110 ms rows, 35 ms notes); no song uses it yet.

Tests: `test_short_notes_halve_the_rows_until_they_fit`, `test_mean_release_rows_play_the_release_averaged_over_the_row`.

## Build options: tradeoffs (2026-11-08)
What each option buys and costs, as measured. Sizes are totals over all 16 songs. Pattern data is 256 B per track per 64-row pattern, so allocation options change it through the track count; sample options change the sample data. Where a cost hasn't been measured, the entry says so.

**Sample sets**
- **`samples = "per-song"` vs `"shared"`.** *Per-song:* each `.NTP` carries only its own sounds, and free DOC RAM grows steady sounds into long raw loops (better sustains; per-song Elec Organ's 8192 B loop holds its rotary wobble). *Shared:* one bank for every song, byte-identical in each file, so a game can keep it resident; every sound gets a 512 B loop, and four attacks are shortened to fit (below). *Size:* per-song 1101 KB of `.NTP`, shared 1376 KB, because every shared file carries the whole ~54 KB bank; with a resident bank a game loads only header + patterns per song (shared patterns 516 KB). *Polyphony:* about the same (shared drops 2, audible steals 180; per-song 3, 280).
- **`ntp_attack_bytes` (shared: Elec Organ, Sax, Acou Bass, Vibes at 1016 B).** Keeps the 16-page sound-effect reserve in the shared bank (−16 pages), at the least measured cost: log-spectral distance vs Munt over the first 0.6 s changes −0.28…+0.06 dB.
- **`release_rows = "mean"`** (default since 2026-11-12). A release row plays the release's mean gain over the row, so fast releases (Elec Org 2, Accordion: -20 dB in 10 ms) no longer hold full volume for a row. *Gains:* closer balance to the MT-32 (M2101, energy vs the drums: Accordion +8.1 -> +5.0 dB) and fewer steals (MI160 387 -> 300). *Cost:* none in size. Heard as "both a fidelity improvement and a stealing improvement".
- **`row_fit = "short-notes"`** (off; per song if needed). Rows halve until the song's short notes fit. *Gains:* short notes stay short, timing error falls (MI153 12.7 -> 8.4 ms), more steals saved (MI160 -> 220). *Cost:* MI1's `.NTP` +17 % (1,436 -> 1,688 KB; MI153 +67 %). Heard as "a tiny bit better, but it doesn't pay for the inflated file size".
- **`reserve_pages = 16`.** 4 KB of DOC kept for game sound effects; it limits loop growth (per-song) and forces the shortened shared attacks.

**Sounds**
- **`one_shots`** (default on). A decaying sound that fades into 8-bit noise within its attack + loop budget is stored whole: only Xylophone qualifies. *Gains:* exactly in tune (was −1.0 cents), no loop of quantisation noise, 1 oscillator, frees its track when the sample ends (MI162 drops 8 → 0). *Cost:* none measured; shared bank 240 → 238 pages.
- **Loop-only Elec Organ** (trial `per-song-lo` / `shared-lo`; **rejected and removed 2026-11-09**, user: "I don't think they hold up"). *Gains:* 1 oscillator instead of 2: drops 11 → 1, audible steals 262 → 195, and with the track plan and one-shot routing 184 → 39 (shared). *Costs:* the attack is lost. Over the first 100 ms the tone is about 2 dB further from Munt, and the first 0.2–0.3 s is quieter: the 5.4 dB onset peak and the gain-1 handover are gone. **Listening: clearly audible and significantly quieter, much more so in shared.** *Size:* patterns +29…+30 KB (more 1-oscillator tracks), samples −16…−20 KB (no attack headers), `.NTP` +10…+13 KB; DOC −4 pages (shared 237 → 233). The converter rule it relied on (a loop starting at 0 takes 1 oscillator) stays modelled in `ntp_blocks` and track kinds.
- **Song-sized one-shots** (rejected 2026-11-08): +100 KB of samples (+9 %) for no polyphony gain and little audible difference; see `docs/sample-construction.md`.
- **`drum_floor_lsb = 1.5`** (default since 2026-11-08; 0 = the old −46 dB length). Drums end at the 8-bit floor, with junk near the floor after the decay cut too (above). *Gains:* the kick, snare, bongo and mute conga drop to smaller DOC blocks: shared bank 237 → 220 pages (shared-lo 233 → 216); `.NTP` shared 1480 → 1376 KB (−7 %), per-song 1142 → 1101 KB. Per-song free DOC RAM goes to loop growth: MI167, the one song at its page limit, gets its Strings 16384 B raw loop back (was halved to 8192). The shared bank's freed pages are unused so far. A shorter drum also frees its track sooner. *Polyphony:* unchanged (drops and steals identical in all four variants). *Cost:* the last few dB of each tail (below 8-bit resolution); not yet listened to.
- **`tremolo`** (trial `shared-trem`, 2026-11-09; **rejected and removed**: the organ pumped, its wobble being tonal, and no measurement predicts which sounds are safe). Replayed the MT-32's chorus/beating level modulation in the per-row `Cxx` of sounds whose 512 B averaged loop is flat ("Tremolo trial" below). *Gains:* MI159's chords pulse as in Munt instead of holding still. *Size:* `.NTP` unchanged (fixed cells); deflated patterns 92 → 93 KB; DOC unchanged. *Polyphony:* audible steals 181 → 185 (the quietest-release choice moves). Not yet listened to.
- **`halt_before_delayed`** (default on). Halts a 2-oscillator track still sounding on the row before a delayed note (or moves the note onto a row when that row starts one). *Gains:* no tick-0 ghost of a still-sounding note (MI160 on the IIGS: 336 → 2 jumps over the song, the loud ones +24 to +35 dB). *Cost:* the old note ends up to a row early (release tails at −30 dB or below); moved notes shift 1–2 ticks (per-song MI160 5, MI167 19; MI167 mean timing error +0.6 ms). Patterns, samples and sizes otherwise unchanged ("Ghosts on still-sounding tracks").
- **`halt_released_loops`** (default on). Ends each release on a 2-oscillator track with a silent looped rest that replaces the loop. *Gains:* no ghost of the previous note before a delayed note, and delayed notes start their loop at the right volume ("Flicker"). *Cost:* one 512 B rest sample (2 pages) per set; per-song MI167 was at its page limit, so its Strings loop halved (16384 → 8192 B) until the drum trim freed pages.

**Sampling**
- **`pitch_plan`** (default on since 2026-11-09, both modes; `pitch_plan = false` restores the configured keys, e.g. as a trial mode with a `base`). Each sound's sampled key(s), up to `pitch_max_zones` key zones (each needing ≥ `pitch_zone_gain_db`; a lone sample moves only for ≥ `pitch_move_gain_db`) and short-loop rate, from the notes played ("Sampled pitch and rate"). *Gains:* the DOC-vs-Munt measure −21.5 → −26.1 dB per-song, −21.3 → −22.9 shared. Listening: MI152 "way way better"; after the Panpipes and Pipe Organ fixes, "everything else I've listened to sounds good". *Costs:* per-song `.NTP` +168 KB (+14 %); per-song steals 275 → 433 (MI160); extra zones take DOC RAM before loop growth.
- **Hybrid planner** (default since 2026-11-09, both modes: `pitch_zones = "hybrid"`, `drum_fill_block = true`, `sample_budget = "value"`; the old planner is `"weighted"`, false, `"zones"`). Free zone boundaries with every played key counted; drums at the highest rate their block holds; zones and drum blocks bought from one DOC budget by dB × exposure per page down to `upgrade_min_db_per_page` (0.011) ("Hybrid key weights, drums and one value budget"). *Gains:* per-song pitch error better on both measures (coverage −24.3 → −25.2, weighted −26.0 → −26.2); drums −9.6 → −16.5/−21.6 dB (Kick) and similar; shared uses 17 idle pages on drums. Listening: "overall I think it sounds better"; with the 0.011 threshold, MI160's organ "Nailed it!". *Costs:* per-song raw loops fewer and shorter (several steady sounds lose theirs; MI160 Elec Organ kept by the threshold); more samples (+36 KB of `.NTP`).
- **Raw-loop rate** (`raw_loop_tune_cents = 1.0`, `raw_loop_max_rate_drop = 0.1`; all modes). Raw loops take the highest in-tune rate instead of the closest tuning. *Gains:* per-song −20.2 → −21.5 dB (Elec Organ −19.8 → −23.6, Shakuhachi −20.9 → −24.8). *Costs:* per-song `.NTP` 1116 → 1201 KB; steals 284 → 275 (fewer). `raw_loop_tune_cents = 0` restores the closest tuning.

**Envelope**
- **`env_max_rise_db = 1.5`** (default since 2026-11-09; 0 = off). Sustain rows rise at most 1.5 dB a row ("Smoother sustains", step 5). *Gains:* no pumping on beating sounds on the IIGS, where a volume step plays instantly: rises ≥ 2.5 dB MI168 41 → 5, MI167 50 → 4. *Costs:* French Horn and Strings lag their contour on fast rises (mean ≤ 0.65 dB per row); no other part over 0.1 dB. *Size, polyphony:* unchanged (notes, drops, steals identical). Not yet listened to.

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
- **`delay_snap_ms = 20`** (default since 2026-11-11; 0 = off; trialled as modes `per-song-snap20`, `per-song-snap30`). Snaps by distance rather than by level: a delayed note whose onset is within this many ms of its nearest row moves onto it with its velocity in-cell, however close the default volume is (`delay_snap_db` still moves the far-off ones at any distance). Same exclusions: a note whose velocity is the default (it gains nothing), one the cell before can carry, and the flam/roll guard. *Why ms:* at speed 3 a delayed note lies ½–1½ ticks from its row, and a tick is 18–51 ms depending on the song's tempo. The default-volume notes of per-song (`v:dflt`, 5,001) lie 15 / 18 / 24 / 34 / 43 ms from their row at the 10 / 25 / 50 / 75 / 90th percentiles; the build log prints this line for every variant. Per-song, all 16 songs:

  | `delay_snap_ms` | moved by it | delayed (`EDx`) | velocity in-cell | default volume | mean timing error | ghost halts | drops / steals |
  |---|---|---|---|---|---|---|---|
  | 0 (default) | 0 | 5,856 | 6,045 | 5,001 | 8.09 ms | 548 | 1 / 398 |
  | 20 | 1,082 | 4,779 | 7,122 | 3,920 | 8.75 ms | 413 | 1 / 408 |
  | 30 | 2,430 | 3,530 | 8,370 | 2,768 | 10.57 ms | 225 | 2 / 309 |

  Of 11,902 notes. Notes within the threshold that stay delayed are guarded flams and rolls or already at the default volume. Most moved by song at 20 ms: MI160 408, MI163 225 (its 55 ms rows: mean timing error 4.6 → 7.1 ms), MI161 85, MI150 93; MI152, MI153 and MI167 (slow, 112–154 ms rows: ticks of 37–51 ms) hardly change. *Gains:* more notes start at their own velocity (`v:dflt` −22 % / −45 %) and fewer need a ghost halt. *Cost:* timing: each moved note shifts by up to the threshold, toward the grid (flattening the MT-32 performance's micro-timing). Patterns and samples unchanged in size. **Listening (user, 2026-11-11, IIGS):** "really good"; 20 ms preferred over 30; made the default.
- **`keep_volume_db`** (trial `per-song-keep3`, 2026-11-11; rejected and removed). A delayed note on a track still holding its sample at a volume within 3 dB of its own left out its instrument, so it played at the track's volume with its `EDx` kept and nothing halted (`ninjatrackerplus.s`: `setup_effect` writes a volume only when the cell names a sample; `check_play_note` plays `curr_sample` with control and frequency writes only). NTP keeps no per-note velocity, only each track's volume register, and most delayed notes find their track halted (the rest) or holding another sound: 412 notes kept at 3 dB (639 at any difference), default-volume starts 3,920 → 3,606, timing error 8.75 → 8.66 ms. Dropped as not worth a fourth placement rule and a melodic cell form not verified in a DOC log.

**Timing grid** (`rows_per_unit`, `speed`, `quantize`; full table in `plans/ntp-quantization.md`)
- **Current 2 rows per unit, speed 3:** 45.7 % of notes start at the default volume for 1–2 ticks (median 1.6 dB off), timing error 7.7 ms, patterns 527 KB (before the plan).
- **Finer rows** cure velocity loss (6 rows/unit: 0 %) but cost 2.8× the pattern data. **Row packing** (1 row/unit, speed 6) halves it (295 KB) at the same velocity and timing, but more than doubles steals. **More ticks per row** (2, 6) halves timing error and loses more velocity (67 %). Quantization is on hold.

**Volume commands and size (measured 2026-11-09, shared, all songs).** NTP patterns are uncompressed fixed 4-byte cells (format above), so the per-row `Cxx` volume envelopes cost nothing in the `.NTP`: the size is set by tracks × patterns alone. Cells: 57 % empty, 28 % volume-only `Cxx`, 10 % note + `Cxx`, 4 % note + `EDx`, 0.5 % `Fxx`. They would cost under compression: patterns 505 KB raw, 92 KB deflated, **45 KB deflated without the volume-only cells**. So the envelopes are about half of any compressed music data, and they keep repeated sections from matching for pattern de-duplication. Their other cost is the single effect column (see "Velocity of delayed notes").

**Current totals:**

| variant | `.NTP` total | patterns | samples + header | largest |
|---|---|---|---|---|
| per-song | 1101 KB | 516 KB | 585 KB | MI160 132 KB |
| shared | 1376 KB | 516 KB | 860 KB | MI160 134 KB |

## Tremolo trial (`shared-trem`, 2026-11-09; rejected and removed)
User report: in Munt's MI159 the main chords have a slight tremolo. **It isn't in the MIDI:** MI159 has one CC7 and one CC10 per channel at the start, and no modulation, aftertouch, pitch bend or SysEx. **It's the patches:** Elec Org 2 (channel 3, the chords) and Chorale (channel 4) pulse by themselves while held.

**Measured (Munt, 8 s holds, 2026-11-09):** the modulation is quasi-periodic beating, not an LFO. Its period changes erratically with key, and the autocorrelation peak is only 0.4–0.7:

| sound | swing p5–p95, 35 ms smoothing | 70 ms | period by key (50…79) |
|---|---|---|---|
| Elec Org 2 | 8.9–10.8 dB | 6.5–10.0 dB | 0.28–1.35 s |
| Chorale | 4.5–5.3 dB | 3.8–5.0 dB | 0.19–1.04 s |

The organ also has sharp 20–40 ms dips (−11 dB) that no row rate can follow. **What we kept, MI159 loops:** per-song raw loops 9.4 dB (organ, 8192 B) and 6.4 dB (Chorale, 16384 B); shared 512 B averaged loops 0.0 dB. That flatness is by design, since a short raw slice of the wobble looped at 15 Hz ("Tremolo from short raw loops").

**Trial (removed):** `tremolo` (sound names, per mode) recorded a trace for each key in the sound's range: dB of the 35 ms-smoothed envelope minus the 0.8 s contour (`tremolo_traces`), per 10 ms, faded in over 0.2–0.5 s. `env_at` multiplies each loop row by the trace of the nearest key, averaged over the row. So every chord voice pulses with its own key's pattern, as on the MT-32, and notes longer than the 8 s trace replay it from 1 s. It applies only to averaged (≤ `loop_bytes`) loops: raw loops already carry the motion. MI159's organ rows went from a held 4 to moving between 2 and 8. Those low volumes step by 1–2.5 dB, so the motion is coarse. The trace overshoots a pure sine's swing by about 1 dB, because the 0.8 s contour isn't a whole number of periods.

**First listening (user, 2026-11-09): little difference in shared MI159.** Why, measured:
- MI159's organ chords (channel 3) are staccato: 144 notes, median 0.21 s, none over 0.5 s. They're over before the trace fades in, and before one wobble period (0.3–1.3 s). Only 44 organ cells changed. In 0.2 s the MT-32 shows at most one of its sharp dips, which rows can't carry.
- Chorale (channel 4: median 1.6 s, p90 3.2 s) does carry it: 554 cells changed, at its gentler 4–5 dB.
- The one long organ note, a 40 s drone (channel 2, key 62), **faded to volume 1 after 2 s in every mode**. That was a bug, below.

**Second listening (user, 2026-11-09):** the per-song organ "sounds really good" with the contour fix. The shared-trem organ has "a slightly off-putting mmmlllleeemmmlleemm sort of quality", worse than the flat shared one; audible in MI160. **Why, measured** (Munt, key 62, 8 s, STFT band levels over 0.8–7.5 s, p5–p95 swing):

| band | Elec Org 2 | Chorale |
|---|---|---|
| overall | 10.1 dB | 5.6 dB |
| 0–500 Hz | 2.5 | 7.0 |
| 500–1000 Hz | 16.6 | 7.5 |
| 1–2 kHz | 11.4 | 10.4 |
| 2–4 kHz | 5.1 | 5.4 |
| 4–8 kHz | 1.6 | 2.2 |

The organ's fundamental hardly moves. Its 2nd–5th harmonics come and go (detuned copies beating), so the wobble is a change of tone, a slow wah, not of loudness. A `Cxx` tremolo on a fixed-tone loop moves the fundamental too, which is a pumping "mlem". Raw loops (per-song) record the tone change itself.

**No general gate, so the trial was removed (2026-11-09).** The aim: decide per sound from a measurement, not from names, since the pipeline should serve other soundtracks too (user). The candidate was a "level share": the energy-weighted fraction of the band-level motion (6 octave bands, 0.8 s contour removed) that the overall level explains. It ranks the sounds the wrong way: Elec Organ 0.77 (rejected by ear) is above Shakuhachi 0.59, Chorale 0.53, Strings 0.51 and Fantasy 0.40. What sounds wrong is the fundamental being dragged along: it's quiet in energy terms but carries the pitch. The underlying limit is general: a `Cxx` tremolo on a fixed-tone loop changes loudness where the instrument changes tone. Only a raw loop carries that motion. Raw loops are already chosen by measurement, with no names: `is_steady`, the `raw_loop_ok` gate and usage-based growth.

**Bug found: steady contours drooped at the sampled note-off (fixed 2026-11-09).** A sound's row contour comes from a 2 s render (`hold_seconds`). Its 0.8 s smoothing window reached past note-off into the release and pulled the last 0.4 s down: at 1.95 s, Elec Organ −14.5 dB, Strings −9.3, Chorale −4.0. Envelopes hold their last value, so **every steady note held past ~1.7 s played 4–14 dB too quiet** from then on, in every mode. The loop-only trial's quietness and MI153's quiet long organ notes may partly be this. **Fix:** `sustain_contour` smooths only the held part, up to onset + hold − 20 ms (smf2wav drops leading silence: `docs/iigs-midisynth.md`). The tremolo traces end there too; reaching 0.5 s into the release had given the drone a +14 dB spike at 7.3 s. **Effect:** the MI159 drone holds 4 (shared) instead of fading to 1. Per-song-and-instrument summed level rises up to +1.2 dB (MI153 Pipe Organ, MI159 Elec Organ/Chorale, MI165 Trombone/Shakuhachi, MI167 Strings); single long notes rise up to 14 dB. Polyphony: audible steals shared 181 → 184, per-song 282 → 284; drops unchanged.

## Sampled pitch and rate (`pitch_plan`, 2026-11-09; default on)
User report: the organ chords in MI152 are "pretty aliased" and dark. Each sound was sampled at one key from `config/games/monkey/mt32sounds.toml` (Elec Organ: 60). Per-song MI152 plays it at keys 37–67 (median 50), and 43 % of its notes lie an octave or more below 60. A sample played n semitones below its key runs at rate × 2^(−n/12): at key 37 the stored 8.3 kHz bandwidth comes out at 2.2 kHz (dark), and the DOC's drop-sample stepping leaves images of the spectrum in the audible band (grit).

**Measure (`docsim.py`).** For a sample of key k at rate r playing key n: the Munt render of k, resampled to r and rounded to 8 bits, is stepped through at the DOC rate (26 320 Hz: NTP enables all 32 oscillators, `$E1 = $3E`), without interpolation. The output is compared with Munt's render of n: time-averaged spectra (0.03–0.6 s), A-weighted to 12 kHz, level-normalised. Harmonics are compared as magnitudes, and the energy between them (images, noise) as a total. The result is a distortion-to-signal ratio in dB. Time averaging keeps modulation out: two renders of a wobbling organ differ in its phase, not in quality (frame-by-frame comparison scored a perfect key-60 sample at −14 dB instead of −25). Results are cached in `build/cache/docsim.json`. Floor (each note sampled at its own key, 16.7 kHz, 8 bits): −24 to −27 dB for the organ.

**Planner (`pitchplan.py`).** The weights are the note-seconds per key over the songs that use the sound: one song for per-song, all songs for shared. Candidates are every key in the played range, at the rates its E′+ 512 B loop allows: whole cycles dividing 512, so the rate is the key's frequency times a power of two, within `min_rate`–`max_rate`. A candidate must also reach its zone's keys within the 72-note period table. For 1 to `pitch_max_zones` zones, the best contiguous split of the played keys and the best candidate per zone are found exactly by dynamic programming. Extra zones go in, largest gain first, while one gains ≥ `pitch_zone_gain_db` (1.0 dB) on its sound's mean and fits the DOC pages and the 31 MOD slots. Loops grow afterwards into what is left. Zones become key splits like `config/games/monkey/ntp.toml`'s (sample names carry the sampled key, e.g. "Elec Organ 49").

**MI152 Elec Organ (per-song notes, measure in dB):**

| sample(s) | 16.7 kHz | 22 kHz | 26.3 kHz |
|---|---|---|---|
| key 60 (configured) | −18.4 | −19.0 | −20.4 |
| key 52 | −21.6 | −22.8 | −23.8 |
| best 1 zone | | −23.5 (key 49) | −24.1 (key 55) |
| best 2 zones | | −25.5 (49 / 61) | −25.7 (43 / 55) |

Per note, key 60 played at keys 38–45 scores −8 to −10 dB in harmonics and −17 to −20 dB between them, against −27 to −29 near 60. A nearer key fixes both.

**Rate above 22 kHz: dropped.** It adds 0.2–0.6 dB once keys are planned (table above), and costs the raw loops: an 8192 B loop at 25 kHz repeats 3 times a second and fails `raw_loop_ok`, so steady sounds lose their wobble. The user: "the only reason I was interested in higher sample rate was for quality". The plan's rate applies to short (512 B) loops only (`rate_window`); raw loops choose their own rate in the config window. Rate does matter for bright sounds: a 16 kHz sample's content at 7 kHz leaves an image at 16 − 7 = 9 kHz (synthetic tone: −17 dB at its own key, −50 dB at 26.3 kHz); the organ has little energy up there.

**Raw-loop rate (`raw_loop_tune_cents`, `raw_loop_max_rate_drop`; all modes, 2026-11-09).** A raw loop holds any whole number of cycles, so `best_tuning` found a near-exact tuning somewhere in 9–22 kHz and took the first: per-song Elec Organ played at **9165 Hz** in every song (8192 B, 0.89 s), Panpipes at 14 903 Hz, Shakuhachi 11 154, Chorale 10 981. The attack is stored at the loop's rate, so this costs the whole note. Measured on Panpipes (key 72, keys 74–81, `sus.py`: the built sample through the DOC, early = 0.03–0.6 s, sustain = 0.6–1.8 s): 512 B at 16.8 kHz −26.6 / −26.4 dB; 16384 B at 14.9 kHz −24.8 / −25.5; the same loop at 20.6 kHz −27.6 / −28.8. Rule now (`_raw_tuning`): the **highest rate tuned within 1 cent**; if the loop isn't clean there (`raw_loop_ok`), the highest so tuned at which it repeats at most `raw_loop_slow_hz`; and never more than 10 % below the short loop's rate. Without that floor the slow-repeat fallback put Sax (MI163) at 8192 B / 16.3 kHz: −20.3 dB early against the short loop's −21.7 at 18.9 kHz. Elec Organ's 8192 B wobble loop now plays at 15.7 kHz (−6 %), its 16384 B at 21.4 kHz. The measure prefers the short loop to most raw loops (Elec Organ key 60: 512 B −22.7 dB, 16384 B −20.6): it averages over time, so it can't hear the wobble they exist for. It therefore sets their rate, not whether they are used.

**Raw loops join their attack in phase (all modes, 2026-11-09).** A loop starts at a rising zero crossing; the attack ends wherever `attack_bytes / rate` falls in the cycle. Raw loops picked the crossing best correlated with the attack's tail, but when the tail ends half a cycle off, every crossing is anti-phase: Panpipes 16384 B at 21 770 Hz scored −0.97 at best, and the crossfade dipped 13.6 dB for a few milliseconds on every note (Trombone 16384 B: 6.5 dB; Elec Organ 8192 B: 3.0). Which songs it hit depended on the rate, so changing the rate moved the dip around: this was the "transition from attack to sustain slightly misaligned". Raw loops now use E′+'s `join_aligned`: the attack's source start moves up to one period earlier (pre-onset silence) so that it ends in phase. Join dips are now 0.6–2.3 dB (one-cycle windows; the sound's own movement). Test: `test_raw_loop_joins_its_attack_in_phase` (the old join failed it with 2.7–5.1 dB dips).

**Key moves need a gain (`pitch_move_gain_db = 0.5`).** User report (MI154): Panpipes "rougher … a long loop that it doesn't really benefit from, and the transition from attack to sustain slightly misaligned". The plan had moved it from key 72 to 74 for 0.0 dB. The measure covers the first 0.6 s, not the loop that a new key and rate get: at key 74 its raw loop shrank 16384 → 8192 B at 11.6 kHz with a 2.8 dB level pump at 1.4 Hz (0.6 dB at key 72), and the attack's last 256 bytes sat 0.7 dB below the loop (0.1 at key 72). A single sample now leaves its configured key only for ≥ 0.5 dB.

**One-shots stay one-shots.** The measure can't see polyphony. Xylophone is a one-shot at key 76 (one oscillator per note); the plan first moved it to key 63 at 19.9 kHz, where its decay no longer fits the 2 KB block, so it looped, and MI160 dropped 13 notes (was 1) with 413 steals (was 261). Now a sound that is a one-shot at its configured key may only take keys where it still is one, at the one-shot's rate (`ntp_shot_rate` → `one_shot_tuning`).

**Results (measure, note-seconds weighted, dB; main before this work → now):**

| sound | per-song | per-song-pitch | shared-pitch |
|---|---|---|---|
| Elec Organ | −19.8 → −23.6 | −27.5 | −25.0 |
| French Horn | −17.3 → −18.0 | −24.7 | −18.6 |
| Pipe Organ (all three) | −12.7 | −27.2 | −20.5 |
| Shakuhachi | −20.9 → −24.8 | −28.2 | −23.8 |
| Chorale | −19.9 → −21.8 | −26.4 | |
| Panpipes | −21.6 → −23.0 | −26.1 | |
| **all** | **−20.2 → −21.5** | **−26.1** | **−22.9** (shared −21.3) |

Main per-song gains come from the raw-loop rate alone; shared and shared-pitch have no raw loops and don't change with it. Shared has no free MOD slot (31 samples), so it only moves keys; per-song adds zones (MI160: 21 samples, 3 for Acou Bass). *Size (`.NTP` total):* per-song 1116 → 1201 KB (raw loops at higher rates are longer), per-song-pitch 1369 KB, shared 1376 KB, shared-pitch 1370 KB. *Polyphony (drops / steals):* per-song 3 / 284 → 3 / 275; shared 1 / 184; per-song-pitch 1 / 433 (MI160 415); shared-pitch 1 / 105. *Build time:* the first run measures every candidate (≈ 7 ms a note); later runs read the cache. Listening (per-song-pitch): MI152 "sounds way way better"; with the Panpipes (MI154) and Pipe Organ (MI153) fixes, "everything else I've listened to sounds good". It was then made the default for both modes; the trial modes `per-song-pitch` / `shared-pitch` (the names used in the measurements above) became `per-song` / `shared`.

**A planned rate with no tuning (2026-11-12).** The plan picks a short loop's rate for the sampled key's nominal pitch, and the build tunes within +-3 % of it (`rate_window`). E′+ loops need a whole number of periods dividing the loop. A sound that measures well off its key's pitch can leave none: Fate of Atlantis's Triangle (program 121) is 361 Hz at key 65, 58 cents sharp, which gives 16.07-17.07 periods in 512 B. The build then tunes it in the config's whole window (`_tune_loop`), as for an unplanned sound, rather than failing. Test: `test_a_planned_rate_with_no_whole_cycle_tuning_falls_back_to_the_config_window`.

### Even zones (trial `per-song-even` / `shared-even`, 2026-11-09; replaced by the hybrid trial and removed)
**User report:** the sampled keys for the pipe organ are "not super evenly distributed".
- Per-song MI153 Pipe Organ had 52–75 → key 74, 76–83 → 76, 84–91 → 88: key 52 played from a sample 22 semitones above it, while two samples sat 2 semitones apart.
- Across the 42 multi-zone per-song sounds: MI162 Shakuhachi had samples at 63 and 64; MI163 Acou Bass played 41–54 from key 40; MI160 Panpipes had zones 38–45 and 66–84.
- **Cause:** `pitchplan.plan` minimises the *note-seconds-weighted* error, so samples gather where notes are held longest.

**Trial rule (`pitchplan.even_plans`):**
- zones of equal width across the sound's played range (per song, or every song's union for shared);
- each zone takes the key and rate with the lowest docsim error averaged over **every key in the zone, played or not**;
- the zone count and `pitch_move_gain_db` use the same equal-weight mean;
- shared still has one sample per sound (no free MOD slot), with its key now chosen for the whole range.

**Up or down?**
- **Practitioner advice:** small shifts, root mid-zone ([Sound On Sound](https://www.soundonsound.com/sound-advice/q-how-can-get-my-multisampled-synths-sound-more-even), [Polyphone forum](https://www.polyphone.io/en/forum/help/283-advice-on-how-to-lay-out-key-ranges-to-samples)). It disagrees on direction ([Elektronauts](https://www.elektronauts.com/t/making-sample-banks-of-your-analog-synths/149168?page=2): lower usually works better); no controlled study was found.
- **On the DOC the measure is asymmetric the other way.** Mean extra error vs the sampled key, from `build/cache/docsim.json` (averaged over whatever key/rate pairs were measured, so indicative):

  | Sound | 6 semitones down | 6 up | 12 down | 12 up |
  |---|---|---|---|---|
  | Elec Organ | +7.1 | +0.7 | +12.3 | +3.1 |
  | Strings | +8.0 | −0.4 | +12.2 | +3.0 |
  | Panpipes | +5.1 | +0.6 | +9.4 | +3.8 |
  | Trombone | +8.1 | +2.5 | +12.9 | +6.9 |
  | Pipe Org 2 | +9.8 | +8.1 | +15.1 | +15.4 |

- **Why:** a sample played below its key loses bandwidth with it, and drop-sample stepping leaves images. The planner doesn't snap to the nearest sample; the measure places each key in its zone.

**Results** (both planners scored on the same two measures, dB, lower is better):

| | Coverage (every key once) | Note-time weighted |
|---|---|---|
| per-song → per-song-even, 62 sounds | −24.28 → −25.00 | −26.00 → −25.28 |
| shared → shared-even, 16 sounds | −20.43 → −20.71 | −23.65 → −22.95 |

- **Zones changed:** 48 of 62 per-song sounds and 8 of 16 shared ones.
- **Biggest coverage gains:**
  - MI167 Elec Organ 46–72:64 → 46–59:49 | 60–72:64 (−21.5 → −26.8);
  - MI168 Elec Organ (−22.0 → −26.4);
  - MI163 Acou Bass (−16.7 → −20.6, three zones);
  - MI160 Panpipes (−18.3 → −21.6);
  - MI153 Pipe Organ 52–65:60 | 66–78:73 | 79–91:86 (−21.6 → −24.1; held notes −26.1 → −23.8).
- **Biggest held-note losses:**
  - MI162 Shakuhachi (−29.7 → −24.7);
  - MI153 Pipe Organ Lo (−28.8 → −25.0);
  - MI151 Panpipes (−26.0 → −22.9);
  - shared Strings 70 → 64 (−28.0 → −24.3, coverage unchanged).
- **Side effect:** unplayed keys count. MI153 Pipe Organ Lo2 gets three samples for keys 36–38, one of them for key 37, which the song never plays.
- **Cost:**
  - per-song samples 118 → 126, DOC pages +98 in total; no raw loop lost;
  - steals per-song MI152 11 → 24, MI160 414 → 263 (+1 drop), MI151 3 → 1;
  - shared MI160 92 → 131, MI151 0 → 2.
- `per-song` / `shared` stay byte-identical (the default is `pitch_zones = "weighted"`).
- **Side fix:** `_raw_tuning` treats a raw-loop size with no whole number of periods in its rate window as no fit. Even zones sampled Pipe Organ Lo2 at 37, a 34.4 Hz two-period cycle, and `best_tuning` raised.
- Not listened to: replaced by the hybrid trial below. Equal widths were too rigid. Against the weighted plan, 13 per-song sounds were better on both measures, 15 worse on both, and 17 traded one for the other; where played keys cluster or leave gaps, a fixed cut splits a cluster or spends a zone on a gap.

### Hybrid key weights, drums and one value budget (trial `per-song-hybrid` / `shared-hybrid`, 2026-11-09; default since 2026-11-09)
**Pitch plan (`pitch_zones = "hybrid"`, `ntpmod.key_weights`).**
- The weighted plan's free zone boundaries (`pitchplan.plan`) are kept. Each played key weighs `pitch_key_share` (0.5) of an equal share per played key plus the rest by note seconds.
- Every played key pulls on the zones, long notes pull harder, and unplayed keys never count.
- MI153 Pipe Organ: 52–75: 74 | 76–83: 76 | 84–91: 88 → 52–67: 60 | 69–80: 74 | 81–91: 88. Pipe Organ Lo2: 36 | 38 (no sample for the unplayed 37).

**Drums were "pretty crunchy" (user, 2026-11-09).**
- **The rate rule:** `drum_rate` follows where 95 % of the energy lies, and for drums that is mostly bass. Seven of the 14 drums were stored at 5872 Hz:
  - Kick, Tom, Claves, Bongo, Mute Conga, Conga and Timbale;
  - their Nyquist is 2.9 kHz, so Kick's click (99 % of its energy lies below 3.1 kHz) and Timbale's ring (below 5.1 kHz) were cut;
  - drop-sample playback at 26.3 kHz leaves images around 5.9 and 11.7 kHz.
- **Half-empty blocks:** several drums left half their block unused (Kick 788 B of a 1 KB block). Cabasa and Quijada were held down by their configured bytes.
- **Measure:** `docsim.drum_error_db`, the A-weighted distortion of the DOC playing the stored drum (8-bit, drop-sample, at its offset for each covered key) against Munt, over 39 ms frames across the hit, level-free.
- **Error by rate, dB / DOC pages, to the 8-bit floor:**

  | Drum | 5.9 kHz | 11 kHz | 14 kHz | 26.3 kHz |
  |---|---|---|---|---|
  | Kick | −9.6 / 4 | −15.5 / 8 | −16.6 / 8 | −23.2 / 16 |
  | Snare | −3.8 / 4 | −13.1 / 8 | −13.4 / 8 | −21.4 / 16 |
  | Closed Hat | −0.3 / 2 | −3.4 / 4 | −4.5 / 8 | −11.8 / 8 |
  | Claves | −4.6 / 2 | −13.0 / 4 | −14.4 / 4 | −23.3 / 8 |

- **Fix (`drum_fill_block`):** a drum takes the highest period-table rate at which the whole drum still fits the block it occupies (or the one the budget gives it, `ntp_bytes`).
  - **Free gains:** Kick −12.0 dB in its 4 pages; Snare −13.7 dB in 8; Bongo −12.1 dB in 1.
  - **`drum_steps`:** each doubling up to the top rate. Tom gains 0.2 dB at 16 pages and 3.8 more at 32.

**One budget (`sample_budget = "value"`, `ntpmod._value_budget`).**
- Extra zones (an attack + short loop and a MOD slot) and bigger drum blocks compete for DOC pages, the best value per page first, down to `upgrade_min_db_per_page` (0.01).
- **Value** = dB gained × the sound's share of the set's exposure, each note or hit counted up to 0.6 s (what both measures see).
- A drum may jump to any larger block.
- What is left grows loops, as before.
- Example, MI160:
  - zones for Elec Organ (+2.8 dB, 0.076/page) and Panpipes (+5.7 dB);
  - drums Mute Conga 2 → 4 pages (−15.7 → −22.5 dB), Kick 4 → 8 → 16 (−12.0 → −21.6 dB), Conga, Claves and Bongo;
  - Marimba and Acou Bass zones after them.
- Shared (no free slot): only drums. Kick, Mute Conga, Conga, Claves and Bongo use 17 pages that were idle (221 → 238 of 240).

**Results** (`per-song` / `shared` byte-identical):
- **Pitch plan** (the two measures of "Even zones"): per-song coverage −24.28 → −25.15 dB and note-time weighted −26.00 → −26.17, both better.
  - 32 of 62 sounds better or equal on both, 3 worse on both (MI160 Fantasy and Shakuhachi, MI167 Trombone: low exposure, so they lost zones to drums), 5 trades;
  - shared −20.43 → −20.57 and −23.65 → −23.52 (French Horn traded, Trombone slightly worse).
- **Polyphony:** steals MI160 414 → 372, MI152 11 → 17, MI151 3 → 2, MI168 2 → 1; shared unchanged; no drops.
- **The cost: long loops.** The measure can't price them, and zones and drums now take pages first.
  - Per-song raw loops: 26 × 16 KB + 6 × 8 KB → 12 × 16 KB + 14 × 8 KB.
  - Lost entirely: MI162 Shakuhachi (both), MI167, MI159 and MI152 (key 49) Elec Organ, MI168 Trombone, MI151 Panpipes.
  - Halved: e.g. MI160 Elec Organ, MI153 Pipe Organ 60.
  - Per-song samples 215 → 248, `.NTP` 1341 → 1377 KB.
- **Listening (2026-11-09):** "overall I think it sounds better", but "the organ is noticeably not as good on 160": its Elec Organ 64 16 KB loop had gone to 512 B, the 49 zone getting 8 KB.
- **Threshold 0.01 → 0.011:** the lowest that gives it back; scanned on MI160 at 0.011 / 0.014 / 0.017 / 0.024 / 0.028 / 0.040. Only the last upgrade drops there: a 3rd Acou Bass zone, 0.010/page.
  - Over all songs: MI160 Elec Organ 64 back to 16384r (steals 372 → 378);
  - MI167 Elec Organ's 3rd zone (+0.6 dB) gives way to an 8 KB French Horn loop;
  - MI163 Sax loses its 2nd zone (+2.5 dB, little played);
  - MI151 Rim and MI161 Mute Conga keep their filled block instead of a doubling (+1.5, +1.6 dB);
  - shared-hybrid unchanged.
- **Default (2026-11-09).** After listening ("Nailed it!"), `pitch_zones = "hybrid"`, `sample_budget = "value"` and `drum_fill_block = true` became the defaults for both modes, and the trial modes became `per-song` / `shared`. The measurements above name them `per-song-hybrid` / `shared-hybrid`; the old planner is a commented `[modes.per-song-weighted]` example in `config/games/monkey/ntp.toml`.

### Attacks sized to their measured length (trial `attack-per-song` / `attack-shared`, 2026-11-10; rejected and removed 2026-11-11)
**Question (user):** are attack blocks sized to the attack they hold, and do they use all their RAM?

**How attacks are stored:**
- Every looped sound gets a fixed attack block: 2040 B (a 2 KB block with the 8 stopper bytes), or 1016 B where `ntp_attack_bytes` says so.
- The block is always full. The attack is cut to exactly that many bytes at the sound's rate, so the rate only sets how long it lasts (2040 B: 97 ms at 21 kHz, 213 ms at 9.6 kHz).
- Loops and attacks are separate power-of-two blocks in swap mode.

**Measured** (every built looped sample, `loopbuild.settle`'s attack end against what its attack holds):

| | shared | per-song (16 songs) |
|---|---|---|
| attacks cut short | 7 of 15 | 55 of 145 |
| attack pages now | 96 | 1128 |
| pages to hold every whole attack at its rate | 408 | 5365 |

- Most attacks are far shorter than their blocks: Acou Bass 10–52 ms, Marimba 14–43, Taiko 15, organs and Strings 35–44.
- A few steady sounds settle slowly, plausibly the MT-32's filter sweeps: French Horn 1.0–1.1 s, Chorale 1.2–1.3 s, Trombone 162 ms (key 52) rising to 1.6 s (key 72).
- `settle` is unreliable on wobble and beating: Elec Organ measures 46 ms at key 64 but 1731 ms at 63 and 2364 ms at 71.

**Trial rule** (a pass before the value budget):
1. Each attack became the smallest of 504/1016/2040 B holding `settle`'s end plus the 4-cycle crossfade, at most `attack_bytes`. 248 B was excluded: GSSquared starts swap loops up to 255 samples early.
2. A change was kept only if `loopbuild.attack_error_db` (spectral shape vs Munt, first 0.5 s) worsened by at most 0.1 dB.
3. Freed pages went to the budget and loop growth; zones were priced by the blocks their own fitted attacks added.
4. Where a block held more than the attack, a short loop's rate rose to the highest whole-cycle rate that still fit.

**Results:**
- *per-song:*
  - 18 attacks changed: 14 shrank 2040 → 1016 B, and 4 MI153 organ attacks grew 1016 → 2040 B (attack error 3.6–4.8 → 2.7–4.2 dB).
  - DOC use fell 4–20 pages in 7 songs. MI161 and MI162 spent it on 8 KB raw loops.
  - Losses: MI153's grown attacks cost Pipe Organ Lo2 its second zone and Pipe Organ Lo 48 its raw loop, and MI160 lost a Panpipes zone. Steals rose 398 → 408.
- *shared:*
  - Five 1016 B attacks grew back to 2040 B (Sax 1.03 → 0.61 dB), for +12 pages.
  - Those pages came out of the drums: Mute Conga, Conga and Claves lost their upgrades. Steals rose 103 → 111.
- *Step 4 never fired at `max_rate` = 22 kHz.* With its cap raised to the DOC's 26 320 Hz, it fired once (per-song MI153 Pipe Organ Lo 43, 12 557 → 25 114 Hz) with attack error unchanged (3.59 dB). Why:
  - E′+ needs whole samples per cycle in a power-of-two loop, so a key's rates are an octave apart and the only step is doubling.
  - Only rates below 13.2 kHz can double.
  - Short attacks had just been moved into blocks with no room to double, and long ones don't fit twice the bytes.

**Why not kept:** the user heard no difference ("the difference is inaudible"). Shrink-only would have dropped only step 4 and the growth (about 20 of 290 lines). It would have kept the pass, the budget plumbing and the cross-pass interactions. Pricing growth inside the value budget would have added a third measure and an onset-weighted exposure, on top of the budget's existing assumption that dB on different measures are equal.

**Kept:** `play`, `to_bytes` and `attack_error_db` moved from `looplab.py` to `loopbuild.py`, so the build can use them without the lab. `DOC_RATE` now lives in `docram.py` (2026-11-11), the one definition every module imports.

**Rate limits, for reference:**
- The DOC's useful ceiling is its output rate, 894 886 / 34 = 26 320 Hz with all 32 oscillators enabled. Above it, the oscillator skips bytes and aliases.
- `max_rate` = 22 kHz for melodic samples is ours ("Rate above 22 kHz: dropped", above).
- Drums go to 26 kHz (`drum_max_rate`).

## Track allocation (`convert_song`; current rules, 2026-11-07)
NTP has no voice allocation: each MOD track owns its oscillators for the whole song (`determine_osc_per_track`). So `convert_song` assigns every note to a track itself, once, in onset order.

**One note per key, as on the MT-32 (`read_notes`, 2026-11-09).** User report: MI153 has "major polyphony problems … many notes are just not making it", although it's a single organ part, never more than 8 notes at once. Checked first: the MOD holds all 197 notes (no drops or steals, 13 tracks), and a DOC register log in GSSquared (all 32 oscillators about every 17 ms, `gs2client` state blob) shows NTP starting every one of them, row for row as in the MOD.
- **The cause was in reading the MIDI.** MI153 strikes 21 keys again while they're still held (no sustain pedal: the song sends only CC7 and CC10). Every other song together does it 5 times (MI160: 3, MI166: 2). `read_notes` paired note-offs first-in-first-out per key. So a re-struck key became two overlapping notes, and the second lasted until the *later* note-off.
- **What the MT-32 does:** MT-32 patches in single-assign mode (assign modes POLY1 and POLY2) abort a key's sounding poly when the key is struck again (Munt `Part::playPoly` → `abortFirstPoly(key)`). A note-off releases the first sounding poly of its key, and a note-off with none sounding does nothing (`Part::stopNote`).
- **Measured through Munt** (on 1.0 s, re-struck 2.0 s, offs 3.0 s and 4.5 s). Pipe Org 2 (program 13), program 9 and program 104 are all quiet from 3.0 s on, while a single note held to 4.5 s keeps sounding. Pipe Org 2's level between 2 and 3 s is the single note's (−31 dB), not two notes'.
- **Rule now:** one note per channel and key. Striking a sounding key ends the old note at the new onset. The next note-off ends the new note, and a stray note-off is ignored. A key struck twice at the same tick plays once.
- **Effect:**
  - MI153: notes 197 → 195, and Pipe Organ note time 225 → 206 s (19 s of notes that went on sounding after the MT-32 had ended them). The loop plan moved one 16 KB loop from key 88 to Pipe Organ Lo key 50.
  - MI160: 2727 → 2726 notes, steals 415 → 414 (shared 94 → 92).
  - Every other song is identical, in both modes.

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
- **Minimum:** at least 2 tracks (3 in a looping song, whose row 0 also holds the `D00`).
- **Control commands:** speed and tempo go in row 0; the song's end (`Bxx`, `D00`, `F00`) is in "Song ends and loops".

**What the numbers mean:** `trk` and `osc` in the build log are the tracks and oscillators used. Overload remains where demand really exceeds 31: MI160 peaks at 44 oscillators, over budget for 9.6 % of the song, measured with notes ringing to note-off plus release. That's where its steals and 9–11 dropped Claves come from. Loop-only (1-oscillator) organ samples would cut that to 2.3 %; that's a proposal, not built.

### Loop-only sounds (trial `per-song-lo` / `shared-lo`, 2026-11-08; rejected and removed 2026-11-09)
The trial's `loop_only` (sound names, per mode) dropped a sound's attack header, so it needs **1 oscillator** instead of 2. Ninjaforce's converter gives a looped MOD sample 1 oscillator when its loop starts at 0 and its length is a power of two (`modinstrument::get_instrument_type`, `ntpinstrument::get_osc_count`, `ntpconverter_lib.php:49`, `:337`). Such notes go on the 1-oscillator (drum) tracks; the envelope rows follow the contour from the onset. Loops are planned with the attack in place and the attack is removed afterwards, so the loop is byte-identical to the base mode and only the attack differs. The freed attack pages stay free (shared 237 → 233 pages).

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
