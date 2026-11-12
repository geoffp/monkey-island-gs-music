# Indiana Jones and the Fate of Atlantis (`atlantis`)

*Indiana Jones and the Fate of Atlantis*, DOS CD-ROM release, English (SCUMM v5, iMUSE).
Game id `atlantis` (ScummVM's); settings in `config/games/atlantis/`, output in
`build/atlantis/`. The formats are in `../scumm-v5-resources.md` and `../imuse-format.md`;
this page holds what is true of this game only. Verified against the game data on
2026-11-11: the MI2 reader read every sound unchanged.

## Release
| file | md5 |
|---|---|
| `ATLANTIS.000` | `182344899c2e2998fca0bebcd82aa81a` (ScummVM `scumm-md5.h`: atlantis, CD, English, DOS, 12,035 bytes) |
| `ATLANTIS.001` | `4c8e73ac88f522b3452b37a607240190` |

`MONSTER.SOG` (the speech) isn't read.

## Where the music is
- The sound directory has 250 entries, 207 used; 204 sounds have a `ROL ` (MT-32) part
  (148 with `ADL `, 52 with `ADL ` and `SPK `, 4 `ROL ` only; 3 are Sound Blaster only).
- **Music:** the `ROL ` sounds up to 169 with at least 40 notes and 8 s of playing
  (`game.toml` `[music]`, a decision): **113 songs**, 77 looping. Sounds 170 on are effects
  (one note or a few, each with its own custom timbre: clicks, splashes, shots).
- **Custom timbres:** 52 distinct (`build/atlantis/extracted/timbres.toml`); the songs use
  ten: Arabongo, Big Drum, Fire Pit (both also in MI2), Mellow Str, applause, boom15,
  cb tpt 2, crash, squirel, street. Their GM equivalents (`game.toml` `[mt32] timbre_gm`)
  are guesses by name: ScummVM's table doesn't map them.
- **The driver's MT-32 set-up:** taken as MI2's (the same "old system" driver in ScummVM,
  `IMuseDriver_MT32`, for MI2 and Indy4).
- **17 songs change tempo**; the NTP build follows them (`../ninjatrackerplus.md`, "Songs
  that change tempo").
- Not followed, as for MI2: pitch bend (7,284 events), hooks that wait for scripts (879
  jumps, 91 other hooks).

## The songs
One pass at the song's starting tempo; *loop from X* = where the loop goes back to.

| id | length | loop | channels | notes | tempo changes |
|---|---|---|---|---|---|
| 1 | 267.3s | from 0.2s | 8 | 1439 |  |
| 2 | 55.6s | once | 8 | 139 | 11 |
| 3 | 68.7s | from 0.1s | 6 | 700 | 1 |
| 5 | 40.2s | once | 5 | 254 |  |
| 6 | 24.6s | once | 3 | 423 |  |
| 7 | 171.2s | from 0.2s | 2 | 555 |  |
| 8 | 151.5s | from 21.0s | 6 | 888 | 2 |
| 9 | 12.2s | from 4.2s | 3 | 243 |  |
| 10 | 24.6s | once | 5 | 1093 |  |
| 11 | 185.7s | from 72.4s | 9 | 238 |  |
| 12 | 9.0s | from 0.1s | 6 | 214 |  |
| 14 | 47.4s | once | 5 | 636 |  |
| 15 | 96.1s | from 0.1s | 5 | 231 |  |
| 19 | 46.5s | once | 3 | 246 |  |
| 20 | 100.5s | from 0.2s | 4 | 179 |  |
| 21 | 34.7s | once | 8 | 616 |  |
| 22 | 40.1s | from 0.1s | 1 | 161 |  |
| 24 | 20.1s | once | 7 | 217 |  |
| 25 | 16.2s | once | 9 | 272 |  |
| 26 | 65.9s | from 0.1s | 6 | 931 |  |
| 27 | 121.1s | from 41.1s | 6 | 1177 |  |
| 28 | 45.6s | once | 7 | 619 |  |
| 29 | 80.1s | from 0.1s | 2 | 247 |  |
| 30 | 40.1s | from 0.1s | 1 | 152 |  |
| 31 | 54.5s | from 43.3s | 6 | 613 |  |
| 32 | 84.8s | from 0.1s | 7 | 2702 |  |
| 33 | 40.1s | from 0.1s | 6 | 285 | 1 |
| 37 | 66.6s | from 11.2s | 8 | 1316 |  |
| 39 | 96.1s | from 0.1s | 4 | 255 |  |
| 40 | 100.2s | from 0.2s | 6 | 823 |  |
| 41 | 72.5s | once | 6 | 718 |  |
| 42 | 72.3s | once | 5 | 615 |  |
| 43 | 84.1s | from 28.9s | 3 | 715 |  |
| 44 | 207.1s | from 33.1s | 2 | 609 |  |
| 45 | 23.1s | once | 5 | 69 |  |
| 46 | 122.6s | from 0.3s | 6 | 1071 | 2 |
| 47 | 70.1s | from 9.0s | 3 | 437 |  |
| 48 | 140.2s | from 100.2s | 3 | 482 |  |
| 49 | 60.3s | once | 8 | 518 | 18 |
| 50 | 8.1s | once | 5 | 64 | 94 |
| 51 | 32.2s | from 0.2s | 2 | 46 |  |
| 53 | 28.6s | from 3.4s | 2 | 451 |  |
| 54 | 14.1s | from 0.1s | 5 | 106 |  |
| 55 | 53.5s | once | 3 | 506 |  |
| 56 | 21.9s | once | 2 | 140 |  |
| 57 | 30.9s | once | 3 | 204 |  |
| 58 | 129.4s | from 0.2s | 6 | 931 |  |
| 59 | 80.3s | from 0.3s | 8 | 841 |  |
| 61 | 66.2s | from 0.2s | 3 | 872 |  |
| 62 | 28.6s | from 3.4s | 2 | 527 |  |
| 64 | 32.8s | from 0.8s | 5 | 316 |  |
| 65 | 33.9s | from 0.1s | 4 | 237 | 1 |
| 66 | 240.2s | from 0.2s | 2 | 543 |  |
| 67 | 405.2s | from 0.2s | 3 | 415 |  |
| 68 | 150.2s | from 0.2s | 3 | 174 |  |
| 69 | 262.7s | from 0.2s | 3 | 705 |  |
| 70 | 111.8s | from 1.0s | 4 | 243 |  |
| 71 | 56.4s | from 1.0s | 4 | 543 |  |
| 72 | 168.1s | from 0.1s | 7 | 1002 |  |
| 73 | 174.2s | from 27.9s | 6 | 485 |  |
| 74 | 123.3s | from 0.1s | 2 | 238 |  |
| 75 | 244.6s | from 0.1s | 6 | 668 | 5 |
| 78 | 82.1s | from 4.1s | 4 | 91 |  |
| 80 | 146.1s | from 0.2s | 6 | 662 |  |
| 81 | 19.7s | once | 5 | 113 |  |
| 82 | 103.6s | from 0.1s | 5 | 558 |  |
| 85 | 68.9s | from 4.0s | 7 | 203 |  |
| 86 | 27.1s | from 0.1s | 5 | 1048 |  |
| 87 | 58.2s | once | 6 | 471 | 1 |
| 88 | 142.1s | once | 9 | 1836 |  |
| 89 | 12.2s | from 0.2s | 3 | 121 |  |
| 90 | 75.7s | from 19.9s | 4 | 189 |  |
| 95 | 13.4s | once | 5 | 120 |  |
| 97 | 12.1s | once | 7 | 99 | 9 |
| 99 | 180.1s | from 0.1s | 6 | 647 |  |
| 103 | 14.1s | from 12.1s | 7 | 415 |  |
| 104 | 150.1s | from 0.2s | 1 | 471 |  |
| 105 | 151.0s | from 0.8s | 8 | 395 |  |
| 110 | 11.2s | from 4.6s | 5 | 339 |  |
| 112 | 322.4s | from 0.2s | 9 | 1313 |  |
| 113 | 92.2s | from 0.2s | 7 | 342 | 1 |
| 114 | 76.0s | from 13.0s | 8 | 1101 |  |
| 115 | 48.0s | from 8.0s | 8 | 400 |  |
| 119 | 65.5s | from 0.1s | 8 | 791 |  |
| 120 | 38.7s | from 25.8s | 5 | 219 |  |
| 122 | 51.5s | from 0.1s | 6 | 550 |  |
| 127 | 8.7s | once | 6 | 88 | 50 |
| 129 | 28.0s | once | 4 | 133 |  |
| 130 | 13.0s | once | 3 | 60 |  |
| 133 | 37.4s | once | 4 | 148 |  |
| 135 | 58.7s | once | 5 | 259 |  |
| 138 | 11.2s | once | 4 | 50 |  |
| 139 | 28.8s | once | 5 | 139 |  |
| 140 | 78.2s | from 48.2s | 5 | 554 |  |
| 143 | 41.1s | from 9.1s | 5 | 387 |  |
| 145 | 9.7s | from 2.5s | 4 | 445 |  |
| 146 | 10.8s | from 3.6s | 3 | 190 |  |
| 147 | 60.6s | once | 9 | 1536 | 37 |
| 148 | 129.5s | from 66.3s | 8 | 896 |  |
| 151 | 204.2s | from 12.2s | 4 | 855 |  |
| 153 | 25.9s | once | 6 | 54 |  |
| 155 | 64.8s | from 36.3s | 7 | 1735 |  |
| 156 | 65.8s | from 2.9s | 4 | 457 |  |
| 157 | 48.4s | from 0.6s | 4 | 106 |  |
| 158 | 14.7s | from 0.1s | 7 | 333 |  |
| 159 | 34.1s | once | 8 | 610 |  |
| 160 | 15.8s | from 0.1s | 7 | 242 |  |
| 161 | 30.7s | once | 7 | 884 |  |
| 162 | 38.7s | from 25.8s | 1 | 115 |  |
| 163 | 36.4s | from 10.2s | 8 | 446 | 1 |
| 164 | 39.7s | once | 6 | 213 | 1 |
| 165 | 13.6s | once | 6 | 165 | 6 |
| 166 | 35.4s | from 2.2s | 4 | 777 |  |

## Open questions
- Song names (cues).
- Does the Atlantis driver send the same MT-32 set-up as MI2's? (Assumed from ScummVM.)
