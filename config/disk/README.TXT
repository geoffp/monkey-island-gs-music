THE SECRET OF MONKEY ISLAND - MT-32 SOUNDTRACK FOR THE APPLE IIGS
=================================================================

The Roland MT-32 soundtrack of The Secret of Monkey Island, sampled
from the MT-32 and arranged as NinjaTrackerPlus songs for the IIGS's
Ensoniq DOC. The disk boots into SenseiPlay; the songs are in /SONGS.

/SONGS holds the same 16 songs built two ways:

INST.PER.SONG  Each song carries its own instrument samples, chosen
               for the notes that song plays, with all of the sound
               RAM to itself. The best-sounding set: listen to these.

INST.SHARED    Every song uses one common set of instrument samples,
               identical in every file. That is what a game needs:
               load the instruments into sound RAM once, then load
               only each song's notes. The sound RAM is split among
               the instruments of all the songs, so samples are
               shorter and coarser.

The file names (MI150 to MI168) are the game's sound numbers.  GAP.13
is a placeholder that SenseiPlay appears to need in long folders;
ignore it.
