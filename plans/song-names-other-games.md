# Song names and missing songs: Monkey Island 2, Fate of Atlantis (deferred)

Deferred 2026-11-12 (user: "Let's defer non-MI1 games until later"). MI1 is done
(`config/games/monkey/song_names.toml`, method in `docs/games/monkey.md`, "Song names").

## MI2: what was found
**Names.** The community list is [The Legend of Monkey Island's MT-32 rips](https://legendofmi.com/downloads/music/mi2/)
(46 tracks, `mi2mt32mp3s.zip`). The MI1 chroma matching works here too, with two differences:
- many rips are medleys of several of our sounds, so match each sound to the rip that contains
  it (best score per sound), not each rip to one sound;
- many sounds are short iMUSE pieces (transitions, stingers, variants) with no rip of their own:
  name them after their rip plus a suffix, or leave them `MI2 <id>`.

The per-sound scores of the 2026-11-12 run follow (best rip : score @ lag in seconds). A score of 0.75 or more
is a sure match; below about 0.6, there's no match.

**Missing songs.** Rips with no sound above about 0.7: Captain Dread's map, Rum Rogers'
cottage, Booty Island boutique and costume shop (best is 118, the spit contest), the guard kiosk,
LeChuck's cutscene, the acid pit and the Dinky Island hole. At least two are ours, cut short by `imuse.Reader`:
- **113** ("kiosk sequence", 9.9 KB, 968 raw notes, 4 played): ticks 119/120 are a jump with hook
  id 1 (script) and then an unconditional jump back to tick 0. The song idles in a silent loop until a
  script sets the hook. The reader takes that idle loop as the song's loop, and `min_notes` drops it.
- **175** (24.5 KB, a two-track file: track 0 has 6 notes and loops, track 1 has 290 notes). Nothing in
  track 0 jumps to track 1, so a script must start it.

Other sounds play far fewer notes than they hold (rawnotes scan below). Most are scripted jumps
into variations, which is expected, but 109 (962 → 135), 165 (538 → 237) and 181 (400 → 41) stop
at script hooks and may be missing whole sections.

**A possible rule:** when a song reaches a loop back into what has played without sounding
a note, take the scripted jump out of it instead. Also, read a track that nothing reaches as a
song of its own. Both need `docs/imuse-format.md` entries and tests. Then rerun the matching.

## Fate of Atlantis
No community list maps titles to sound numbers. Candidates (titles only):
[VGMdb, reworked MIDI](https://vgmdb.net/album/91075), [RetroTracks](https://retro.sx/music/301),
[IJC.at](https://www.ijc.at/Movies+TV/Soundtracks/FoA.htm). Find MT-32 rips first, then match.

## Room names (any game)
The index's `RN` block names every room (MI1: `lookout`, `sh-deck`, `cu-bar-1`, ...). With a
script disassembler, the rooms that start a sound could suggest names. That would be computed into `build/`, not config.

## Data from the 2026-11-12 run
```
--- per render
100      11.3s  captain-dread:0.68@-55  dinky-island-jungle:0.59@-186  captain-dread-map:0.58@-71
101     109.7s  dinky-island-jungle:0.61@-185  captain-dread-map:0.58@-66  woodtick:0.54@85
102      51.0s  cemetery:0.93@0  dinky-island-jungle:0.40@-184  booty-island-overview:0.38@-91
103      38.5s  woodtick:0.74@-98  captain-dread:0.56@-79  dinky-island-jungle:0.54@22
104      42.0s  woodtick:0.77@-62  captain-dread:0.57@20  dinky-island-jungle:0.52@25
105      34.0s  phatt-island-jail:0.96@-31  phatt-island-waterfall:0.39@18  phatt-island-overview:0.37@-26
106     162.7s  phatt-island-overview:0.97@0  captain-dread-map:0.61@145  caught-by-lechuck:0.57@-150
107     118.2s  scabb:0.99@1  captain-dread-map:0.58@100  interlude:0.56@32
108      92.4s  introduction:0.95@-2  closing-theme:0.70@-158  dinky-island-jungle:0.52@-54
109      71.4s  voodoo-lady:0.90@1  dinky-island-jungle:0.48@51  interlude:0.44@59
110      38.0s  woodtick:0.96@-172  dinky-island-jungle:0.52@22  captain-dread:0.52@17
111      40.3s  woodtick:0.85@-21  booty-island-overview:0.54@23  captain-dread:0.54@22
112      96.1s  bones-song:0.88@-61  booty-island-overview:0.47@-90  dinky-island-jungle:0.47@-179
114     110.6s  mardi-gras:0.93@0  spit-contest:0.60@-96  booty-island-costume-shop:0.54@-138
115     115.3s  booty-island-overview:0.96@0  dinky-island-jungle:0.61@-134  captain-dread-map:0.61@-71
116      39.6s  stans-theme:0.88@0  caught-by-lechuck:0.53@-154  dinky-island-jungle:0.49@-42
117     110.7s  mardi-gras:0.55@-51  booty-island-overview:0.47@84  dinky-island-jungle:0.46@89
118      56.1s  mardi-gras:0.94@-108  spit-contest:0.81@0  booty-island-costume-shop:0.75@-91
119      45.4s  cook-chase:0.66@0  captain-dread-map:0.61@-70  interlude:0.57@7
120      29.4s  the-dive:0.94@-95  dinky-island-jungle:0.54@12  bones-song:0.53@12
121      79.2s  bones-song:0.83@0  captain-dread-map:0.67@-72  dinky-island-jungle:0.66@-170
122      16.3s  caught-by-lechuck:0.77@-154  bones-song:0.73@-148  phatt-island-overview:0.70@-107
123      16.8s  the-dive:0.57@-49  closing-theme:0.27@-23  bones-song:0.25@-156
125      89.6s  phatt-island-mansion-guard:0.91@-82  guybrush-lechuck-tunnels:0.40@-184  the-dive:0.34@75
126      51.9s  phatt-island-arrival:0.79@1  phatt-island-waterfall:0.52@-128  phatt-island-mansion-outside:0.45@-117
127     105.2s  dinky-island-jungle:0.56@90  bones-song:0.52@88  booty-island-overview:0.51@90
128      37.7s  outside-rum-rogers:0.92@-62  dinky-island-jungle:0.62@23  booty-island-overview:0.53@22
131      61.3s  inside-fortress:0.64@-515  guybrush-lechuck-tunnels:0.44@42  lechuck-in-agony:0.31@32
132      61.3s  inside-fortress:0.62@-515  guybrush-lechuck-tunnels:0.49@41  dinky-island-hole:0.41@28
133      62.0s  inside-fortress:0.57@-515  guybrush-lechuck-tunnels:0.36@41  theme:0.33@40
134      42.5s  inside-fortress:0.68@-495  guybrush-lechuck-tunnels:0.40@22  love-theme:0.25@18
135     141.5s  guybrush-lechuck-tunnels:0.62@122  love-theme:0.40@118  lechuck-in-agony:0.34@120
136     144.3s  inside-fortress:0.79@-515  guybrush-lechuck-tunnels:0.53@122  theme:0.38@129
137     165.9s  inside-fortress:0.85@5  guybrush-lechuck-tunnels:0.39@142  dinky-island-hole:0.33@133
138     118.2s  inside-fortress:0.53@-358  theme:0.33@94  phatt-island-overview:0.31@103
139     177.4s  inside-fortress:0.75@145  guybrush-lechuck-tunnels:0.31@161  lechuck-in-agony:0.26@61
140      98.6s  inside-fortress:0.58@-496  guybrush-lechuck-tunnels:0.55@80  dinky-island-hole:0.33@-119
141     115.9s  inside-fortress:0.69@-375  guybrush-lechuck-tunnels:0.40@82  booty-island-costume-shop:0.36@97
142      75.0s  theme:0.86@60  dinky-island-hole:0.78@60  inside-fortress:0.59@-375
143      87.6s  inside-fortress:0.87@-335  guybrush-lechuck-tunnels:0.48@61  theme:0.43@73
144     159.9s  inside-fortress:0.68@-514  dinky-island-hole:0.67@140  guybrush-lechuck-tunnels:0.45@-150
145     153.9s  inside-fortress:0.73@-495  dinky-island-hole:0.47@139  guybrush-lechuck-tunnels:0.41@123
146     239.3s  inside-fortress:0.65@-115  stans-theme:0.39@224  interlude:0.39@133
147     161.4s  inside-fortress:0.56@-455  guybrush-lechuck-tunnels:0.33@141  lechuck-in-agony:0.30@140
148     160.9s  inside-fortress:0.56@-435  guybrush-lechuck-tunnels:0.39@142  love-theme:0.29@138
149     241.7s  inside-fortress:0.78@-115  phatt-island-tunnel:0.50@227  guybrush-lechuck-tunnels:0.44@221
150      81.5s  inside-fortress:0.59@-115  guybrush-lechuck-tunnels:0.46@61  rum-rogers-secret:0.29@-59
151      49.6s  largo:0.93@24  bones-song:0.47@34  dinky-island-jungle:0.46@33
152      38.7s  woodtick:0.92@-131  dinky-island-jungle:0.60@22  captain-dread:0.57@17
153      29.5s  cook-chase:0.32@-14  bones-song:0.31@-134  booty-island-costume-shop:0.31@-144
154     135.0s  theme:0.92@-3  lechuck-in-agony:0.61@-68  dinky-island-hole:0.60@-119
155      20.8s  rum-rogers-cottage:0.47@-3  introduction:0.47@-72  scabb:0.47@-81
157      31.4s  rum-rogers-secret:0.75@2  theme:0.49@14  lechuck-in-agony:0.49@-66
159     144.7s  dinky-island-beach:0.93@-142  phatt-island-waterfall:0.58@127  phatt-island-overview:0.57@104
160     208.2s  dinky-island-jungle:0.95@-0  booty-island-overview:0.70@191  captain-dread-map:0.70@-69
161      22.5s  dinky-island-jungle:0.68@-66  introduction:0.60@-60  jojo-playing:0.55@-4
162      80.0s  jojo-playing:0.97@-77  lechuck-in-agony:0.46@65  introduction:0.45@-74
164      14.3s  booty-island-boutique:0.54@-119  booty-island-costume-shop:0.54@-119  spit-contest:0.53@-77
165     190.0s  theme:0.35@175  dinky-island-hole:0.28@175  lechuck-in-agony:0.25@173
166      74.6s  caught-by-lechuck:0.89@-41  acid-pit:0.67@-99  dinky-island-hole:0.57@54
169      12.3s  interlude:0.96@-0  bones-song:0.58@-10  booty-island-boutique:0.57@-14
170      45.8s  love-theme:0.77@-51  rum-rogers-cottage:0.39@-116  lechucks-cutscene:0.36@-33
171     123.4s  phatt-island-alley:0.93@-122  dinky-island-beach:0.39@-136  lechucks-cutscene:0.38@105
172     103.2s  phatt-island-wheel:0.94@-101  phatt-island-waterfall:0.37@73  introduction:0.36@-88
173     154.7s  phatt-island-waterfall:0.95@0  dinky-island-beach:0.51@-142  caught-by-lechuck:0.51@-151
174      32.9s  phatt-island-tunnel:0.87@-104  dinky-island-jungle:0.51@-39  dinky-island-hole:0.47@-14
176      62.2s  caught-by-lechuck:0.64@28  acid-pit:0.32@47  voodoo-lady:0.27@42
177      63.3s  phatt-island-mansion-guard:0.76@14  love-theme:0.40@46  theme:0.38@-108
178      52.9s  love-theme:0.93@-0  phatt-island-waterfall:0.52@37  rum-rogers-cottage:0.49@-113
179      31.0s  phatt-island-mansion-outside:0.88@0  caught-by-lechuck:0.57@-150  phatt-island-overview:0.48@-78
180      62.6s  acid-pit:0.31@43  jojo-playing:0.27@47  phatt-island-wheel:0.25@38
181      19.6s  bones-song:0.37@-42  woodtick:0.34@5  booty-island-costume-shop:0.34@-16
182      31.5s  dinky-island-jungle:0.48@14  largo:0.46@-72  bones-song:0.46@14
193      20.4s  theme:0.46@6  bones-song:0.44@-155  dinky-island-hole:0.42@6
194      24.4s  interlude:0.43@6  phatt-island-tunnel:0.38@9  caught-by-lechuck:0.38@-152
195      15.2s  inside-fortress:0.48@-478  guybrush-lechuck-tunnels:0.40@-2  dinky-island-tunnels:0.36@-39
197      26.7s  dinky-island-hole:0.52@-20  theme:0.41@12  interlude:0.40@3
198      20.2s  closing-theme:0.38@-80  guybrush-lechuck-tunnels:0.35@-186  dinky-island-hole:0.34@5
199      25.4s  dinky-island-hole:0.34@10  bones-song:0.24@-149  acid-pit:0.22@-20
200      18.9s  guybrush-lechuck-tunnels:0.81@-17  inside-fortress:0.67@-176  lechuck-in-agony:0.45@-11
201      71.3s  largo-voodoodoll:0.97@-192  phatt-island-mansion-outside:0.48@54  bones-song:0.47@56
202      77.0s  guybrush-lechuck-tunnels:0.89@0  theme:0.65@62  inside-fortress:0.64@-516
205      39.8s  inside-fortress:0.29@-516  phatt-island-mansion-guard:0.29@23  guybrush-lechuck-tunnels:0.20@-170
208      17.0s  closing-theme:0.61@-19  caught-by-lechuck:0.57@-104  bones-song:0.55@-155
209      15.5s  dinky-island-jungle:0.55@-156  phatt-island-waterfall:0.46@-99  caught-by-lechuck:0.46@-113
210      27.3s  lechuck-in-agony:0.95@0  theme:0.53@12  guybrush-lechuck-tunnels:0.49@10
211      49.3s  closing-theme:0.41@1  caught-by-lechuck:0.38@-144  dinky-island-hole:0.35@34
212     110.9s  dinky-island-tunnels:0.92@-0  caught-by-lechuck:0.36@-147  interlude:0.32@12
213      96.4s  dinky-island-tunnels:0.47@-59  interlude:0.38@67  acid-pit:0.31@-117
214      96.3s  dinky-island-tunnels:0.44@-59  interlude:0.36@67  phatt-island-wheel:0.36@81
215      96.7s  dinky-island-tunnels:0.40@-59  phatt-island-wheel:0.34@80  interlude:0.33@67
216      94.4s  interlude:0.35@50  phatt-island-wheel:0.32@73  dinky-island-tunnels:0.29@-60
217      95.1s  interlude:0.45@66  dinky-island-tunnels:0.35@-59  phatt-island-wheel:0.30@80
218      95.4s  dinky-island-tunnels:0.47@-60  phatt-island-wheel:0.36@79  interlude:0.36@68
219      95.6s  dinky-island-tunnels:0.42@-59  interlude:0.39@68  acid-pit:0.30@-117
220      95.3s  dinky-island-tunnels:0.42@-59  interlude:0.41@68  cook-chase:0.27@50
222     158.6s  closing-theme:0.84@-23  introduction:0.74@131  phatt-island-waterfall:0.48@-126

rawnotes scan (sound, room, raw notes per track, played):
100 room 22 raw [76, 975, 25, 71, 161, 286, 54] played 76
102 room 16 raw [888, 186, 53] played 607
103 room 9 raw [511, 37, 43, 36, 44, 42, 45, 33, 35, 37, 24] played 511
104 room 8 raw [527, 26, 28, 26, 25, 23, 33, 23, 32, 34, 41, 13, 169] played 527
109 room 70 raw [962] played 135
110 room 14 raw [823, 45, 53, 48, 56, 54, 48, 53, 56, 59, 29] played 526
111 room 15 raw [856, 69, 43, 69, 45, 69, 48, 71, 40, 64, 22] played 856
113 room 70 raw [968] played 4
115 room 45 raw [1584, 7, 60] played 608
116 room 50 raw [822, 22, 14, 32, 47, 58, 57, 53, 49, 59, 59, 90, 0, 39, 42, 36, 40, 43, 54, 63] played 822
118 room 46 raw [2301] played 1174
120 room 81 raw [1925] played 795
122 room 60 raw [325, 697] played 325
126 room 43 raw [719, 1675, 50] played 719
128 room 38 raw [439, 104, 99] played 439
151 room 7 raw [1122, 40, 59, 69, 57, 0, 90, 90, 87] played 768
152 room 12 raw [669, 31, 39, 38, 45, 41, 42, 73, 36, 39, 28] played 669
157 room 40 raw [97, 23] played 76
165 room 85 raw [538] played 237
166 room 79 raw [625, 14, 327, 35] played 341
168 room 79 raw [4, 100] played 4
170 room 56 raw [1032, 31, 31, 32, 34] played 577
175 room 68 raw [6, 290] played 3
181 room 53 raw [400] played 41
182 room 13 raw [435, 849, 392] played 435
193 room 91 raw [165, 180, 16] played 163
194 room 91 raw [152, 183, 16] played 152
195 room 91 raw [119, 120, 15] played 119
196 room 91 raw [112, 178, 16] played 112
197 room 91 raw [125, 147, 5] played 122
198 room 91 raw [167, 182, 8] played 165
199 room 91 raw [155, 232, 8] played 155
200 room 91 raw [175, 318, 8] played 172
201 room 21 raw [951] played 612
202 room 88 raw [474, 267, 20] played 472
203 room 88 raw [244, 232, 8] played 111
210 room 93 raw [95, 270] played 95
```
