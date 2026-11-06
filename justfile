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
fetch: fetch-gssquared fetch-mcp fetch-cp2 fetch-soundfont fetch-munt fetch-synthfile

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

# Working copies of the System 6.0.4 disks (originals in gs/system6 stay untouched)
disks:
    mkdir -p gs/disks
    cp -n gs/system6/*.PO gs/disks/

# Boot the IIgs in GSSquared with System 6 + synthLAB in the 3.5" drives
emu: fetch-gssquared disks
    "{{gs2}}" -p 5 -ds5d1="$PWD/gs/disks/SYSTEM.DISK.PO" -ds5d2="$PWD/gs/disks/SYNTHLAB.PO"

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

# Run the unit tests
test *args:
    uv run pytest {{args}}
