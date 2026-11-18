<!-- Advisory research by a side agent (2026-11-17, read-only, from docs/ntp-effects.md), kept so
     we can come back to it (the user's request). Status since it was written (main session):
     - #1 release slides: trial slides-per-song in all three games; MI2 177/208/160 heard as
       "sounds great" (docs/ninjatrackerplus.md "Release slides"). Its drops note is off: M2177's
       drops went 8 -> 12 in all (Vibe 2, Acou Bass2), MI2's steals fell 719 -> 693, drops 190 -> 197;
       MI1's steals rose 317 -> 358 (MI160 302 -> 338, same mean stolen level 12).
     - #2 pitch bend: the extraction side is done (commit b4a5b93: bends in the MT-32 and GM MIDI
       and so in the Munt references, docs/imuse-format.md "Pitch bend"); the MODs don't play them yet.
       The bender range is 16 semitones (ScummVM's iMUSE MT-32 driver), not the MT-32 default.
     - Section 6, M2160: confirmed by measurement (Soundtrack, preset 36, falls 10 dB in 0.44-1.0 s
       and 40 dB in 3.9-5.1 s; release_max 0.3 s cuts it).
     - Section 9 (CPU per tick at the higher tick rates): still unmeasured. -->
# Which ProTracker effects could help our NTP builds (advisory, read only, 2026-11-17)

Branch docsim2-vibrato. Nothing was built or run. Everything marked "reading" comes from reading
`vendor/ntp/src/ninjatrackerplus.s` ("s." below) or the repo; nothing here says how anything sounds.
Listening verdicts quoted are the user's, from `docs/ninjatrackerplus.md`.

## 0. Spot-check of the inventory, and facts it lacked

Confirmed in s.: arpeggio restarts the sample every step (`handle_arpeggio_4` jumps to `check_play_note`,
which ORs `%10000010` into `what_to_do`, s. 2551-2553); `setup_vibrato` resets `vibrato_pos` on every 4xy
row, even 400 (s. 2645-2670); `handle_tremolo` has no clamp (s. 3345-3370); `handle_note_cut` never cuts
for x=0 and `handle_retrigger` retriggers every tick for x=0 (s. 3414-3450); `ED0` decrements a 16-bit
count to $FFFF and never plays (s. 3456-3470, 3074-3084); `setup_volslide` takes `0y` as "down y" and any
`x` nibble as "up x, y ignored" (s. 2715-2735); B, D, F00 share `pattern_jump` (s. 2759-2780).

Things the inventory left open or got incomplete, now settled by reading:

1. **1xy/2xy/E1x/E2x step in whole periods.** `freq_finetune` (s. 3806) has one entry per integer period,
   from about 1815 down to 28 (38 at the low end is period ~1815; `max_finetune_ptr` = 3574 = 2 x 1787), and
   `slide_delta` = 2 x param bytes = param entries (s. 2601-2606). So a slide step is exactly PT's: x periods
   per tick. A period unit is about 62 cents at period 28, 16 cents at 110, 8 cents at 214, 4 cents at 428.
2. **4xy depth is in whole periods too.** `make_tables.php` 326-343: block y holds `2*round(y*sin)` bytes,
   so amplitude is +-y periods, and at small y only a few levels (y=1: -1, 0, +1). Cycle = 64/x ticks
   (`vibrato_rate` = 2x, 128 bytes of table).
3. **Effects last one row.** `setup_effect` writes `curr_effect` for every cell, empty ones included (s. 2467,
   2517-2522). A vibrato, slide or tremolo must be re-stated in every row it should run in. The only
   continuation that does not reset state is **6xy** for vibrato (`setup` = `setup_volslide`, no
   `vibrato_pos` reset) and 5xy for 3xy. A plain 400 every row would restart the sine every row.
4. **A volume slide's end level persists** (`curr_volume`, s. 3379-3400), tick 0 of a slide row plays the old
   level, ticks 1..speed-1 move by y each, and a 2-oscillator track writes both oscillators' volume and
   frequency each time (`talk_to_doc_2osc`, s. 1821-1850). Volume or frequency changes cost about 4 DOC
   register writes per track per tick (reading; cycle count not measured).
5. **Cells are not compressed.** The user's premise that NTP compresses cells is not supported:
   `vendor/ntp/src/file_format.txt` says patterns are `64 rows x tracks x 4 bytes` and a grep of
   `ntpconverter_lib.php` for compress/pack/deflate finds nothing. `docs/ninjatrackerplus.md` (~line 842) says
   the same. So **no effect changes the `.NTP` size**. Size moves only through rows x tracks. (Deflate in the
   distribution zip/ShrinkIt is the only place cell contents matter.)
6. **One effect per cell is the binding constraint.** A note cell is `note + inst + (Cxx | EDx | ...)`;
   a sustain row is `Cxx | (nothing)`. Anything that wants a sustain row's column (vibrato, slide) competes
   with the envelope `Cxx`. Free cells are plentiful (57 % empty, docs line 848) but the *rows that need a
   level change* are exactly the ones that are not free.

CPU: the tracker interrupt runs `handle_effects` (a `jsr (table,x)` per track) plus `talk_to_doc` (scans all
tracks) every tick (s. 1542-1571, 3139-3151). Idle cost is per tick, so **raising the tick rate is the real
CPU lever, not any one effect.** The slides trial already raised tempo, e.g. M2100 67 -> 131 and M2103
113 (build/monkey2/ntp/slides-per-song.log, line 3298): about 27 Hz to about 50 Hz ticks. Nobody has
measured the 65816 load at that rate with 20-30 tracks. That measurement belongs ahead of any trial that
adds more ticks (see section 9).

## 1. Ranking by expected value

| # | Effect | Problem | Gain | Cost | Verdict |
|---|---|---|---|---|---|
| 1 | A0y / Ax0 (finish the trial; extend to sustain rows) | M2177, staccato, env-rise lag, pumping | measurable, in flight | zero size; ticks | do |
| 2 | 1xy / 2xy / E1x / E2x / 3xy (pitch bend) | MI2 16,193 and Atlantis 7,284 bends are dropped | largest unmeasured gap | extraction work + conflicts with Cxx | measure first |
| 3 | 6xy + 4xy (vibrato in place of whole-cycle loops) | DOC pages (sets that do not fit) | tens of pages per vibrato sound | high effort; risk of a different sound | trial after #1 |
| 4 | ECx (cut inside a row) | staccato, note length shorter than a row | modest | zero size; needs default velocity | small add-on to #1 |
| 5 | Fxx per row (non-uniform rows) | delayed notes at default volume | speculative | rows/size unknown | note only |
| 6 | E9x | rolls and repeats within a row | small | few notes | low |
| 7 | E5x / E1x per note | tuning at the top keys | small | column conflict | low |
| - | 0xy, 7xy, 9xy, 8xy, E4x/E7x, E6x, EFx, EEx, 3xy for glides, E0x/E8x | | none | | no |

None of the open problems M2160 (release), M2208, Swell 1, FOA001 Triangle, oscillator shortage, or the
neighbour effect is solved by an effect. Section 6 says what each one needs instead.

## 2. Recommendation 1: volume slides (A0y / Ax0), finish and extend

**Effect and problem.** The trial `slides-per-song` (commit 6a3ea21, `ntpsong.py` 483-512 `plan_release`,
730-775 `write_release_slides`, `config/games/monkey2/ntp.toml` 166-169, `tick_max_ms` 25) writes each
release row as a `Cxx` or an `A0y`, whichever lands nearer the MT-32 release tick by tick. This targets
M2177's ReggaeBass: rows of 141.5 ms, one row per note, so the release was one flat step at the row mean
(docs 765-768). Awaiting the user's listening; I cannot tell whether it fixes what was heard.

**Mechanism checked.** The A row holds the previous level for tick 0, then falls y per tick. The planner
works in written volume with the measured curve (`docvolume.written_levels`), so non-linearity is handled.

**Cost seen in the build logs** (not in the commit message): M2177 drops rose 3 -> 5 for Vibe 2 and 3 -> 5
for Acou Bass2 (`build/monkey2/ntp/zones-per-song.log` line 3303 against `slides-per-song.log` 3303);
halts moved (M2125 94 -> 120 halts, M2119 29 -> 44); the M2177 build took 544 s against 352 s
(logged timings; the machine may differ). `delay_snap_ms` counts also differ because ticks are finer.
Worth explaining before a verdict: slide releases that fall in the last row may keep the track
busy one tick-row longer than the C00-plus-rest convention, or `tick_max_ms` changes the busy rows. Nobody
has checked.

**Extension A: slide on sustain rows (the same planner, more rows).** The `env_max_rise_db` workaround
(docs 295: rises capped at 1.5 dB a row because a DOC volume step plays instantly; French Horn and Strings
lag fast rises by up to 0.65 dB mean, 2.8 dB p95) and "Pumping beats" (MI168, 111 ms steps of 2.5-3.6 dB)
exist because a row holds one volume. A slide row ramps over speed-1 ticks, so a 3 dB rise needs no cap:
it arrives as 3-5 steps of 0.6-1 dB. Where a row's wanted change |d| is small enough to reach with
y x (speed-1), use `A0y`/`Ax0` instead of `Cxx`. Expected gain: the French Horn/Strings contour lag and
the audible pump drop; **no size change**; cells are the same (a row with a level change already has a
`Cxx`). Cost: one-tick lag (tick 0 plays the old level; the planner prices it); more volume writes (a
`what_to_do` bit 0 per sliding track per tick). Also `level_at` (`ntpsong.py` 543-549) does not model an
`FX_SLIDE` cell (it returns the earlier `Cxx`, so a sustain chain would drift); it needs the written level
that `plan_release` returns.

**Extension B: mean-release rows stay as the fallback** where speed is 3 and ticks are long; do not drop
them.

**Risks.** A slide row cannot carry a velocity or an `EDx`, so the planner must test `effect_free`, as it
does now (`ntpsong.py` 738-750). The measured volume curve is non-monotonic in the foot; the slide passes
through written values below 7 that are silent. `plan_release` already prices that (`levels`); it must also
keep sustain slides out of written volumes below 7 unless the target is silent.

**Trial design.** Variant `slides-sustain` (base `slides-per-song`; `release_slides = true`,
`sustain_slides = true`). Benchmarks MI168 (horn pump, docs 295), MI167 (strings) and MI2 177. Listening
build with Munt, DOC render. Check in `docrender` (already plays Axy as NTP does) and then one
GSSquared DOC-register log of MI168's French Horn rows as the ghost work did (docs 366-371). Metrics from
`tools/ntp_*`: rises over 2.5 dB (docs 295 table), steals, drops.

**Candid.** The M2177 verdict is the user's. The quiet -20 dB tail in Munt that "may be reverb" is not
reachable by any effect: reverb is a mix-level wash, not a note property. The only way to show it is to keep
a track sounding at about volume 7-12, which costs oscillators. Check first whether it is a release or
reverb (a solo render with Munt's reverb off does it; `just mt32-wav` has the knobs).

## 3. Recommendation 2: pitch bend (1xy, 2xy, E1x, E2x, 3xy) — a gap nobody listed

**Finding.** `docs/games/monkey2.md` 36: "Pitch bend isn't played (the NTP pipeline has none); MI2 bends a
lot (16,193 events)". `docs/games/atlantis.md` 31: 7,284 events. `src/migs/imuse.py` line 254 skips the
event, so **the MT-32 MIDI and the Munt reference WAVs lack the bends too**. The user compares MOD renders
against Munt renders of the same bend-less MIDI, so a missing bend is invisible to every comparison we
make. It matters only if the aim is the game's sound. That is a decision for the user, not an engineering
call.

**Why effects fit.** NTP has per-tick pitch slides in period units, tone portamento and fine slides. No
sample cost, no DOC RAM cost. Slide step is one period (section 0, item 1): coarse at high notes (62 cents
at period 28), fine in the middle.

**What it would take.** (1) extraction: keep type-0xE events with the bend range (RPN or MT-32 pitch-bend
range; MT-32 default is 12 semitones... unchecked here) in the SMF; (2) planner: note-plus-bend tracks
with `1xy`/`2xy` rows. Conflict: a bent note's rows then lose their `Cxx` column on bend rows
(one effect per cell), and `5xy` is slide-to-note plus volume slide, not 1xy plus volume slide; the
only combined effect for a bend plus a level change is `5xy` (3xy continuing) with a slide. Practical:
bends on notes with steady level are cheap; bends on decaying notes force the level onto slides or onto
the next row.

**Expected gain.** Unknown until counted; "16,193 events" is many events per bend, not many bends.
**First step, read-only:** a script that parses the MI2/Atlantis sounds, groups bend events into
excursions, and tabulates, per MT-32 program: excursion count, size in semitones/cents, duration, and
whether the note's level is steady. That is an hour's work and decides everything.

**Risks.** `3xy` shares `slide_delta` with 1xy/2xy (s. 2601, 2617), so a 3xy after a 1xy uses the
1xy speed; never mix them in one row chain without restating the speed. A slide past the table end is
clamped (`max_finetune_ptr` 3574, `handle_slide_up` s. 3196-3215). A bend of 12 semitones from a high key
at period 28 would clamp. Desktop libopenmpt previews will use PT slide units (1xy = 1 period per tick
too), so they should agree; a slide beyond ProTracker's own range (period 113-856 in PT, not our 28) may
differ in a desktop player; keep desktop previews as a check only.

**Trial design.** Not before the count. If the count says bends are rare, record it in
`docs/games/monkey2.md` and stop. If common: variant `bend-per-song` for MI2 with `pitch_bend = true`;
the benchmark is the one song with the most excursions on a steady-level part. Munt reference needs the
bends in the SMF, so the extraction change comes first, and the MT-32 and GM MIDI renditions change
with it (both must keep working per CLAUDE.md).

## 4. Recommendation 3: vibrato by 4xy/6xy instead of whole-cycle raw loops

**Problem.** Sounds whose movement is a pitch LFO (`docsim2.sound_kind` "vibrato": Whistle 2 6.16 Hz
+-28 cents, Chorale 5.66 Hz +-18, Elec Organ 3.66 Hz +-16, Sax 5.18 Hz +-11-13; docs 703-720) keep it by
raw loops of whole cycles, bought as one set per sound. Sets that did not fit before `sets_take_zones`:
MI1 5 sounds in 3 songs, MI2 42 sounds in 32 songs, Atlantis 98 sounds in 71 of 113 songs (docs 743).
Atlantis 48's whistle costs 54 pages for three zones (4096 + 8192 + 2048 B, with the planner's
whole-cycle sizes); a plain 512 B loop is 2 pages a zone.

**Mechanism.** Short averaged loop (as today's plain zones) plus a software vibrato: `4xy` once to set
rate and depth (resets phase), then `6xy` in every following sustain row (no phase reset, volume slide
through its Axy half). The song's tick rate sets the rate: f = x * tickrate / 64, x = 1..15, at 40-50 Hz
ticks about 0.6-0.78 Hz steps, a 6.16 Hz vibrato is x = 10 at 40 Hz (6.25 Hz). Depth is whole periods, so
cents -> y = round(cents x period / 1731): fine in the middle octaves (+-17 cents at period ~200 is y=2),
unusable above about period 60 for +-12 cents (y rounds to 0 or 1 = 29 cents). Notes in the coarse zone
play plain.

**Gain.** Tens of pages per vibrato sound (the whistle: ~54 -> ~6), which the planner would spend on
sets that did not fit and on extra zones (the user says extra zones and movement are worth more the wider
the range, docs 765). That is the one DOC-RAM-saving idea in this list. No `.NTP` size change except fewer
sample pages.

**Costs and risks (all reading, none measured).**
- **Column conflict.** Every sustain row of a vibrato note needs `6xy`; a row that needs a level change must
  carry it as the slide half of the same cell (`60y` or `6x0`), i.e. level changes become slides of the
  step size above. So #1's sustain slides are a prerequisite. A `Cxx` row would cut the vibrato for one
  row (the cell's effect becomes C; vibrato position is kept but pitch snaps back to the centre for the row).
- **Not the same sound.** The MT-32's movement is a sound-level property. The sine is stepped (small y has
  3-5 levels), pitch moves in whole periods, and the LFO phase starts wherever the track's `vibrato_pos` is
  (6xy does not reset it; only 4xy does). The raw loop also carries any beating between partials; only the
  pitch is carried here. "Elec Organ" in the list is explicitly not a pure LFO (the trial removed for
  being a wah, docs 872-886): leave it out; start with whistle/Chorale/Sax-type sounds, the ones the
  measure calls regular at every key.
- **Tick rate.** At speed 3 and 111 ms rows, ticks are 37 ms (27 Hz), i.e. 4 samples per 6 Hz cycle. It
  needs `tick_max_ms` about 25 or less (so 6+ points per cycle). That is more ticks (CPU, see section 0).
- **Desktop previews.** PT's depth for 4xy is `(sine * depth) >> 7` over a 255-amplitude table, about
  2y periods peak; NTP's is y. So libopenmpt would show twice the vibrato. Trust `docrender` only, after
  adding NTP's `sine_tbl` and the 6xy continuation to it.
- **Retrigger on instrument number.** A 6xy row with an instrument number resets nothing, but a cell
  with an instrument and no note on a non-ideal-size looped sample retriggers the last note (s. 2492-2506:
  "edge case to avoid clicking"). Never put an instrument number on a vibrato row.

**Trial design.** Variant `vib-fx-per-song` (base `inst-per-song`): `vibrato_effect = true` for sounds with
`sound_kind == "vibrato"` and a measured rate at every key; the planner then treats the sound as plain
(512 B loops, no `sync_loops` purchase) and the writer emits `4xy` on the first sustain row and `6xy` after.
Benchmarks: Atlantis 48 (Whistle 2, the user's case), a Chorale song and a Sax song in MI1 (MI159, MI163).
The comparison is against `inst-per-song` as built, with the page counts printed beside. Because this swaps a
measured loop for a synthetic one, only listening decides; I do not predict it.

## 5. Recommendation 4: ECx inside a row

**Mechanism.** A note whose real length is under a row (staccato; `r_off = max(r_off, r+1)` forces one row,
docs 487; rows over 10th-percentile note length in 51 of 96 MI2 and 9 of 16 MI1 songs) could cut itself at
tick x of its own row: `note + inst + ECx`. With `tick_max_ms` 25 and rows of 110 ms that is about 4
resolvable cut times against the 1 now.

**Constraint.** The cell's effect column then cannot hold `Cxx`, and the cell's instrument number sets the
sample's default volume at tick 0 (s. 2480-2490). So it only works for a note whose velocity is within a few
dB of the sample default (`delay_snap_db` territory: 42-46 % of notes start at default; docs 842-843), and not
for a note that also needs `EDx`. Candidates are a subset of short notes.

**In `plan_release` as an extra option,** cheaper and safer: a release row "hold w for x ticks, then 0"
(`ECx`) next to `Cxx` and `A0y`. `A0y` with y at most 15 cannot cut from a high volume in one tick; `ECx`
can. Useful for the fastest releases (Elec Org 2, Accordion, -20 dB in 10 ms). With ticks at 37 ms the hold
overshoots the real 10 ms, so it matters only with `tick_max_ms` 25 or lower. Honest expectation: small.

**Quirks.** Never emit x=0 (no cut) or x >= speed (the count survives into the next row, `handle_note_cut`
s. 3436-3450). After the cut the loop still runs silently, so the existing rest-plus-`C00` halt still has
to land on a later row (the ghost rule, docs 364-395).

**Trial design.** Fold into #1's variant as `note_cut = true`, so there is one listening build, not two.

## 6. The open problems an effect does not solve, with the real lever

**M2160 Soundtrack: the slow release missing.** Hypothesis from code, not tested: the release is taken from
a window of `release_max` = 0.3 s (`ntpsamples.py` 1127-1128; `config/games/monkey2/ntp.toml` 53) and
`steps = len(rel_g) x 0.01 / row_s` (`ntpsong.py` ~700-722), after which the writer sets 0 (`gg = 0.0` when
`i >= steps`). A release longer than 0.3 s therefore ends in a cut from the last measured level, not a tail.
Check: `rel_g[-1]` for Soundtrack (and any long-release patch). If it is well above 0, the lever is
`release_max` (per sound), at the cost of longer-busy tracks (more steals). No effect shortens that cost:
slides are one cell per row, like `Cxx`. The pulsing being stronger than Munt's is separate: the plan bought
Soundtrack a 16384 B raw loop at key 44 (`slides-per-song.log` 1774) and the song's Fantasy movement set; no
effect reduces the pulse and the user doubted it was worth chasing.

**M2208 "out of place tones under the pipe organs".** No effect hypothesis. Candidates the docs already
name: the `sets_take_zones` trades (Big Drum 2 -> 1, Str Sect 3 3 -> 1, Brs Sect 1 2 -> 1 for Pipe Org 1's
movement, docs 760), merges, ghosts or steals. Next step: a solo-track render per track of that song.

**Swell 1 at MOD volume 1-2.** The level resolution is 1/64; volume 1 and 2 are 6 dB apart, 1.5-3.5 dB
apart after the measured map. No effect makes finer steps (a slide is integer steps too, an `A` slide cannot
plateau mid-row; `EAx`/`EBx` are the same units). Lever: store the Swell sample at a lower peak so its
playing volume lands near 8-16 (costs 8-bit resolution: at a quarter amplitude about 12 dB worse SNR, noise
that matters little for a swell pad but is unmeasured here). A per-sound sample gain in `mt32sounds.toml`
and a volume-range check would do it.

**FOA001 Triangle tuning.** If the MT-32's Triangle does not follow the key (docs 958), any transposed zone
is wrong and no effect (not even E1x/E2x) fixes it: the answer is a drum-style plain one-shot per key
(or one key for all). Measure whether Munt's Triangle moves with the key before changing the planner.

**Drops and steals for lack of oscillators.** Only arpeggio (0xy) would put several pitches on one track,
and it restarts the sample every step (s. 3157-3180), so each step is a new attack at 25-40 ms: no.
Slides ending a release inside its last row could free a track a row earlier, but the log above showed
more drops with slides, not fewer, so that is not a gain yet.

**Quiet-volume neighbour effect on the real DOC.** A loud oscillator in the slot before raises a quiet one
by 5-10 volume units (docs/es5503.md 212-224). No effect touches it. The only handle is oscillator order:
put quiet melodic tracks after quiet ones. `track order` is decided by the converter (first-fit pairs on
even oscillators, docs 1170-1171), so we control only the order we emit tracks.

## 7. Already-solved problems: does an effect do better?

| Solved | Current | Does an effect do better? |
|---|---|---|
| Staccato; note length against row | mean release rows; `row_fit` rejected (+17 % size) | Partly: slides (#1) shape the release inside the row; ECx (#4) cuts early where velocity = default. Neither changes the minimum of one row for the note itself. Neither costs size. |
| Mean release rows | RMS gain per row | Slides replace the flat mean with a tick-wise shape. Keep mean as the fallback. |
| Vibrato/tremolo kept by long raw loops | `sync_loops`, whole-cycle raw loops, costing pages | Vibrato by 6xy could be cheaper in pages (#3). Tremolo by 7xy: no (tone-change sounds pumped, docs 872-886; and s. 3345 has no volume clamp: index past 65 reads `freq_finetune`). |
| Extra key zones for tuning or timbre | pitch_plan zones, `pitch_cents_db` | Timbre: no. Tuning: E1x/E2x can reach the nearest integer period when the finetune table's entry is off, but the floor stays one period, the column conflicts with `Cxx`, and the planner already prices it. Low. |
| Coarse table at high notes | zone choice, `best_tuning` | The floor is the integer period. No effect is finer. |
| Halting released loops with a silent rest | `halt_released_loops` | No effect halts an oscillator; only a note start stops one (`talk_to_doc` stop pass, s. 1629-1645). The rest stays. |
| Delayed notes `EDx`, ghosts | `halt_before_delayed`, `delay_snap_ms` | Nothing replaces EDx. A varying-speed row (Fxx per row, below) could put more onsets on row starts. |
| Envelope rise limit | `env_max_rise_db` 1.5 | Sustain slides (#1 extension A) could make the limit unnecessary. |
| Tempo changes | `F` BPM and speed | Fine as is. Note one edge: NTP reads F20 (32) as BPM, ProTracker and libopenmpt read <= 32 as ticks (inventory note 18); a 32 BPM tempo would preview wrongly on desktop. |
| Song loops | `Bxx`/`D00`/`F00` | Fine. E6x loops are global, not per track; no gain over the order list. |

## 8. Fxx per row (non-uniform rows), E9x, E5x: speculative, low

- **Fxx per row.** A speed command (`F01`..`F1F`) may sit in any track's empty cell. A row could last 2, 3 or
  4 ticks, so onsets would land on row starts where now 20-35 % are delayed (`v:dflt` 3,920 of 11,902 at
  `delay_snap_ms` 20, docs 842). Same resolution as `EDx` (one tick), but the note cell keeps its
  `Cxx`, so no default-volume start and no ghost. The cost is rows: every extra row is tracks x 4 bytes
  (docs 846, 850: finer rows 2.8x, `row_fit` +17 %). Without a count of distinct onset times per song I
  cannot say whether this beats the grid; `plans/ntp-quantization.md` is on hold for this. Only worth a
  computation (distinct onset offsets modulo the grid), not a trial.
- **E9x.** Repeated notes within a row (flams, rolls: 52 guarded notes left over 6 dB in shared, docs 843;
  112 collapsed unguarded). `E9x` retriggers the track's current sample at the current volume every x ticks
  (s. 3414); x=0 and x=1 are both "every tick" in NTP. It could carry a roll but not a flam of two
  different velocities. Small count; skip unless the roll count is large.
- **E5x per note.** `set_sample_finetune` writes the instrument's finetune globally (s. 2949-2960), so the
  next note on any track with that instrument takes it. A per-note tuning would race with other tracks.

## 9. Before any trial: two measurements

1. **CPU per tick** at the slide trial's tick rates on a real build. A GSSquared cycle count between two
   interrupts with a busy song (MI160 or M2125) at tempo ~130 (50 Hz) against 67 (27 Hz). Use
   `mcp__gs2-debug__*` (it already served the ghost work). Without it, all of #1-#4 add unquantified
   per-tick cost.
2. **Why the slides build had more drops in M2177** (3 -> 5 for two sounds): see which tracks the slide
   releases hold busy.

## 10. What I did not check

- ProTracker semantics of PT's 9xy units, random waveform, F at exactly 32, E9 first tick (inventory
  "Unverified"): not needed for our emitted effects.
- Whether `docrender.py` models ECx, 4xy and 6xy as NTP does (it models Axy). It must before any listening
  build of #3 or #4.
- Real-hardware behaviour of every slide: the DOC volume curve is non-monotonic in its foot and the
  neighbour effect applies; the planner prices the curve but not the neighbour effect.
- The measured size of the bend set (#2), the Soundtrack release tail (section 6), the number of short notes
  at default velocity (#4).
