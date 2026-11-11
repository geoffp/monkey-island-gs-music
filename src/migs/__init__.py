# SPDX-License-Identifier: GPL-3.0-or-later
"""SCUMM MT-32 soundtracks -> Apple IIGS (first: Monkey Island 1): extraction from the SCUMM v4
game data, NinjaTrackerPlus songs, and MIDI Synth sequences and banks. The package name is
from "Monkey Island GS".

Modules by stage (CLAUDE.md "Pipeline"):
- extract: scumm_v4 (game files), ro (the MT-32 music stream), mt32 (tables), extract_music;
- MIDI Synth: midisynth (file formats), mid2seq, mt32bank, midisynth_tuning, midisynth_voices,
  midisynth_audition, midisynth_tunetest;
- NinjaTrackerPlus: ntpmod (build), ntpsamples, ntpsong, modfile, ntpfile; loopbuild, looplab;
- shared: munt (MT-32 renders), audio (analysis), docram (DOC RAM and output rate);
- games: games (per-game settings, config/games/<id>/, and output dirs, build/<id>/);
- emulator: gs2config (machine configs), bram (battery RAM), gs2client (DOC scope).
"""
