# Plan (deferred): quantize MI1 MIDI before NTP conversion

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

## Steps
1. **Tempo tracking.** Estimate the grid's local phase across the song, and turn it into a tempo map written as `Fxx` changes. One step of tempo is about 1% at these tempos, enough to follow the drift. This reuses `tempo_commands` with a time-varying ideal.
2. **Quantize as a separate MIDI step** (`quantize_notes`), writing `music/ntp/q/NNN.mid`, so quantization alone can be auditioned through Munt (`just mt32-wav`) before any IIgs sound is involved.
3. **Snap selectively.** Notes within `snap_tolerance` of a row (say 30% of a row) snap. Outliers keep `EDx`: grace notes, rolled chords, pushes. Note-offs snap too (minimum 1 row). Config in `gs/ntp.toml`: `quantize` (strength), `snap_tolerance`, `rows_per_unit`, `tempo_follow`.
4. **Try `rows_per_unit = 1`** (a row per 16th or triplet 8th) with tempo-following and snapping. That halves pattern RAM (MI160 ≈ 39 KB); speed 6 still leaves 6 `EDx` positions for the outliers.
5. **Measure each setting.** New build-log columns: % snapped, % velocity in-cell, timing error before and after, pattern KB, rows per second. Plus Munt previews of the quantized MIDI.
6. **Tests:** synthetic drifting performances should snap back to their rows, keep the outliers, and recover the tempo map.
7. **Investigate 152 and 158** (unit choice or rubato). They may need per-song overrides, or to stay unquantized.

## Alternatives considered
- **Velocity in the preceding cell** for looped (melodic) tracks: no good. After `C00` the loop keeps running silently, so a `Cxx` before the new note would make the old loop audible again. It only works on one-shot tracks, which `convert_song` already does.
- **Duplicate sample headers with different default volumes:** MOD can't share sample data between slots, and NTP's converter would store copies. There's no DOC RAM for that.
