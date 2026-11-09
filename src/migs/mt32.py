# SPDX-License-Identifier: GPL-3.0-or-later
"""MT-32 / General MIDI tables and conversion rules (docs/mt32-to-gm.md).

Table provenance (ScummVM, GPL-2.0-or-later; see ref/scummvm):
- MT32_PRESET_NAMES: engines/sci/sound/drivers/map-mt32-to-gm.h (Mt32PresetTimbreMaps names)
- MT32_TO_GM: audio/mididrv.cpp (MidiDriver::_mt32ToGm)
- MT32_RHYTHM_KEYS: map-mt32-to-gm.h (Mt32PresetRhythmKeymap, identity-mapped keys)
- MT32_DEFAULT_PANNING: audio/mt32gm.cpp (MidiDriver_MT32GM::MT32_DEFAULT_PANNING)

Programs, keys and channels are 0-based, as on the wire.
"""

RHYTHM_CHANNEL = 9  # MIDI channel 10

MT32_PRESET_NAMES = (
    "AcouPiano1", "AcouPiano2", "AcouPiano3", "ElecPiano1", "ElecPiano2", "ElecPiano3",
    "ElecPiano4", "Honkytonk", "Elec Org 1", "Elec Org 2", "Elec Org 3", "Elec Org 4",
    "Pipe Org 1", "Pipe Org 2", "Pipe Org 3", "Accordion", "Harpsi 1", "Harpsi 2",
    "Harpsi 3", "Clavi 1", "Clavi 2", "Clavi 3", "Celesta 1", "Celesta 2", "Syn Brass1",
    "Syn Brass2", "Syn Brass3", "Syn Brass4", "Syn Bass 1", "Syn Bass 2", "Syn Bass 3",
    "Syn Bass 4", "Fantasy", "Harmo Pan", "Chorale", "Glasses", "Soundtrack", "Atmosphere",
    "Warm Bell", "Funny Vox", "Echo Bell", "Ice Rain", "Oboe 2001", "Echo Pan", "DoctorSolo",
    "Schooldaze", "BellSinger", "SquareWave", "Str Sect 1", "Str Sect 2", "Str Sect 3",
    "Pizzicato", "Violin 1", "Violin 2", "Cello 1", "Cello 2", "Contrabass", "Harp 1",
    "Harp 2", "Guitar 1", "Guitar 2", "Elec Gtr 1", "Elec Gtr 2", "Sitar", "Acou Bass1",
    "Acou Bass2", "Elec Bass1", "Elec Bass2", "Slap Bass1", "Slap Bass2", "Fretless 1",
    "Fretless 2", "Flute 1", "Flute 2", "Piccolo 1", "Piccolo 2", "Recorder", "Panpipes",
    "Sax 1", "Sax 2", "Sax 3", "Sax 4", "Clarinet 1", "Clarinet 2", "Oboe", "Engl Horn",
    "Bassoon", "Harmonica", "Trumpet 1", "Trumpet 2", "Trombone 1", "Trombone 2",
    "Fr Horn 1", "Fr Horn 2", "Tuba", "Brs Sect 1", "Brs Sect 2", "Vibe 1", "Vibe 2",
    "Syn Mallet", "Wind Bell", "Glock", "Tube Bell", "Xylophone", "Marimba", "Koto", "Sho",
    "Shakuhachi", "Whistle 1", "Whistle 2", "BottleBlow", "BreathPipe", "Timpani",
    "MelodicTom", "Deep Snare", "Elec Perc1", "Elec Perc2", "Taiko", "Taiko Rim", "Cymbal",
    "Castanets", "Triangle", "Orche Hit", "Telephone", "Bird Tweet", "OneNoteJam",
    "WaterBells", "JungleTune",
)

MT32_TO_GM = (  # MT-32 preset program -> nearest GM program
    0, 1, 0, 2, 4, 4, 5, 3, 16, 17, 18, 16, 16, 19, 20, 21,
    6, 6, 6, 7, 7, 7, 8, 112, 62, 62, 63, 63, 38, 38, 39, 39,
    88, 95, 52, 98, 97, 99, 14, 54, 102, 96, 53, 102, 81, 100, 14, 80,
    48, 48, 49, 45, 41, 40, 42, 42, 43, 46, 45, 24, 25, 28, 27, 104,
    32, 32, 34, 33, 36, 37, 35, 35, 79, 73, 72, 72, 74, 75, 64, 65,
    66, 67, 71, 71, 68, 69, 70, 22, 56, 59, 57, 57, 60, 60, 58, 61,
    61, 11, 11, 98, 14, 9, 14, 13, 12, 107, 107, 77, 78, 78, 76, 76,
    47, 117, 127, 118, 118, 116, 115, 119, 115, 112, 55, 124, 123, 0, 14, 117,
)

# MT-32 default rhythm setup: these keys sound, and map to the same GM percussion key.
MT32_RHYTHM_KEYS = frozenset([*range(35, 52), 54, 56, *range(60, 74), 75])

# Power-on panning of MT-32 parts 1-8 (MIDI channels 2-9), in MT-32 (reversed) pan sense.
MT32_DEFAULT_PANNING = (0x40, 0x49, 0x40, 0x49, 0x25, 0x5B, 0x00, 0x7F)

# ScummVM initGM: "Reverb 64 = equivalent of MT-32 default Reverb Level 5".
GM_DEFAULT_REVERB = 64


def flip_pan(v: int) -> int:
    """MT-32 pan is reversed vs. the MIDI spec (ScummVM mt32gm.cpp: 0x80 - v, clamped)."""
    return min(0x80 - v, 0x7F)


def default_pan(channel: int) -> int:
    """MT-32 power-on pan of a melodic part's channel (1-8), in MT-32 pan sense."""
    return MT32_DEFAULT_PANNING[channel - 1]


GM_NAMES = (
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel",
    "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion",
    "Harmonica", "Tango Accordion", "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)", "Electric Guitar (muted)",
    "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
    "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass", "Slap Bass 1",
    "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani", "String Ensemble 1",
    "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs",
    "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2", "Soprano Sax", "Alto Sax",
    "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo",
    "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)",
    "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)",
    "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)",
    "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba",
    "Bagpipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise",
    "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause",
    "Gunshot",
)

GM_DRUM_NAMES = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1", 37: "Side Stick", 38: "Acoustic Snare",
    39: "Hand Clap", 40: "Electric Snare", 41: "Low Floor Tom", 42: "Closed Hi-Hat",
    43: "High Floor Tom", 44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat",
    47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash Cymbal 1", 50: "High Tom",
    51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell", 54: "Tambourine",
    55: "Splash Cymbal", 56: "Cowbell", 57: "Crash Cymbal 2", 58: "Vibraslap",
    59: "Ride Cymbal 2", 60: "Hi Bongo", 61: "Low Bongo", 62: "Mute Hi Conga",
    63: "Open Hi Conga", 64: "Low Conga", 65: "High Timbale", 66: "Low Timbale",
    67: "High Agogo", 68: "Low Agogo", 69: "Cabasa", 70: "Maracas", 71: "Short Whistle",
    72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro", 75: "Claves",
    76: "Hi Wood Block", 77: "Low Wood Block", 78: "Mute Cuica", 79: "Open Cuica",
    80: "Mute Triangle", 81: "Open Triangle",
}
