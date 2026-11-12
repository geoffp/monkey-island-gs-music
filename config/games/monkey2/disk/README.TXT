# Template for the NTP disk's README.TXT (`just disk` fills it in: src/migs/diskreadme.py).
# Lines starting with "#" are dropped. Fields come from the build: {songs} (songs per
# variant folder), {first} and {last} (their file names), {gaps} (SenseiPlay's placeholder
# files). [[...]] is left out when a field in it is empty. A paragraph with a field is
# re-wrapped to 70 columns once filled in. The rest is written by hand: keep the variant
# folders in step with the justfile's ntp_modes. ProDOS text: plain 7-bit ASCII.
MONKEY ISLAND 2 - MT-32 SOUNDTRACK FOR THE APPLE IIGS
=====================================================

The Roland MT-32 soundtrack of Monkey Island 2: LeChuck's Revenge,
sampled from the MT-32 (with the game's own custom sounds) and
arranged as NinjaTrackerPlus songs for the IIGS's Ensoniq DOC.
The disk boots into SenseiPlay; the songs are in /SONGS.

/SONGS holds the same {songs} songs built two ways:

INST.PER.SONG  Each song carries its own instrument samples, chosen
               for the notes that song plays, with all of the sound
               RAM to itself. The best-sounding set: listen to these.

INST.SHARED    Every song uses one common set of instrument samples,
               identical in every file. That is what a game needs:
               load the instruments into sound RAM once, then load
               only each song's notes. The sound RAM is split among
               the instruments of all the songs, so samples are
               shorter and coarser.

Songs that loop in the game loop here too, for as long as they play;
the others play once and stop. Where the game changes a song as you
walk around (iMUSE), these play it as it starts, before any change.
Pitch bends are not played.

The file names ({first} to {last}) are the game's sound numbers.[[  {gaps}
is a placeholder that SenseiPlay appears to need in long folders;
ignore it.]]
