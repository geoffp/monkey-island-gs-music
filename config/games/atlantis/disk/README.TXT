# Template for the NTP disk's README.TXT (`just disk` fills it in: src/migs/diskreadme.py).
# Lines starting with "#" are dropped. Fields come from the build: {songs} (songs per
# variant folder), {first} and {last} (their file names), {gaps} (SenseiPlay's placeholder
# files). [[...]] is left out when a field in it is empty. A paragraph with a field is
# re-wrapped to 70 columns once filled in. The rest is written by hand: keep the variant
# folders in step with game.toml's ntp_variants. ProDOS text: plain 7-bit ASCII.
FATE OF ATLANTIS - MT-32 SOUNDTRACK FOR THE APPLE IIGS
======================================================

The Roland MT-32 soundtrack of Indiana Jones and the Fate of
Atlantis, sampled from the MT-32 (with the game's own custom sounds)
and arranged as NinjaTrackerPlus songs for the IIGS's Ensoniq DOC.
The disk boots into SenseiPlay; the songs are in /SONGS.

/SONGS/INST.PER.SONG holds {songs} songs. Each song carries its own
instrument samples, chosen for the notes that song plays, with all
of the sound RAM to itself. (There is no shared set, as the Monkey
Island 1 disk has: this game plays too many different sounds for one
set of 31 samples.) A song that plays more sounds than fit leaves
out its least-played ones.

Songs that loop in the game loop here too, for as long as they play;
the others play once and stop. Where the game changes a song as you
walk around (iMUSE), these play it as it starts, before any change.
Pitch bends are not played.

The file names ({first} to {last}) are the game's sound numbers.[[  {gaps}
is a placeholder that SenseiPlay appears to need in long folders;
ignore it.]]
