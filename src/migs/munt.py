# SPDX-License-Identifier: GPL-3.0-or-later
"""Render single MT-32 notes through Munt (mt32emu-smf2wav), cached as WAVs.

Every sampled sound in the project (the MIDI Synth bank, the NTP samples, the loop lab)
starts here. Renders are dry (reverb off), centred, and cached under a name that encodes
everything that affects them, so the cache never needs invalidating.

Munt quirk: smf2wav drops leading silence, so a note scheduled at 0.5 s starts at 0.0 s in
the WAV and its note-off is at onset + hold (docs/iigs-midisynth.md).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import mido
import numpy as np
from scipy.io import wavfile

SR = 32000                      # Munt output rate
MELODIC_CH, RHYTHM_CH = 1, 9    # MT-32 part 1 = MIDI channel 2; rhythm = 10
_TPQ, _TICKS_PER_S = 480, 960   # 120 BPM
_LEAD_IN_S = 0.5                # note-on time in the MIDI file (dropped by smf2wav)
_TAIL_S = 2.0                   # rendered after note-off, for the release


def reverb_off() -> mido.Message:
    """MT-32 SysEx: system area 10 00 03 (reverb level) = 0, with the Roland checksum."""
    addr_data = [0x10, 0x00, 0x03, 0x00]
    checksum = (128 - sum(addr_data) % 128) % 128
    return mido.Message("sysex", data=[0x41, 0x10, 0x16, 0x12, *addr_data, checksum])


TIMBRE_TEMP_PART1 = bytes([0x04, 0x00, 0x00])   # MT-32 timbre temporary area, part 1 (MIDI channel 2)


def timbre_sysex(data: bytes) -> list[int]:
    """Roland DT1 writing a 246-byte timbre to part 1's temporary area: the part then plays it
    (as iMUSE's MT-32 driver loads a custom timbre: IMuseChannel_MT32::sysEx_customInstrument)."""
    body = TIMBRE_TEMP_PART1 + data
    return [0x41, 0x10, 0x16, 0x12, *body, (128 - sum(body) % 128) % 128]


def _note_file(program: int | None, key: int, hold: float, velocity: int, volume: int,
               setup: tuple[bytes, ...] = (), timbre: bytes | None = None) -> mido.MidiFile:
    ch = RHYTHM_CH if program is None else MELODIC_CH
    trk = mido.MidiTrack([*(mido.Message("sysex", data=s) for s in setup), reverb_off(),
                          mido.Message("control_change", channel=ch, control=7, value=volume, time=0),
                          mido.Message("control_change", channel=ch, control=10, value=64, time=0)])
    if timbre is not None:
        trk.append(mido.Message("sysex", data=timbre_sysex(timbre), time=0))
    elif program is not None:
        trk.append(mido.Message("program_change", channel=ch, program=program, time=0))
    trk.append(mido.Message("note_on", channel=ch, note=key, velocity=velocity,
                            time=int(_LEAD_IN_S * _TICKS_PER_S)))
    trk.append(mido.Message("note_off", channel=ch, note=key, velocity=64, time=int(hold * _TICKS_PER_S)))
    trk.append(mido.MetaMessage("end_of_track", time=int(_TAIL_S * _TICKS_PER_S)))
    mf = mido.MidiFile(ticks_per_beat=_TPQ)
    mf.tracks.append(trk)
    return mf


def _hash(*parts: bytes) -> str:
    return hashlib.sha1(b"".join(parts)).hexdigest()[:8]


def sound_tag(program: int | None, setup: tuple[bytes, ...] = (), timbre: bytes | None = None) -> str:
    """What a render plays, as a cache name: the preset (p013), a custom timbre by its content
    (t1a2b3c4d) or the rhythm part, plus the driver set-up when there is one (-i...). Program
    ids >= 128 are a game's own numbering, so they never name a cache entry."""
    s = f"t{_hash(timbre)}" if timbre is not None else ("rhythm" if program is None else f"p{program:03d}")
    return s + (f"-i{_hash(*setup)}" if setup else "")


def render(smf2wav: Path, roms: Path, cache: Path, *, program: int | None, key: int,
           hold: float, velocity: int = 100, volume: int = 100, setup: tuple[bytes, ...] = (),
           timbre: bytes | None = None) -> np.ndarray:
    """Render one note (melodic `program` on part 1, or rhythm `key` if program is None),
    held `hold` s at channel volume (CC7) `volume`. Mono float in [-1, 1) at SR. `setup`: the
    game's MT-32 driver start-up SysEx, sent first; `timbre`: a custom timbre (246 bytes) for
    part 1, played instead of a preset."""
    if program is not None and program >= 128 and timbre is None:
        raise ValueError(f"program {program} is a custom timbre, and no timbre data was given")
    tag = f"{sound_tag(program, setup, timbre)}-k{key}-h{hold}-v{velocity}"
    tag += f"-c{volume}" if volume != 100 else ""
    wav = cache / f"{tag}.wav"
    if not wav.exists():
        cache.mkdir(parents=True, exist_ok=True)
        mid = cache / f"{tag}.{os.getpid()}.mid"         # per process: builds run side by side
        part = cache / f"{tag}.{os.getpid()}.part.wav"  # renamed when complete: an interrupted
        try:                                            # render never poisons the cache
            _note_file(program, key, hold, velocity, volume, setup, timbre).save(mid)
            subprocess.run([str(smf2wav), "--quiet", "-m", str(roms), "-f", "-o", str(part), str(mid)],
                           check=True, stdout=subprocess.DEVNULL)
            os.replace(part, wav)
        finally:                                        # nor leaves per-process files behind
            mid.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
    rate, data = wavfile.read(wav)
    if rate != SR:
        raise ValueError(f"{wav}: expected {SR} Hz, got {rate}")
    x = data.astype(np.float64)
    return (x.mean(axis=1) if x.ndim == 2 else x) / 32768.0


class Munt(NamedTuple):
    """Where Munt and its inputs live, and the game's MT-32 set-up: its driver's start-up
    SysEx and its custom timbres (id -> 246 bytes). Hashable, so renders can be cached."""
    smf2wav: Path
    roms: Path
    cache: Path
    setup: tuple[bytes, ...] = ()
    timbres: tuple[tuple[int, bytes], ...] = ()

    def timbre(self, program: int | None) -> bytes | None:
        return dict(self.timbres).get(program) if program is not None and program >= 128 else None

    def sound_tag(self, program: int | None) -> str:
        return sound_tag(program, self.setup, self.timbre(program))

    def render(self, *, program: int | None, key: int, hold: float, velocity: int = 100,
               volume: int = 100) -> np.ndarray:
        return render(self.smf2wav, self.roms, self.cache, program=program, key=key, hold=hold,
                      velocity=velocity, volume=volume, setup=self.setup, timbre=self.timbre(program))
