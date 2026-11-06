# Monkey Island 1 MT-32 → Apple IIGS MIDI Synth
# Run `just` to list recipes. `just setup` fetches everything not under source control,
# except what you must supply yourself (see README: game data, MT-32 ROMs).

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

game     := "original files/The Secret of Monkey Island (Floppy DOS VGA)"
roms     := "original files/mt-32 roms"
vendor   := "vendor"
gs2      := vendor / "gssquared/GSSquared-x86_64.AppImage"
cp2      := vendor / "cp2/cp2"
smf2wav  := vendor / "munt/bin/mt32emu-smf2wav"
sf2      := vendor / "soundfonts/GeneralUser-GS.sf2"

# Pinned versions / checksums (sha256)
mcp_ver       := "v0.1.4"   # jawaidbazyar2/gssquared-mcp (verified via release SHA256SUMS)
gs2_ver       := "v1.0.0"   # jawaidbazyar2/gssquared
gs2_sha       := "a4ca6e0a5de1bbc4c93150dd7fd11a7c9a2e68a949101e3b9925136e4449778d"
cp2_ver       := "1.2.0"    # fadden/CiderPress2 (no published checksums)
munt_tag      := "munt_2_8_3"
munt_sha      := "a9baa120bbcdd9e5af7ed0878a32c59b378b730612459851b9acafcd76519b72"
sf2_rev       := "684543d5e5efaef08d02be50dcda8d552478fa60"   # mrbumpy409/GeneralUser-GS
sf2_sha       := "9575028c7a1f589f5770fccc8cff2734566af40cd26ed836944e9a5152688cfe"
synthfile_url := "https://mirrors.apple2.org.za/ground.icaen.uiowa.edu/apple16/gno/sounds/synthfile15.shk"
synthfile_sha := "e4e729aa9e800e5d124f1cf189f97ceea3a93cabc306ff594de6bd9db4bc679d"
ers_url       := "https://www.brutaldeluxe.fr/documentation/synthlab/apda_midisynth_synthlab_ocr.pdf"
ers_sha       := "47f7d2eb6e37ccffdb51901177ba2b3239fbe9112fd8276a6d552cf06c07e428"
manual_url    := "https://www.brutaldeluxe.fr/documentation/synthlab/synthlab_usersmanual.pdf"
manual_sha    := "54a5ec84e5fd152043a9a5b666bec0dab366656effeae5bd4a759894aa488a93"

# List recipes
default:
    @just --list

# Fetch and build everything not under source control
setup: doctor submodules sync fetch

# Check host tools (installed via your package manager)
doctor:
    #!/usr/bin/env bash
    missing=0
    for c in git gh uv curl unzip tar sha256sum cmake c++ pkg-config fluidsynth; do
      command -v "$c" >/dev/null || { echo "missing: $c"; missing=1; }
    done
    pkg-config --exists glib-2.0 || { echo "missing: glib2 development files (for mt32emu-smf2wav)"; missing=1; }
    if [ $missing = 1 ]; then
      echo "Fedora: sudo dnf install git gh uv curl unzip cmake gcc-c++ pkgconf glib2-devel fluidsynth"
      exit 1
    fi
    echo "host tools OK"
    [ -d "{{game}}" ] || echo "note: game data not found at '{{game}}' (see README)"
    [ -d "{{roms}}" ] || echo "note: no MT-32 ROMs at '{{roms}}' (needed only for MT-32 rendering)"

# ScummVM reference source (shallow submodule)
submodules:
    git submodule update --init --depth 1

# Python environment from uv.lock
sync:
    uv sync

# All downloaded/built dependencies
fetch: fetch-gssquared fetch-mcp fetch-cp2 fetch-soundfont fetch-munt fetch-synthfile fetch-docs

# GSSquared emulator AppImage
fetch-gssquared:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{gs2}}"
    if [ ! -f "$f" ] || ! echo "{{gs2_sha}}  $f" | sha256sum -c --quiet - 2>/dev/null; then
      mkdir -p "$(dirname "$f")"
      gh release download {{gs2_ver}} -R jawaidbazyar2/gssquared -p GSSquared-x86_64.AppImage -D "$(dirname "$f")" --clobber
      echo "{{gs2_sha}}  $f" | sha256sum -c -
      chmod 755 "$f"
    fi

# gs2-mcp, the GSSquared MCP server used by .mcp.json
fetch-mcp:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -x "{{vendor}}/bin/gs2-mcp" ] && exit 0
    asset=gs2-mcp-linux-amd64-{{mcp_ver}}
    mkdir -p "{{vendor}}/bin" && cd "{{vendor}}/bin"
    gh release download {{mcp_ver}} -R jawaidbazyar2/gssquared-mcp -p "$asset" -p SHA256SUMS --clobber
    grep "$asset" SHA256SUMS | sha256sum -c -
    mv "$asset" gs2-mcp && chmod 755 gs2-mcp && rm SHA256SUMS

# CiderPress II CLI (ProDOS disk images, ShrinkIt archives)
fetch-cp2:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -x "{{cp2}}" ] && exit 0
    zip=cp2_{{cp2_ver}}_linux-x64_sc.zip
    mkdir -p "{{vendor}}/cp2"
    gh release download v{{cp2_ver}} -R fadden/CiderPress2 -p "$zip" -D "{{vendor}}" --clobber
    unzip -q -o "{{vendor}}/$zip" -d "{{vendor}}/cp2" && rm "{{vendor}}/$zip"
    chmod 755 "{{cp2}}"

# GeneralUser GS soundfont (General MIDI listening tests)
fetch-soundfont:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{sf2}}"
    [ -f "$f" ] && echo "{{sf2_sha}}  $f" | sha256sum -c --quiet - && exit 0
    mkdir -p "$(dirname "$f")"
    curl -fL -o "$f" "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/{{sf2_rev}}/GeneralUser-GS.sf2"
    echo "{{sf2_sha}}  $f" | sha256sum -c -

# Munt (MT-32 emulator): build libmt32emu + mt32emu-smf2wav from source
fetch-munt:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -x "{{smf2wav}}" ] && exit 0
    d="{{vendor}}/munt"; mkdir -p "$d"
    curl -fL -o "$d/src.tar.gz" "https://github.com/munt/munt/archive/refs/tags/{{munt_tag}}.tar.gz"
    echo "{{munt_sha}}  $d/src.tar.gz" | sha256sum -c -
    rm -rf "$d/src" && mkdir "$d/src" && tar -xzf "$d/src.tar.gz" -C "$d/src" --strip-components=1
    cmake -S "$d/src" -B "$d/build" -DCMAKE_BUILD_TYPE=Release \
      -Dmunt_WITH_MT32EMU_QT=OFF -Dmunt_WITH_MT32EMU_SMF2WAV=ON \
      -DCMAKE_INSTALL_PREFIX="$PWD/$d"
    cmake --build "$d/build" -j
    cmake --install "$d/build"

# synthfile 1.5 (Dave Tribby): MIDI Synth file-format reference source
fetch-synthfile: fetch-cp2
    #!/usr/bin/env bash
    set -euo pipefail
    d="{{vendor}}/synthfile"; [ -d "$d/src" ] && exit 0
    mkdir -p "$d"
    curl -fL -o "$d/synthfile15.shk" "{{synthfile_url}}"
    echo "{{synthfile_sha}}  $d/synthfile15.shk" | sha256sum -c -
    mkdir -p "$d/src"
    "$PWD/{{cp2}}" extract --exdir="$d/src" "$d/synthfile15.shk"

# Reference PDFs: MIDI Synth ERS and synthLAB manual (Brutal Deluxe scans)
fetch-docs:
    #!/usr/bin/env bash
    set -euo pipefail
    d="{{vendor}}/docs"; mkdir -p "$d"
    get() { [ -f "$d/$1" ] && echo "$3  $d/$1" | sha256sum -c --quiet - && return 0
            curl -fL -o "$d/$1" "$2" && echo "$3  $d/$1" | sha256sum -c -; }
    get apda_midisynth_synthlab_ocr.pdf "{{ers_url}}" "{{ers_sha}}"
    get synthlab_usersmanual.pdf "{{manual_url}}" "{{manual_sha}}"

# Working copies of the System 6.0.4 disks (originals in gs/system6 stay untouched).
# The boot disk copy also gets the MIDI Synth tool (Tool035) from the synthLAB disk.
disks: fetch-cp2
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p gs/disks
    cp -n gs/system6/*.PO gs/disks/
    cp2="$PWD/{{cp2}}"
    if ! "$cp2" list gs/disks/SYSTEM.DISK.PO | grep -qi '^System:Tools:Tool035$'; then
      t=$(mktemp -d)
      "$cp2" extract --exdir="$t" --preserve=naps gs/system6/SYNTHLAB.PO System/Tools/Tool035 >/dev/null
      (cd "$t" && "$cp2" add "$OLDPWD/gs/disks/SYSTEM.DISK.PO" System >/dev/null)
      rm -rf "$t"; echo "installed Tool035 on gs/disks/SYSTEM.DISK.PO"
    fi

# GSSquared machine configs are generated into gs/disks/<name>.gs2 by `gs2-config` (migs.emu),
# keeping the id/battery RAM GSSquared saves on quit. Battery RAM is patched so the IIgs
# boots straight from the right slot with the built-in 5.25" drives off (slot 6 = Your Card).
# speed: 1.024mhz | 2.8mhz | 7.159mhz | 14.3mhz | ludicrous
seed_bram := "gs/emu/default-bram.hex"

# Boot the IIgs from floppies: System 6 + stock synthLAB (default: unlimited speed)
emu speed="ludicrous": fetch-gssquared disks
    uv run gs2-config gs/disks/synthlab.gs2 --name "MI1 synthLAB floppies" --speed {{speed}} \
      --disk 5:1=SYSTEM.DISK.PO --disk 5:2=SYNTHLAB.PO --startup-slot 5 --your-card 6 --seed {{seed_bram}}
    "{{gs2}}" "$PWD/gs/disks/synthlab.gs2"

# Boot the IIgs from the BazFast hard disk image (System 6 + synthLAB + our music)
emu-music speed="ludicrous": fetch-gssquared hdimage
    uv run gs2-config gs/disks/mi-music.gs2 --name "MI1 music (BazFast HD)" --speed {{speed}} \
      --card 7=bazfast3 --disk 7:1=MI.HD.PO --startup-slot 7 --your-card 6 --your-card 7 --seed {{seed_bram}}
    "{{gs2}}" "$PWD/gs/disks/mi-music.gs2"

# Same, from floppies (System 6 boot disk + the MI synthLAB floppy)
emu-music-floppy speed="ludicrous": fetch-gssquared disks gsdisk
    uv run gs2-config gs/disks/mi-music-floppy.gs2 --name "MI1 music (floppies)" --speed {{speed}} \
      --disk 5:1=SYSTEM.DISK.PO --disk 5:2=MI.SYNTHLAB.PO --startup-slot 5 --your-card 6 --seed {{seed_bram}}
    "{{gs2}}" "$PWD/gs/disks/mi-music-floppy.gs2"

# Render an MT-32 MIDI file to WAV with Munt (needs ROMs)
mt32-wav mid wav: fetch-munt
    "{{smf2wav}}" -m "{{roms}}" -f -o "{{wav}}" "{{mid}}"

# Render a General MIDI file to WAV with FluidSynth
gm-wav mid wav: fetch-soundfont
    fluidsynth -ni -F "{{wav}}" "{{sf2}}" "{{mid}}"

# Extract the MT-32 soundtrack: music/midi/{mt32,gm}/*.mid + music/patches/*.toml
extract:
    uv run extract-music "{{game}}" music

# Render every extracted song to WAV via both paths (music/wav/{mt32,gm}/)
render: extract fetch-munt fetch-soundfont
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p music/wav/mt32 music/wav/gm
    for f in music/midi/mt32/*.mid; do
      n=$(basename "$f" .mid)
      "{{smf2wav}}" --quiet -m "{{roms}}" -f -o "music/wav/mt32/$n.wav" "$f"
      fluidsynth -ni -q -F "music/wav/gm/$n.wav" "{{sf2}}" "music/midi/gm/$n.mid" >/dev/null
    done
    ls music/wav/*/ | head -50

# Stock synthLAB banks/waves/sample sequences, extracted from gs/system6/SYNTHLAB.PO
stock-banks: fetch-cp2
    #!/usr/bin/env bash
    set -euo pipefail
    d="{{vendor}}/synthlab"; [ -f "$d/COMBO.BNK" ] && exit 0
    rm -rf "$d.tmp" && mkdir -p "$d.tmp" "$d"
    "$PWD/{{cp2}}" extract --exdir="$d.tmp" gs/system6/SYNTHLAB.PO SEQ.AND.INSTR >/dev/null
    for f in "$d.tmp"/Seq.and.Instr/*; do cp "$f" "$d/$(basename "$f" | tr a-z A-Z)"; done
    rm -rf "$d.tmp"; ls "$d"

# Convert the GM MIDI files to MIDI Synth sequences, per export profile (gs/seq/<profile>/MInnn.SEQ)
seq: stock-banks
    rm -rf gs/seq/stock && uv run mid2seq music/midi/gm/*.mid -p stock -o gs/seq/stock -b "{{vendor}}/synthlab"

# Tuning experiment bank/sequence (gs/seq/tune/TUNE.*), see src/migs/tunetest.py
tunetest:
    uv run tunetest -o gs/seq/tune

# Boot the HD and sample the DOC oscillators to gs/disks/docscope.jsonl while you play something
docscope seconds="240": fetch-gssquared fetch-mcp hdimage
    uv run gs2-config gs/disks/mi-music.gs2 --name "MI1 music (BazFast HD)" --speed ludicrous \
      --card 7=bazfast3 --disk 7:1=MI.HD.PO --startup-slot 7 --your-card 6 --your-card 7 --seed {{seed_bram}}
    uv run gs2-docscope --config gs/disks/mi-music.gs2 --seconds {{seconds}}

# Drum-kit audition sequences (gs/seq/stock/AUDITnn.SEQ + .txt key tables) for the default bank's kits
drum-audition: stock-banks
    uv run audition "{{vendor}}/synthlab/COMBO.BNK" 0 --wave "{{vendor}}/synthlab/COMBO.WAV" -o gs/seq/stock --lo 28 --hi 100
    uv run audition "{{vendor}}/synthlab/COMBO.BNK" 9 --wave "{{vendor}}/synthlab/COMBO.WAV" -o gs/seq/stock --lo 28 --hi 100

# Build gs/disks/MI.SYNTHLAB.PO: a copy of the synthLAB disk whose /Seq.and.Instr holds
# our sequences plus the bank/wave files they use (Apple's samples removed to make room)
gsdisk: seq drum-audition
    #!/usr/bin/env bash
    set -euo pipefail
    cp2="$PWD/{{cp2}}"; img="$PWD/gs/disks/MI.SYNTHLAB.PO"; stage="$PWD/gs/disks/stage"
    mkdir -p gs/disks && rm -rf "$stage" && cp gs/system6/SYNTHLAB.PO "$img"
    "$cp2" list "$img" | grep '^Seq.and.Instr:' | tr : / | while read -r f; do "$cp2" delete "$img" "$f" >/dev/null; done
    mkdir -p "$stage/Seq.and.Instr"
    for f in gs/seq/stock/*.SEQ; do cp "$f" "$stage/Seq.and.Instr/$(basename "$f")#d50001"; done
    for b in $(uv run python -I -c "import tomllib;c=tomllib.load(open('gs/instruments.toml','rb'));print(' '.join(v['file']+' '+v['wave_file'] for v in c['banks'].values()))"); do
      case "$b" in *.BNK) t=d60001;; *.WAV) t=d80004;; esac
      cp "{{vendor}}/synthlab/$b" "$stage/Seq.and.Instr/$b#$t"
    done
    (cd "$stage" && "$cp2" add "$img" Seq.and.Instr >/dev/null)
    rm -rf "$stage"
    "$cp2" catalog "$img" | grep -E 'Seq.and.Instr|blocks free'

# 32 MB ProDOS hard disk image for BazFast (gs/disks/MI.HD.PO): System 6.0.4 merged from the
# boot disk + SystemTools1 (Finder, CDevs, DAs, FSTs, tools, fonts; not its SCSI/printer
# drivers) + SystemTools3 (sounds, fonts), Tool035, and /synthLAB with a folder per export
# profile (Stock, ...) and the tuning test (Tune).
hdimage: seq drum-audition tunetest
    #!/usr/bin/env bash
    set -euo pipefail
    cp2="$PWD/{{cp2}}"; img="$PWD/gs/disks/MI.HD.PO"; st="$PWD/gs/disks/hdstage"
    rm -rf "$st" "$img" && mkdir -p "$st/synthLAB"
    x() { "$cp2" extract --preserve=naps --overwrite --exdir="$1" "${@:2}" >/dev/null; }
    x "$st" gs/system6/SYSTEM.DISK.PO
    x "$st" gs/system6/SYSTEMTOOLS1.PO System/Finder System/CDevs System/Desk.Accs System/FSTs System/Tools System/Fonts Icons
    x "$st" gs/system6/SYSTEMTOOLS3.PO System
    x "$st" gs/system6/SYNTHLAB.PO System/Tools/Tool035
    x "$st/synthLAB" gs/system6/SYNTHLAB.PO synthLAB Icons Reference
    # A folder per export profile (sequences + the banks they reference) and the tuning test
    typed() {   # copy file $1 into dir $2 with its ProDOS type (NAPS suffix)
      case "$1" in *.SEQ) t=d50001;; *.BNK) t=d60001;; *.WAV) t=d80004;; *) return;; esac
      cp "$1" "$2/$(basename "$1")#$t"
    }
    for prof in $(uv run python -I -c "import tomllib;c=tomllib.load(open('gs/instruments.toml','rb'));print(' '.join(f'{k}:{v[\"folder\"]}:{c[\"banks\"][v[\"default_bank\"]][\"file\"]}:{c[\"banks\"][v[\"default_bank\"]][\"wave_file\"]}' for k,v in c['profiles'].items()))"); do
      IFS=: read -r name folder bnk wav <<< "$prof"
      d="$st/synthLAB/$folder"; mkdir -p "$d"
      for f in gs/seq/$name/*.SEQ; do typed "$f" "$d"; done
      for f in "{{vendor}}/synthlab/$bnk" "{{vendor}}/synthlab/$wav" "gs/seq/$name/$bnk" "gs/seq/$name/$wav"; do
        [ -f "$f" ] && typed "$f" "$d"
      done
    done
    mkdir -p "$st/synthLAB/Tune"; for f in gs/seq/tune/*; do typed "$f" "$st/synthLAB/Tune"; done
    "$cp2" create-disk-image "$img" 32m prodos >/dev/null
    "$cp2" rename "$img" : MI.HD >/dev/null
    (cd "$st" && "$cp2" add "$img" * >/dev/null)
    rm -rf "$st"
    "$cp2" catalog "$img" | grep -E '^Disk|blocks free'
    "$cp2" list "$img" | grep -c . | sed 's/^/files: /'

# Run the unit tests
test *args:
    uv run pytest {{args}}
