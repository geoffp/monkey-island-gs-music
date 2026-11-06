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
