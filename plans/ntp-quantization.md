# Plan (deferred): pack rows to the beat, then quantize MI1 MIDI before NTP conversion

Status: **not started**; recorded 2026-11-06 to try later. Background measurements: `docs/ninjatrackerplus.md`, "Grid analysis for quantization" and "Fixes".

## Why
- A note with an `EDx` micro-delay can't carry its velocity `Cxx` in the same cell: a MOD cell has one effect. Unless the track is a silent one-shot track, the note starts at the sample's default volume for one row. Rows are 55–154 ms, so that flattens velocity nuance.
- After tempo dithering, 30–1540 notes per song still need `EDx` (about 40% of the total). Snapping most of them to rows would put their velocity back in-cell.
- Coarser rows would also shrink patterns. They're uncompressed: 256 B per track per 64-row pattern; MI160 is ≈ 78 KB now.

## What the data says (don't redo)
- **The detected unit is the real subdivision.** Finer grids (unit/2, /3, /4) only catch jitter: no systematic 32nds, and no triplets inside duple songs.
- **Triplet-feel songs** (159, 161–165, 167, 168) have a triplet-8th unit, so a beat is 3 units. Duple songs have a beat of 2 or 4 units. So 32 or 24 rows per beat buys nothing musically, and costs about 4× the pattern RAM and speed 1 (a row every tick, which means more player CPU).
- **The performance drifts:** the local phase wanders 22–85 ms across a song. A local fit cuts the median deviation for some songs (MI160 18.9 → 7.9 ms, MI163 13.2 → 4.2), so a fixed grid would snap notes the wrong way.
- **152 and 158** barely sit on any grid, even locally. Either the unit needs rechecking or they're played with rubato.
- Tempo-rounding drift is already fixed (`tempo_commands`).

## Recommended row packing: one row per unit, more ticks per row
Make a row the song's detected unit:
- **duple songs:** a 16th, so **4 rows per beat** and a 4/4 bar = 16 rows;
- **triple-feel songs:** a triplet 8th, so **3 rows per beat** and a 12/8 bar = 12 rows.

Get micro-timing from ticks instead of rows: **speed 6** (6 `EDx` positions per row) instead of today's 2 rows × speed 3.

- **Same timing resolution, half the rows.** Row = unit and speed 6 is exactly the tick grid of row = unit/2 and speed 3: same MOD tempo, same tick rate (tempo × 0.4 Hz, e.g. 35.6 Hz in MI160), same `EDx` precision. Unit/2 rows hold almost nothing (the grid analysis shows no 32nds), so they're mostly empty storage.
- **Gains:**
  - pattern RAM halves (MI160 ≈ 78 KB → ≈ 39 KB);
  - the player parses half as many rows (per-row work: note and instrument setup), while its per-tick work is unchanged.
- **This doesn't need quantization.** It can be done first, on the current unquantized pipeline, with the same timing error.
- **Speed headroom:** tempo ≤ 255 allows speed up to 2.5 × 255 × row_s. That's 17 at MI160's 169 ms unit and about 6 at MI163's 110 ms unit. So speed 6 fits every song; pick the largest speed that fits per song if finer `EDx` helps.
- **Cost: envelope resolution halves.** Today there's one `Cxx` per 32nd. Recover it with **volume slides (`Axy`)**, which act on every tick after the first: one `Axy` per row interpolates the contour linearly across that row's 5 slide ticks. That's smoother than today's per-row steps, at no RAM cost. `Axy` and `Cxx` share the effect column:
  - use `Cxx` at note-on and to resync every few rows;
  - use `Axy` in between;
  - releases become `A0y` ramps.
- **Bar-aligned patterns:**
  - duple: 64 rows = 4 bars of 4/4;
  - triple: 64 isn't a multiple of 12, so end each pattern at 60 rows (5 bars) with a pattern break (`D00`). NTP stores 64 rows regardless, so this wastes 4 rows (6%). The payoff is that a repeated section lands in identical patterns.
- **Pattern de-duplication:** the MOD order list can reuse a pattern, so emit each distinct pattern once. With performed timing, repeats are rarely identical. After quantization (below) they often should be, so measure the saving per song.
- **Rejected:**
  - **32 or 24 rows per beat:** ~4× today's pattern RAM, it forces speed 1 (a row every tick: the most player CPU), and the music has nothing to put in the extra rows.
  - **An LCM grid (12 or 24 per beat)** for mixed 16ths and triplets: no song mixes them, and the per-song unit already adapts.

## Steps
0. **Row packing (independent of quantization):**
   - `rows_per_unit = 1` and `speed = 6` (or the largest that fits);
   - `Axy` envelope interpolation with periodic `Cxx` resync;
   - bar-aligned patterns (60 rows for triple songs);
   - pattern de-duplication.

   Verify it against the current build: same timing error, pattern KB halved, listening.
1. **Tempo tracking.** Estimate the grid's local phase across the song, and turn it into a tempo map written as `Fxx` changes. One step of tempo is about 1% at these tempos, enough to follow the drift. This reuses `tempo_commands` with a time-varying ideal.
2. **Quantize as a separate MIDI step** (`quantize_notes`), writing `build/ntp/q/NNN.mid`, so quantization alone can be auditioned through Munt (`just mt32-wav`) before any IIgs sound is involved.
3. **Snap selectively.** Notes within `snap_tolerance` of a row (say 30% of a row) snap. Outliers keep `EDx`: grace notes, rolled chords, pushes. Note-offs snap too (minimum 1 row). Config in `config/ntp.toml`: `quantize` (strength), `snap_tolerance`, `rows_per_unit`, `tempo_follow`.
4. **With rows = units (step 0), snapping is to the musical grid itself.** Outliers still have 6 `EDx` positions. Re-measure pattern de-duplication, since quantized repeats should now match.
5. **Measure each setting.** New build-log columns: % snapped, % velocity in-cell, timing error before and after, pattern KB (before and after de-duplication), rows per second, ticks per second. Plus Munt previews of the quantized MIDI.
6. **Tests:**
   - synthetic drifting performances should snap back to their rows, keep the outliers, and recover the tempo map;
   - `Axy` interpolation should reach each row's target within one volume step;
   - de-duplicated patterns should round-trip through the order list.
7. **Investigate 152 and 158** (unit choice or rubato). They may need per-song overrides, or to stay unquantized.

## Alternatives considered
- **Velocity in the preceding cell** for looped (melodic) tracks: no good. After `C00` the loop keeps running silently, so a `Cxx` before the new note would make the old loop audible again. It only works on one-shot tracks, which `convert_song` already does.
- **Duplicate sample headers with different default volumes:** MOD can't share sample data between slots, and NTP's converter would store copies. There's no DOC RAM for that.
