# SPDX-License-Identifier: GPL-3.0-or-later
"""Monkey Island 1 MT-32 soundtrack -> Apple IIGS: extraction from the SCUMM v4 game data,
MIDI Synth sequences and banks, and NinjaTrackerPlus songs.

Modules by stage (CLAUDE.md "Pipeline"):
- extract: scumm_v4 (game files), ro (the MT-32 music stream), mt32 (tables), extract_music;
- MIDI Synth: midisynth (file formats), mid2seq, mt32bank, midisynth_tuning, midisynth_voices,
  midisynth_audition, midisynth_tunetest;
- NinjaTrackerPlus: ntpmod (build), ntpsamples, ntpsong, modfile, ntpfile; loopbuild, looplab;
- shared: munt (MT-32 renders), audio (analysis), docram (DOC RAM and output rate);
- emulator: gs2config (machine configs), bram (battery RAM), gs2client (DOC scope).
"""
