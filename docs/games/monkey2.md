# Monkey Island 2 (`monkey2`)

*Monkey Island 2: LeChuck's Revenge*, DOS floppy release, English (SCUMM v5, iMUSE). Game id
`monkey2` (ScummVM's); settings in `config/games/monkey2/`, output in `build/monkey2/`. The
formats are in `../scumm-v5-resources.md` and `../imuse-format.md`; this page holds what is
true of this game only. Verified against the game data on 2026-11-11.

## Release
| file | md5 |
|---|---|
| `monkey2.000` | `3686cf8f89e102ececf4366e1d2c8126` (ScummVM `scumm-md5.h`: monkey2, English, DOS, 11,135 bytes) |
| `monkey2.001` | `50993d719990d2e212c50a915f6c50da` |

Both are in `game.toml`'s `[md5]`. The folder also holds `monkey2.exe` and the sound drivers
(`roland.ims`, `rol_33x.ims`, `adlib.ims`, `sounblas.ims`, `speaker.ims`); they aren't read.

## Where the music is
- The index's sound directory (`DSOU`) has 254 entries, 199 used. 142 sounds have a `ROL `
  (MT-32) part.
- **Music:** the `ROL ` sounds from 100 on with at least 40 notes and 8 s of playing
  (`game.toml` `[music]`, a decision): **97 songs**, 64 looping, 33 playing once, no
  byte-identical copies. Below 100 are effects (spits, burps, the foghorn: they carry most
  of the custom timbres); the cut drops 30 ROL sounds from 100 on, all cues under 8 s or with under 40 notes (113,
  124, 129, 130, 156, 158, 163, 167, 168, 175, 183-192, 196, 203, 204, 206, 207, 221,
  223-226; 130 plays no note before its loop, 167 is 21 s with 30 notes).
- **Custom timbres:** 20 distinct (`build/monkey2/extracted/timbres.toml`, ids 128-147).
  The musical ones: ReggaeBass (139; the bass of 42 sounds), Big Drum (129), Fire Pit (132),
  LeshBass (136), Swell 1 (145), Swell 2 (146), SdTrk Bend (142).
- **The driver's MT-32 set-up** (rhythm keys 24-34 mapped to rhythm timbres) is in
  `game.toml` `[mt32] init`; see `../imuse-format.md`.
- Played as each song starts, with no hooks set by scripts (`../imuse-format.md`, "What the
  reader plays"). Sound 100 (Woodtick) is the clearest case: its track 0 is a 9 s loop, and
  the full arrangements are tracks 1-6, which the game's scripts switch to per building.
- Four songs change tempo (154, 159, 211, 222). The NTP build arranges them in beats and its
  tempo commands follow the changes (`docs/ninjatrackerplus.md`, "Songs that change tempo").
- Pitch bend isn't played (the NTP pipeline has none); MI2 bends a lot (16,193 events).

## The songs
One pass at the song's tempo; *loop from X* = where the loop goes back to; channels with
notes after `imuse.remap_channels`.

| id | length | loop | channels | notes | tempo changes |
|---|---|---|---|---|---|
| 100 | 9.1s | from 1.1s | 2 | 76 |  |
| 101 | 108.1s | from 9.0s | 6 | 1450 |  |
| 102 | 48.3s | from 2.7s | 5 | 607 |  |
| 103 | 37.0s | from 1.6s | 5 | 511 |  |
| 104 | 40.5s | from 1.6s | 4 | 527 |  |
| 105 | 32.1s | from 0.1s | 4 | 404 |  |
| 106 | 156.6s | from 8.7s | 9 | 1007 |  |
| 107 | 116.8s | from 0.1s | 2 | 483 |  |
| 108 | 89.6s | from 0.3s | 9 | 1298 |  |
| 109 | 68.8s | from 0.8s | 2 | 135 |  |
| 110 | 37.0s | from 1.6s | 4 | 526 |  |
| 111 | 38.9s | from 1.7s | 5 | 856 |  |
| 112 | 93.7s | once | 6 | 1890 |  |
| 114 | 109.5s | once | 6 | 2084 |  |
| 115 | 112.5s | from 2.5s | 7 | 608 |  |
| 116 | 36.7s | from 7.4s | 6 | 822 |  |
| 117 | 109.5s | from 55.5s | 7 | 1914 |  |
| 118 | 54.0s | from 6.0s | 6 | 1174 |  |
| 119 | 44.3s | once | 7 | 1056 |  |
| 120 | 27.1s | from 0.7s | 7 | 795 |  |
| 121 | 70.7s | from 0.7s | 6 | 284 |  |
| 122 | 13.0s | from 6.6s | 5 | 325 |  |
| 123 | 14.6s | once | 1 | 70 |  |
| 125 | 84.3s | from 2.8s | 8 | 1280 |  |
| 126 | 48.9s | from 8.9s | 7 | 719 |  |
| 127 | 103.4s | from 7.4s | 6 | 942 |  |
| 128 | 35.7s | from 4.7s | 6 | 439 |  |
| 131 | 60.2s | once | 2 | 317 |  |
| 132 | 59.5s | once | 2 | 126 |  |
| 133 | 60.3s | once | 3 | 597 |  |
| 134 | 40.6s | once | 2 | 161 |  |
| 135 | 140.2s | once | 3 | 304 |  |
| 136 | 143.1s | once | 2 | 417 |  |
| 137 | 160.2s | once | 4 | 677 |  |
| 138 | 119.9s | once | 4 | 500 |  |
| 139 | 175.6s | once | 2 | 520 |  |
| 140 | 97.3s | once | 2 | 100 |  |
| 141 | 114.6s | once | 2 | 204 |  |
| 142 | 73.5s | once | 2 | 53 |  |
| 143 | 86.0s | once | 2 | 325 |  |
| 144 | 152.9s | once | 1 | 47 |  |
| 145 | 153.0s | once | 1 | 54 |  |
| 146 | 236.7s | once | 2 | 184 |  |
| 147 | 160.1s | once | 2 | 267 |  |
| 148 | 159.6s | once | 1 | 177 |  |
| 149 | 240.1s | once | 2 | 406 |  |
| 150 | 80.2s | once | 1 | 59 |  |
| 151 | 46.9s | from 23.3s | 6 | 768 |  |
| 152 | 37.0s | from 1.6s | 7 | 669 |  |
| 153 | 23.4s | once | 4 | 438 |  |
| 154 | 132.6s | once | 9 | 3170 | 15 |
| 155 | 17.9s | once | 3 | 279 |  |
| 157 | 29.7s | from 2.5s | 5 | 76 |  |
| 159 | 142.7s | from 0.2s | 4 | 1515 | 1 |
| 160 | 200.1s | from 0.1s | 5 | 475 |  |
| 161 | 20.8s | once | 5 | 107 |  |
| 162 | 78.7s | from 1.7s | 2 | 686 |  |
| 164 | 11.4s | once | 3 | 257 |  |
| 165 | 204.2s | from 0.1s | 1 | 237 |  |
| 166 | 66.3s | from 0.1s | 7 | 341 |  |
| 169 | 9.6s | once | 5 | 173 |  |
| 170 | 49.0s | from 5.5s | 3 | 577 |  |
| 171 | 122.3s | from 0.1s | 4 | 961 |  |
| 172 | 100.8s | from 0.1s | 7 | 1201 |  |
| 173 | 148.0s | from 36.6s | 9 | 1353 |  |
| 174 | 26.1s | from 0.2s | 4 | 53 |  |
| 176 | 59.2s | from 31.3s | 8 | 429 |  |
| 177 | 62.7s | from 1.6s | 9 | 913 |  |
| 178 | 52.2s | once | 8 | 800 |  |
| 179 | 28.4s | from 6.9s | 7 | 463 |  |
| 180 | 60.2s | from 0.2s | 6 | 486 |  |
| 181 | 17.8s | from 15.8s | 4 | 41 |  |
| 182 | 28.5s | from 0.1s | 5 | 435 |  |
| 193 | 9.8s | from 0.1s | 7 | 163 |  |
| 194 | 14.6s | from 3.1s | 5 | 152 |  |
| 195 | 12.1s | from 2.5s | 5 | 119 |  |
| 197 | 16.1s | from 2.1s | 8 | 122 |  |
| 198 | 9.8s | from 0.1s | 6 | 165 |  |
| 199 | 17.3s | from 0.1s | 8 | 155 |  |
| 200 | 16.7s | from 0.1s | 6 | 172 |  |
| 201 | 68.8s | from 0.8s | 6 | 612 |  |
| 202 | 68.7s | from 34.4s | 9 | 472 |  |
| 205 | 32.1s | from 0.1s | 7 | 65 |  |
| 208 | 16.7s | once | 9 | 115 |  |
| 209 | 13.1s | once | 4 | 277 |  |
| 210 | 25.0s | from 2.0s | 5 | 95 |  |
| 211 | 47.0s | from 0.1s | 6 | 722 | 113 |
| 212 | 106.0s | from 18.9s | 8 | 1325 |  |
| 213 | 93.6s | from 46.9s | 5 | 784 |  |
| 214 | 93.6s | from 46.9s | 4 | 514 |  |
| 215 | 93.6s | from 46.9s | 4 | 652 |  |
| 216 | 93.6s | from 46.9s | 5 | 354 |  |
| 217 | 93.6s | from 46.9s | 2 | 320 |  |
| 218 | 93.6s | from 46.9s | 4 | 504 |  |
| 219 | 93.6s | from 46.9s | 3 | 541 |  |
| 220 | 93.6s | from 46.9s | 3 | 580 |  |
| 222 | 156.1s | from 129.5s | 9 | 3052 | 41 |

## Open questions
- Sound 222 plays 37 sounds, more than a MOD's 31 samples: the NTP build drops its 8 least-played drum keys (`docs/ninjatrackerplus.md`, "Songs with too many sounds"). Which merges would keep them?
- Names of the songs (cues): from the scripts, or by ear.
- Which hooks the scripts set for each room, so songs like Woodtick (100) could be
  extracted per building.
- Does `roland.ims` send more set-up than ScummVM's driver reproduces?
