<!-- Research by a side agent (2026-11-17, read-only), checked by the main session against
     vendor/ntp/src/ninjatrackerplus.s where it matters for what we emit (Axy, EDx). Line numbers
     refer to that file and to the repo as of commit b4a5b93. Recommendations built on it:
     plans/protracker-effects.md. -->
# ProTracker effects vs NinjaTrackerPlus (NTP) vs our MOD writer

Scope: every main effect 0xy-Fxy and extended E0x-EFx. Read-only research, 2026-11-17, branch docsim2-vibrato.

Sources used:
- **[AES]** https://www.aes.id.au/modformat.html (MOD format notes). Read through a fetch summary, so the wording is paraphrased; the semantics match the summary.
- **[PT2]** https://raw.githubusercontent.com/8bitbubsy/pt2-clone/master/src/pt2_replayer.c (ProTracker clone replayer). Read through a fetch summary, no line numbers. Used for effect-memory behaviour.
- **[NTP]** `vendor/ntp/src/ninjatrackerplus.s` (line numbers given). Tables at 2431 (setup main), 2449 (setup E), 3104 (handle main), 3122 (handle E). Dispatch: `tracker_interrupt` 1542, `parse_tracks` 1573, `handle_effects` 3139, `setup_effect` 2467.
- **[CONV]** `vendor/ntp/src/ntpconverter_lib.php`: `extrad_mod_note` 1331-1341 (effect = bits 8-11 of word 2, value = bits 0-7), `add_note_to_ntp` 1368-1372 (rejects effect > 15 and value > 255; no other effect check), `analyze_pattern` 1406-1410 (effect 9 with value != 0 forces the instrument to streamed).
- **[FF]** `vendor/ntp/src/file_format.txt`: pattern cells are "effect (0-15)" and "effect parameter (0-255)". It gives no effect semantics at all.
- **[DOC]** `docs/es5503.md`, `docs/ninjatrackerplus.md` (repo).

## How NTP runs effects (needed for every row below)

- **Tick 0** is the row fetch (`parse_tracks`, 1573). Each track's `setup_effect` (2467) runs in track order, so a higher track's effect wins when two write the same variable. It sets `curr_effect`, calls the setup routine from the main table, then plays the cell's note (`check_play_note`, 2523).
- **Ticks 1 .. speed-1** run `handle_effects` (3139) from `tracker_interrupt`, for every track, through the handle tables. Walking the timer (1542-1571) confirms `speed` ticks per row with tick 0 as the parse, so this matches ProTracker's tick model.
- Setup-only effects (no handle entry) act on tick 0 only.
- A pattern delay (EEx) row boundary calls `handle_effects` instead of the parse, so effects keep running during the delay ([NTP] 1573-1600, `parse_tracks_2`).
- `pattern_jump` (2 = B, 4 = D, 6 = stop) is a single variable shared by Bxx, Dxx and F00, and `pattern_jump_param` is shared by Bxx and Dxx. A row can therefore hold only one of them; the last track wins ([NTP] 2355-2430; the repo's own comment in `ntpsong.py` ~line 944 says the same).
- **Effect memory** in NTP exists only where a setup routine keeps the old value when the parameter is zero: 3xy speed, 4xy x and y separately, 6xy (via 4), 7xy x and y separately, 9xy offset. Everything else reads its parameter fresh each row.

## Table

Key: PT = ProTracker as in [AES]/[PT2]. NTP status: OK = same as PT for our purposes; DIFF = differs (details in notes); NOOP = accepted, does nothing.
"Ours" = effect codes emitted by `src/migs/ntpsong.py` (constants `FX_*`) and `src/migs/modfile.py` (`FX_SET_VOLUME`).

| Code | Name | PT (tick 0 / later ticks; memory) | NTP (where) | NTP status | Emitted by us |
|---|---|---|---|---|---|
| 0xy | Arpeggio | tick 0 base note; ticks 1, 2, 0 mod 3 add x, y, 0 semitones; 000 = off, no memory [AES][PT2] | setup 2563 sets `arpeggio_ptr`; handle 3157 cycles it; base note is the row's note or the last one | DIFF: every arpeggio step restarts the sample (note 2) | no |
| 1xy | Slide up | ticks 1..; period -= xy; 000 = nothing, no memory [AES][PT2] | setup 2601 sets `slide_delta` = 2*param; handle_slide_up 3196, clamps at `max_finetune_ptr` | OK in pitch; units differ (note 3) | no |
| 2xy | Slide down | as 1xy, period += xy [AES] | setup 2601; handle_slide_down 3217, clamps at 0 | OK; units differ | no |
| 3xy | Slide to note (tone portamento) | tick 0: note sets target, note not retriggered; ticks 1..: slide speed xy; 000 keeps the old speed and slide; speed is remembered [AES][PT2] | setup_slide_to_note 2617: param 0 keeps `slide_delta`; note sets target and clears `note` (not played); handle_slide_to_note 3238 | DIFF: speed memory shared with 1xy/2xy (note 4) | no |
| 4xy | Vibrato | ticks 1..; x = speed (cycle = 64/x ticks), y = depth; x and y each keep their old value when 0; waveform from E4x [AES][PT2] | setup_vibrato 2645: x*2 rate, depth = sine block y, memory per nibble; handle_vibrato 3302 | OK in rate; depth scale unverified; resets position on every 4xy row (note 5) | no |
| 5xy | Tone portamento + volume slide | continues the last 3xy (no new target, note not played) and applies Axy [AES][PT2] | setup 2715 is `setup_volslide` only; handle_effect_5 3333 = handle_slide_to_note + handle_volslide | DIFF: a note on a 5xy row is played (retriggered), PT doesn't (note 6) | no |
| 6xy | Vibrato + volume slide | continues the last 4xy, applies Axy; the note plays as normal [AES] | setup = setup_volslide 2715; handle_effect_6 3339 = handle_vibrato + handle_volslide | OK | no |
| 7xy | Tremolo | ticks 1..; x = speed, y = depth on volume; nibble memory as 4xy [AES][PT2] | setup_tremolo 2671; handle_tremolo 3345 | DIFF: no clamp on the volume index (note 7) | no |
| 8xy | Set panning (IIGS, not PT) | PT: "unused by the vast majority of trackers" [AES]. PT ignores it | setup_gs_panning 2698: x < 8 sets the track's output channel (x<<4); x >= 8 is stored, not used | NTP extension; only channel 0/1 matter on a stereo card (note 8) | no |
| 9xy | Sample offset | tick 0: start at offset xy*256 (units disputed, note 9); 000 reuses the last offset [AES][PT2] | setup_sample_offset 2823; streamed instruments only, else no-op; memory via `sample_offset_backup` | OK with streamed samples; non-streamed = NOOP (converter forces streaming, [CONV] 1406-1410) | no |
| Axy | Volume slide | ticks 1..: volume += x or -= y; if x and y both set, y ignored; 00 = nothing, no memory [AES][PT2] | setup_volslide 2715 (param >= $10 uses x only); handle_volslide 3379, clamps 0..64 | OK (note 10) | **yes: A0y only** (release slides, `plan_release`) |
| Bxy | Position jump | tick 0: stop after this row, go to order xy (legal 0..127) [AES] | setup_positionjump 2759: `pattern_jump` = 2, param & $7F; jump at the next row fetch | OK, but shares `pattern_jump` with D and F00 (note 11) | **yes** (loop jump, `ntpsong.py` ~966) |
| Cxy | Set volume | tick 0: volume xy, legal 0..64 [AES] | setup_setvolume 2741, clamps at 64 | OK; the DOC volume curve is non-linear (note 12) | **yes** (`modfile.py` remaps the parameter through the measured curve if `volume_map`) |
| Dxy | Pattern break | tick 0: stop after this row, next pattern at row x*10+y (BCD), legal 0..63 [AES] | setup_patternbreak 2771; handle_pattern_break 2368 via `pattern_break_ptrs` (BCD, 2-digit) | OK for legal values; shares `pattern_jump` (note 11) | **yes** (D00 only, `ntpsong.py` ~959, ~971, ~980) |
| E0x | Set filter | tick 0: filter on (x=0) / off (x=1) [AES] | setup_e_effect_jump_tbl[0] = `setup_effect_jump_rts` (2450, "Not supported") | NOOP | no |
| E1x | Fine slide up | tick 0 only: period -= x, once [AES] | fineslide_up 2876 (tick 0 only); no memory | OK (units as 1xy) | no |
| E2x | Fine slide down | tick 0 only: period += x [AES] | fineslide_down 2876 | OK | no |
| E3x | Glissando | tick 0: 1 = slide to note in semitones, 0 = smooth [AES] | set_glissando 2921 stores the raw param per track; handle_slide_to_note uses it | OK (any non-zero = on, PT only checks 0/1) | no |
| E4x | Vibrato waveform | tick 0: waveform for later 4xy: 0 sine, 1 ramp, 2 square, 3 random; bit 2 = no retrigger on a new note [AES] | set_vibrato_waveform 2928: 0 sine, 1 ramp, 2 and 3 square (`waveforms` 3996, 2048, 4096, 4096) | DIFF: waveform 3 is square, not random; bit 2 freezes the vibrato at the wrap instead of suppressing a retrigger (note 13) | no |
| E5x | Set finetune | tick 0: the current sample's finetune = signed nibble x [AES] | set_sample_finetune 2949 writes the instrument's finetune (global for that instrument) | OK; nibble-to-table mapping UNVERIFIED | no |
| E6x | Pattern loop | tick 0: E60 records the row, E6x jumps back x times; no nesting [AES] | set_loop_pattern 2969 with one global loop state (not per track) | DIFF: loop state is shared by all tracks (note 14) | no |
| E7x | Tremolo waveform | as E4x, for 7xy [AES] | set_tremolo_waveform 3003, same as E4x | DIFF as E4x, for tremolo | no |
| E8x | (unused) | "Unused" [AES] | table entry "unused" = `setup_effect_jump_rts` (2458) | NOOP | no |
| E9x | Retrigger | every x ticks: retrigger the sample; x = 0 no retrigger; first retrigger "on the first tick" then x ticks after (wording unclear, UNVERIFIED) [AES] | setup_retrigger 3024; handle_retrigger 3414: count+1 >= steps retriggers | DIFF: x = 0 retriggers every tick (note 15) | no |
| EAx | Fine volume up | tick 0: volume += x, max 64 [AES] | volslide_up 3033 | OK | no |
| EBx | Fine volume down | tick 0: volume -= x, min 0 [AES] | volslide_down 3033 (falls into volslide_up_1) | OK | no |
| ECx | Note cut | after x ticks of the row, volume = 0; x = 0 means no sound at all [AES] | setup_note_cut 3067; handle_note_cut 3436 (decrements, cuts at tick x) | DIFF: x = 0 is no cut, the note plays (note 16) | no |
| EDx | Note delay | do not start the cell for the first x ticks, then start it (instrument and volume too) [AES] | setup_note_delay 3074 (note held, `note_delay_count` = x); handle_note_delay 3456 plays the note at tick x | DIFF: the cell's instrument and default volume apply at tick 0; x = 0 loses the note (note 17) | **yes** (`FX_EXTENDED` with `NOTE_DELAY | d`, d = 1..speed-1 only, `ntpsong.py` 1144) |
| EEx | Pattern delay | after this row, x rows of delay; effects keep running [AES] | setup_delay_pattern 3090; parse_tracks_2 (1573 area) | OK | no |
| EFx | Invert loop | speed x (funk table, 0 = off); negates the loop bytes [AES] | setup_invertloop 3097; handle_invertloop 3472, `invertloop_add_tbl` 4126 = 00 05 06 07 08 0A 0B 0D 10 13 16 1A 20 2B 40 80 | OK (table matches the PT funk table from memory, UNVERIFIED); modifies sample RAM | no |
| Fxy | Set speed / tempo | tick 0: xy <= 32 (AES) ticks per row, else BPM; 00 = stop (AES: "technically stops", "in practice" 1). Highest track wins [AES] | setup_setspeed 2797: 0 stops (`pattern_jump` = 6); < $20 ticks; >= $20 BPM through `bpm_to_freq` | DIFF at exactly 32 (note 18); F00 stops NTP | **yes**: F00 song end, speed and tempo on row 0, tempo changes (`tempo_commands`) |

## Notes

1. **Tick model** (both): effects after tick 0 run on ticks 1..speed-1. NTP: [NTP] 1542-1600, 3139. PT: [PT2] `tickReplayer`/`checkEffects` run for ticks > 0, and `checkMoreEffects` handles 9, B, C, D, E, F on tick 0.
2. **0xy in NTP (note 2).** `handle_arpeggio_4` (3157 area) sets `note` and jumps to `check_play_note`. That sets `what_to_do` bit 7 ("start"), and `talk_to_doc` (1638) then stops and restarts the oscillators. So each arpeggio step retriggers the attack. PT changes pitch only. Code reading only, not run. This also matters on 2-oscillator swap-mode instruments (`docs/es5503.md` "Swap-mode anomaly", TN #11). Our writer never emits 0xy.
3. **1xy/2xy units.** NTP adds `param*2` to a pointer into `freq_finetune` (3805-), clamped at `max_finetune_ptr` = 3574. PT changes the period. The NTP pitch-per-pointer step was not derived, so the size of a 1xy step versus PT is UNVERIFIED.
4. **3xy memory is shared (NTP).** `slide_delta` is written by 1xy, 2xy and 3xy alike (2601, 2617). A 3000 after a 1xx uses the 1xx's speed. PT keeps a separate tone-portamento speed ([PT2] `tonePortamento`/`n_toneportspeed`). Ours never emits 3xy.
5. **4xy (NTP).** Depth y selects block y of `sine_tbl` (amplitude ~y, values 2*round(y*sin), 3-ish blocks of 128: `make_tables.php` 326-343). Its scale against PT's y/16 semitones is UNVERIFIED. `setup_vibrato` resets `vibrato_pos` on every 4xy row. PT resets on a note (the E4 bit-2 wording in [AES] implies that), so NTP's reset differs when a 4xy comes without a note. Ours never emits 4xy.
6. **5xy (NTP).** Setup 5 is `setup_volslide` (2715), not `setup_slide_to_note`, so the note is not cleared and `check_play_note` plays it. PT's 5xy does not play the note ([AES]: "the period-length ... is not played"). Ours never emits 5xy.
7. **7xy clamp (NTP).** `handle_tremolo` (3345) adds the sine value to `curr_volume` with no clamp, then indexes `volume_lookup` (3798, 66 words: indices 0..65). For `curr_volume + depth > 65` it reads past the table (into `freq_finetune`); for `curr_volume < depth` the index wraps. Reading only: this is a suspected bug, not run. Ours never emits 7xy.
8. **8xy (NTP).** Only x < 8 does anything: it sets `track_channels` = x<<4 (2698-2708). The converter gives each track channel 0 or 1 by default (`$doc_stereo_defaults` 1 0 0 1, per `docs/es5503.md` line 59-62), and the player keeps only CA0 (bit 4), so on a real stereo card 8xy switches left and right by parity, and a mono IIGS sums both. PT ignores 8xy, so a MOD with 8xy plays differently in PT and NTP. Ours never emits 8xy.
9. **9xy (units disputed).** [AES] says the offset is in words; the NTP code comment (`setup_sample_offset`, ~2823-2840) says "the doc says words but it's really only bytes" and uses xx*256 bytes. PT's byte or word unit was not verified from source. Memory: [AES] and [PT2] say yes, and NTP keeps `sample_offset_backup`. An offset past the end is clamped to the end (NTP edge case, ~2857). Streamed-only; the converter forces streaming for any instrument used with 9xy != 0 ([CONV] 1406-1410, `add_note_to_ntp` path ~1107-1120). `docs/ninjatrackerplus.md` says the same.
10. **Axy (both).** Both nonzero: x wins (NTP 2715-2735 uses x whenever param >= $10; [AES] "y is ignored"). Clamp 0..64 (NTP `handle_volslide` 3379-3400). Volume arithmetic is in MOD units, then `volume_lookup` (x4, linear). The DOC curve is applied by our pre-mapping (`doc_volume = "measured"`), so the planner has to count slides in written levels. `plan_release` in `ntpsong.py` (~485-512) does this for A0y.
11. **Shared `pattern_jump` (NTP).** B, D and F00 write the same `pattern_jump` (and B and D share `pattern_jump_param`). PT handles B and D on one row. Our writer avoids this by giving each row at most one of them: the loop jump is a Bxx on the pass's last row, the intro's end is a D00 on its own row, and F00 sits on the song's last row (`ntpsong.py` ~944-980).
12. **Cxy and the DOC curve.** NTP's `volume_lookup` is linear (x4, clamp 255; [NTP] 3798-3805). Real DOC volume is non-linear and silent below 17, so MOD volumes 4 and under are silent on hardware (`docs/es5503.md` 151). Our writer maps Cxy through the measured curve when `doc_volume = "measured"` (`modfile.py` line ~60, `volume_map`).
13. **E4x/E7x (NTP).** The sine/ramp/square waveform table is `waveforms` (4036): 0, 2048, 4096, 4096, so 3 is square. [AES] says "random". The NTP comment says "random=square in protracker!" (4036), which is a claim about PT. Not verified against PT. Bit 2 (`vibrato_retrig`): when the position would wrap past 127 and retrig is set, `handle_vibrato` (3302-3330) skips the store, so the position stays frozen. PT's bit 2 suppresses the waveform restart on a new note. So NTP's effect is a freeze, not a no-retrigger. Ours never emits E4 or E7.
14. **E6x (NTP).** `loop_position`, `loop_position_active`, `loop_repeat_count` (2969-3000) are single variables, not indexed by track, and reset at each pattern end. PT loops are per channel ([AES] "loops do not support nesting"). A song with E6x on two tracks at once behaves differently. Ours never emits E6x.
15. **E9x (NTP).** `handle_retrigger` (3414): steps = x, retrigger when count+1 >= steps. For x = 0 the test is always true, so NTP retriggers on every tick, where PT does nothing. For x = 1 it is every tick too; for x = 2, ticks 2, 4, ... Retrigger restarts the instrument from its start (bit 7). Ours never emits E9.
16. **ECx (NTP).** `setup_note_cut` stores x; `handle_note_cut` (3436) counts down and sets volume 0 at tick x (x >= 1). For x = 0 `beq handle_note_cut_1` means no cut at all. [AES] says x = 0 silences the sample. Ours never emits EC.
17. **EDx (NTP).** `setup_note_delay` (3074) keeps the note for later (`note_delay_note`, `note_delay_count` = x) and sets `note` = 0. But the instrument and default volume are applied at tick 0 (`setup_effect` 2467-2510, `what_to_do` bit 0), so the previous note's oscillators get the new volume for x ticks. The repo's own finding is in `docs/ninjatrackerplus.md` ~line 365-372 ("User report: SenseiPlay has abrupt note endings"). For x = 0, `handle_note_delay` decrements a 16-bit count (the tracker runs in 16-bit mode, `rep #$30`, 1561) to $FFFF and never plays. Reading only, not run. Ours emits ED1..ED(speed-1) only (`ntpsong.py` ~1122-1144: the branch is taken only when `d != 0`), and the handle needs x < speed.
18. **Fxx threshold and stop (both).** [AES] says "if z <= 32 ticks, otherwise BPM". NTP treats < $20 (31) as ticks and >= $20 (32) as BPM (2797-2820). So NTP reads F20 as 32 BPM; a reader following [AES] reads 32 ticks. PT's real rule at exactly 32 was not verified from source. Our BPM values are >= 32 (`fit_pass` range 32..255 in `ntpsong.py`), so a 32-BPM command, if one came up, would be ambiguous. Stop: F00 stops in NTP (`stop_playing`, 2394). [AES] says "technically stops, in practice treated as 1". Desktop players ignore F00, which is why the writer adds D00 on the row after it (`ntpsong.py` ~953-960).
19. **Multiple F or B/D on one row.** NTP: the last track's setup wins (track order, `parse_tracks_loop` 1586-1620). [AES] for F: "the ones on higher-numbered channels take precedence", which agrees. For B and D, [AES] expects both to act; NTP cannot (note 11).

## Limits on the IIGS DOC that matter for effects

- **Volume.** NTP applies `volume_lookup` (x4) to the track's MOD volume. The real DOC volume is non-linear and silent below 17 (`docs/es5503.md` 130-151, `config/doc-volume.toml`). Any slide, tremolo or C that goes below 4 is silent on hardware. Our writer's planner works on written levels to hide this (`plan_release`, `doc_volume = "measured"`).
- **Oscillators.** Each track uses 1 or 2 oscillators (4 with channel doubling). The interrupt oscillator is #31, at volume 0, so 31 tracks max; the uppermost enabled oscillator counts three times on real silicon (`docs/es5503.md` 70-72). This applies to any effect that adds oscillators (none of ours does).
- **Retrigger cost.** Anything that sets `what_to_do` bit 7 (arpeggio, E9x, EDx at its tick, delayed notes, a new note) stops and restarts oscillators (`talk_to_doc` 1629-1680). 2-oscillator swap-mode instruments are the case with a known anomaly (TN #11, `docs/es5503.md` 73-76).
- **Panning.** Only CA0 is kept (`docs/es5503.md` 59-62). 8xy is left and right on a stereo card and a sum on a mono machine.
- **Streaming and DOC RAM.** 9xy and EFx need streamed or DOC-resident data. EFx writes into sample RAM (`handle_invertloop_doc` 3507-3530 for DOC-resident loops; streamed loops use the stream buffer), so an inverted loop changes the sample for everything that shares it.
- **Tick rate.** The timer runs at the song's tick rate (50 Hz at the default BPM). Per-tick slides and vibrato have that resolution, not the DOC's.

## What our writer emits

From `grep FX_ src/migs/ntpsong.py src/migs/modfile.py`:

| Constant | Code | Where used | Form |
|---|---|---|---|
| `FX_SLIDE` | A | `plan_release` (ntpsong.py ~485-512) | A0y only (volume down by y, 1..15, per tick) |
| `FX_JUMP` | B | loop jump at `end_row - 1` (~966) | Bxx to the pass's first pattern |
| `FX_VOLUME` | C | notes' velocity, release rows, rest (~692, ~718, ~1130) | Cxx, 0..64; `modfile.py` line ~60 maps it through `volume_map` |
| `FX_BREAK` | D | intro end, loop start, song end, lead-in (~959, ~971, ~980) | D00 only |
| `FX_EXTENDED` | E | `NOTE_DELAY` = 0xD0 (~1144) | EDx, x = 1..speed-1 |
| `FX_SPEED` | F | speed and tempo on row 0 (~923-927), tempo changes (~923), song end (~953) | F<32 = ticks, F>=32 = BPM; F00 = stop |
| `FX_SET_VOLUME` (modfile.py) | C | the same as FX_VOLUME | the writer's own name for C |

Not emitted: 0 (arpeggio), 1, 2, 3, 4, 5, 6, 7, 8, 9, and every E except D. Note that only D00 is emitted for D, and F00 is only used for the song end.

## Repo docs to correct or extend

- `docs/ninjatrackerplus.md` line 14 says "30 of 31 Protracker effects (not E0 filter)". By this reading, all of 0-F are implemented (9 is streamed-only), and the E-table entries not implemented are E0 (filter) and E8 (unused in PT too). Consider stating it per code.
- The E4/E7 bit 2 meaning and E9 x = 0, EC0 and ED0 behaviour are not in the docs.
- The arpeggio retrigger (note 2) and the 5xy note play (note 6) are not in the docs.

## Unverified

- PT's random waveform (E4x/E7x 3), PT's 9xy units (bytes or words), PT's Fxx at exactly 32, PT's E9x first-tick wording: the sources above are summaries, so these need the PT source or a run.
- NTP's 1xy/2xy pitch step, 4xy depth scale, E5x nibble mapping: read off the tables, not measured.
- Every "reading only, not run" point above (arpeggio restart, EDx x = 0, 7xy index overrun, E9x x = 0, EC0): these come from reading the 65C816 code and would be settled by a GSSquared run or a DOC register trace.
