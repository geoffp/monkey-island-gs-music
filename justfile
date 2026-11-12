# SCUMM MT-32 soundtracks → Apple IIGS (NinjaTrackerPlus; MIDI Synth as an experimental aside)
# Run `just` to list recipes. `just setup` fetches everything not under source control,
# except what you must supply yourself (see README: game data, MT-32 ROMs).

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# The game to build: its settings are config/games/<game>/ (game.toml, ntp.toml, ...), its
# output build/<game>/. Choose one with `just game=<id> <recipe>` or MIGS_GAME=<id>; exported,
# so nested `just` runs and the Python tools (--game's default) see the same game.
game     := env("MIGS_GAME", "monkey")
export MIGS_GAME := game
gconf    := "config/games" / game
out      := "build" / game
_gv      := 'python3 -I tools/game.py "$1" "$2"'
# Builds run at a lower CPU priority (nice 10; MIGS_NICE=0 for full speed), so audio and the
# emulator keep up while they run. The emulator recipes stay at normal priority. `lowprio`
# renices a bash recipe's own shell, and everything it starts inherits that. util-linux renice
# sets an absolute value with -n, BSD/macOS adds it, so a shell already at the level or lower is left alone.
build_nice := env("MIGS_NICE", "10")
nice       := "nice -n " + build_nice
lowprio    := '[ "$(ps -o ni= -p $$)" -ge ' + build_nice + ' ] || renice -n ' + build_nice + ' -p $$ >/dev/null'
data     := "original files" / shell(_gv, game, "data")      # the game's files (read only)
prefix   := shell(_gv, game, "prefix")   # song files MI150.mod, disk volumes MI.NTP
label    := shell(_gv, game, "label")    # titles: "MI1 NinjaTrackerPlus"
roms     := "original files/mt-32 roms"
vendor   := "vendor"
# Platform: os() linux or macos, arch() x86_64 or aarch64. The prebuilt tools: GSSquared for
# Linux x86_64 (AppImage) and macOS (universal app); CiderPress II for Linux x86_64 and macOS
# (x86_64 only: Rosetta 2 on Apple silicon); gs2-mcp for Linux and macOS.
platform := os() + "-" + arch()
gs2      := if os() == "macos" { vendor / "gssquared/GSSquared.app/Contents/MacOS/GSSquared" } else { vendor / "gssquared/GSSquared-x86_64.AppImage" }
cp2      := vendor / "cp2/cp2"
cp2_zip  := if platform == "linux-x86_64" { "linux-x64_sc" } else if os() == "macos" { "osx-x64_sc" } else { "" }
smf2wav  := vendor / "munt/bin/mt32emu-smf2wav"
sf2      := vendor / "soundfonts/GeneralUser-GS.sf2"

# Pinned versions / checksums (sha256)
mcp_ver       := "v0.1.4"   # jawaidbazyar2/gssquared-mcp (hashes from its release SHA256SUMS)
mcp_asset     := if platform == "linux-x86_64" { "linux-amd64" } else if platform == "linux-aarch64" { "linux-arm64" } else if platform == "macos-x86_64" { "darwin-amd64" } else if platform == "macos-aarch64" { "darwin-arm64" } else { "" }
mcp_sha       := if mcp_asset == "linux-amd64" { "20867888552a2e854287bd6dd3828f6ddfd76573d667d846f6c72c3b40ca5e27" } else if mcp_asset == "linux-arm64" { "98ce4b2f33b918de8c90c229889c29413eed547c6b4dbd5c8431e6ff1e18ab63" } else if mcp_asset == "darwin-amd64" { "9c43d88b664a19256d1ef6edc85132daf9a1703fab3d563795c1eefdcb2a5a18" } else { "adf40bb1d6439364fdaf11580df9ecc4d462e1f90207d98cd6568fd7b1220bc7" }
gs2_ver       := "v1.0.0"   # jawaidbazyar2/gssquared
gs2_sha       := "a4ca6e0a5de1bbc4c93150dd7fd11a7c9a2e68a949101e3b9925136e4449778d"   # AppImage
gs2_dmg       := "gssquared-1.0.1953-Darwin.dmg"
gs2_dmg_sha   := "96d476f15291bd92f6b676b4089e642327320f2eaaab975ef30777c43b9fc15b"
cp2_ver       := "1.2.0"    # fadden/CiderPress2 (no published checksums: pinned here)
cp2_sha       := if cp2_zip == "linux-x64_sc" { "8c8748a0b484b4a778354146ffe4b6b44527f066b440a2e7bccebf4050948981" } else { "a1b9d98adb7ca020d103a271eea67025e2eb03f7aa132907d5b8d4471ddc93c6" }
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
ntpsrc_sha    := "461260e35dc0b10176c05865d01c4e31694f704c0c3a9e5760fc093623dc52fb"
ntpdemo_sha   := "350843388bde213b10101212fcd6e3f5affa3b4f25b74ea970b0b6ab85d14d97"
# NTP builds made side by side (build/<game>/ntp/<variant>/), each a [modes.<name>] in the game's ntp.toml
# with a sample-set `base`: inst-per-song (per-song: each song carries only its own sounds,
# longer loops) and inst-shared (shared: one set for every song, like the MIDI Synth bank), plus
# any trial variants.
ntp_modes     := "inst-per-song inst-shared"
# The helpers below expand to `f() {...}; f`: use them as whole commands (a line, or inside
# if/then), never after `||` or `&&`, which would skip only the definition.
# Copy build/<game>/ntp/<mode>/*.ntp (typed $D5/$0008) into <dir>/<MODE>/, one folder per variant.
# ProDOS names allow no "-" (and 15 characters): inst-per-song -> INST.PER.SONG.
ntp_folders   := 'ntpf() { local m d f; for m in ' + ntp_modes + '; do d="$1/$(echo $m | tr a-z- A-Z.)"; for f in ' + out + '/ntp/$m/*.ntp; do [ -f "$f" ] || continue; mkdir -p "$d"; cp "$f" "$d/$(basename "$f" .ntp | tr a-z A-Z).NTP#d50008"; done; done; }; ntpf'
# Add host tree $3 (relative to cwd) to ProDOS image $2 with cp2 $1, in sorted order: folders
# first (cp2 mkdir), then each folder's files. SenseiPlay lists directory order, and `cp2 add` of
# a tree would follow the host's readdir order. SenseiPlay v2.0 also never shows the first entry
# of a directory block after the first (src/p8lib.s treats every block's first slot as the
# header): entries 13, 26, 39, ... of a folder. A placeholder text file fills those slots.
# `cp2 add` sorts the files of one call, so each run between placeholders is its own call.
p8_tree       := 'p8t() { local cp2=$1 img=$2 d f k g fs; for d in $(find "$3" -type d | sort); do "$cp2" mkdir "$img" "$d" >/dev/null; done; for d in $(find "$3" -type d | sort); do k=$(find "$d" -mindepth 1 -maxdepth 1 -type d | wc -l); fs=(); for f in $(find "$d" -maxdepth 1 -type f | sort); do if [ $k -ge 12 ] && [ $(( (k - 12) % 13 )) -eq 0 ]; then g="$d/GAP.$((k + 1))#040000"; echo "Placeholder: SenseiPlay v2.0 skips the first entry of each later directory block." > "$g"; [ ${#fs[@]} -eq 0 ] || "$cp2" add "$img" "${fs[@]}" >/dev/null; "$cp2" add "$img" "$g" >/dev/null; fs=(); k=$((k + 1)); fi; fs+=("$f"); k=$((k + 1)); done; [ ${#fs[@]} -eq 0 ] || "$cp2" add "$img" "${fs[@]}" >/dev/null; done; }; p8t'
# What an NTP build reads (`ntpmod` adds the variant name): ntpmod and the migs modules it
# imports, the game's settings it reads (game.toml: names, MT-32 set-up; ntp.toml; mt32sounds.toml), the MT-32 MIDI
# and its custom timbres (timbres.toml, v5 games),
# the PHP period tables, the Munt binary and the lockfile. (Not the disk README or midisynth.toml.)
ntp_inputs    := "py:src/migs/ntpmod.py " + gconf + "/game.toml " + gconf + "/ntp.toml opt:" + gconf + "/mt32sounds.toml " + out + "/extracted/midi/mt32 opt:" + out + "/extracted/timbres.toml " + vendor + "/ntp/src " + smf2wav + " pyproject.toml uv.lock"
# Build ProDOS image $1 (volume $2) from staging dir $3 at the smallest power-of-two size that
# holds it (1, 2, ... 32 MB, the ProDOS limit): `eval $4` (run in $3) fills it; an attempt counts
# when every staged entry is on the image (bash ignores errexit inside conditions, so the count
# decides; a NAPS resource fork, NAME#tttaaaar, is part of its file's entry). p8_tree's GAP placeholders are made anew each time. Odd sizes would work too
# (CFFA3000, Floppy Emu SmartPort mode and emulators size block devices from the file), but a
# power of two leaves room to copy a file on by hand.
fit_image     := 'fiti() { local img=$1 vol=$2 st=$3 fill=$4 size want got; for size in 1 2 4 8 16 32; do rm -f "$img"; find "$st" -name "GAP.*" -delete; "$cp2" create-disk-image "$img" ${size}m prodos >/dev/null; "$cp2" rename "$img" : "$vol" >/dev/null; (cd "$st" && eval "$fill") >/dev/null 2>&1 || true; want=$(find "$st" -mindepth 1 ! -regex ".*#[0-9a-fA-F]\{6\}r" | wc -l); got=$("$cp2" list "$img" 2>/dev/null | grep -c . || true); if [ "$got" -ge "$want" ]; then echo "$(basename "$img"): $size MB"; return 0; fi; done; echo "$(basename "$img"): $want entries do not fit in 32 MB"; return 1; }; fiti'
# Distribution archives of image $1 in build/<game>/dist/: <volume>.zip holds the image itself (for
# emulators, CFFA3000, Floppy Emu), <volume>.SHK the volume's files with their ProDOS types (to
# unpack with ShrinkIt on a IIgs). cp2 makes .SDK (ShrinkIt disk) archives only of floppy sizes.
dist_pack     := 'distp() { local img=$1 vol d="$PWD/' + out + '/dist"; vol=$(basename "$img" .PO); mkdir -p "$d"; rm -f "$d/$vol.zip" "$d/$vol.SHK"; "$cp2" create-file-archive "$d/$vol.zip" >/dev/null; (cd "$(dirname "$img")" && "$cp2" add "$d/$vol.zip" "$(basename "$img")" >/dev/null); "$cp2" create-file-archive "$d/$vol.SHK" >/dev/null; "$cp2" copy --recurse "$img" "*" "$d/$vol.SHK" >/dev/null; echo "' + out + '/dist/$vol.zip: $(( $(wc -c < "$d/$vol.zip") / 1024 )) KB, ' + out + '/dist/$vol.SHK: $(( $(wc -c < "$d/$vol.SHK") / 1024 )) KB"; }; distp'
sensei_ver    := "v2.0"     # digarok/senseiplay (no published checksums: pinned here)
sensei_sha    := "0965c0f8d45314910f8d20f06ef395006415314fbc2cabedf4d0a221d184c07f"
# Apple IIGS System 6.0.4 (community release, 2016-12-31), 800K ProDOS-order images from the
# Asimov archive, the location given in the release announcement (docs/sources.md). Not
# redistributable here, so fetched: image name on the archive = sha256.
s6_url        := "https://www.apple.asimov.net/images/gs/os/gsos/Apple_IIGS_System_6.0.4/PO%20Disk%20Images"
s6            := vendor / "system6"
s6_files      := "System.Disk:496f8d80d524ded42398e7b24f62694a1562d4bbe5d933eacbf391f3a26b599a SystemTools1:25c1f33fea4e6bb983a1d2a2b3234dbb7c14913e2f922a2587d28c7ff42c9944 SystemTools3:b41cc66963f8490f61854c85c4ba14c4b31b63b96a5f2ef666a19c4ecff48859 synthLAB:ddebd51f90a709a109c8d310159a112726bb6626504c1f5e78b59499e64d7087"
# Freshness checks (tools/fresh.py; its docstring has the details):
#   fresh get URL FILE SHA256                    download a pinned file unless it's already there
#   fresh run STAMP [--out PATH]... INPUT... -- CMD   run CMD unless its inputs are unchanged
fresh         := "python3 tools/fresh.py"
gh            := "https://github.com"
# Internet Archive copies (byte-identical to the pins), tried when an unversioned source fails
# or changes. Add one with: curl "https://archive.org/wayback/available?url=URL"
wb            := "https://web.archive.org/web"

# List recipes
default:
    @just --list

# Fetch and build everything not under source control
setup: doctor submodules sync fetch stock-banks

# Check host tools (installed via your package manager)
doctor:
    #!/usr/bin/env bash
    missing=()
    need="git uv python3 curl unzip tar cmake c++ pkg-config php"
    for c in $need; do command -v "$c" >/dev/null || missing+=("$c"); done
    pkg-config --exists glib-2.0 2>/dev/null || missing+=("glib2 (development files, for Munt)")
    if [ "{{platform}}" = macos-aarch64 ] && ! arch -x86_64 /usr/bin/true 2>/dev/null; then
      missing+=("Rosetta 2, for CiderPress II (softwareupdate --install-rosetta --agree-to-license)")
    fi
    if [ ${#missing[@]} -gt 0 ]; then
      printf 'missing: %s\n' "${missing[@]}"
      case "{{os()}}" in
        macos)   echo "macOS (Homebrew): brew install uv cmake pkg-config glib php ffmpeg fluid-synth";;
        *) if command -v dnf >/dev/null; then echo "Fedora: sudo dnf install git uv curl unzip cmake gcc-c++ pkgconf glib2-devel php-cli ffmpeg fluidsynth"
           elif command -v apt-get >/dev/null; then echo "Debian/Ubuntu: sudo apt install git curl unzip cmake g++ pkg-config libglib2.0-dev php-cli ffmpeg fluidsynth (uv: https://docs.astral.sh/uv/)"
           fi;;
      esac
      exit 1
    fi
    echo "host tools OK"
    # optional: song previews and the General MIDI renders
    ffmpeg -hide_banner -demuxers 2>/dev/null | grep libopenmpt >/dev/null || echo "note: no ffmpeg with libopenmpt: MOD previews (build/<game>/ntp/<mode>/wav/) are skipped"
    command -v fluidsynth >/dev/null || echo "note: no fluidsynth: \`just render\` and \`just gm-wav\` won't run"
    [ -d "{{data}}" ] || echo "note: game data for {{game}} not found at '{{data}}' (see README)"
    [ -d "{{roms}}" ] || echo "note: no MT-32 ROMs at '{{roms}}' (needed only for MT-32 rendering)"

# ScummVM reference source (shallow submodule)
submodules:
    git submodule update --init --depth 1

# Python environment from uv.lock
sync:
    uv sync

# All downloaded/built dependencies
fetch: fetch-gssquared fetch-mcp fetch-cp2 fetch-soundfont fetch-munt fetch-synthfile fetch-docs fetch-ntp fetch-system6

# GSSquared emulator (Linux x86_64 AppImage, macOS app; none for other platforms)
fetch-gssquared:
    #!/usr/bin/env bash
    set -euo pipefail
    rel={{gh}}/jawaidbazyar2/gssquared/releases/download/{{gs2_ver}}
    case "{{platform}}" in
      linux-x86_64)
        {{fresh}} get $rel/GSSquared-x86_64.AppImage "{{gs2}}" {{gs2_sha}}; chmod 755 "{{gs2}}";;
      macos-*)
        [ -x "{{gs2}}" ] && exit 0
        d="{{vendor}}/gssquared"; {{fresh}} get $rel/{{gs2_dmg}} "$d/{{gs2_dmg}}" {{gs2_dmg_sha}}
        m=$(mktemp -d); hdiutil attach -quiet -nobrowse -readonly -mountpoint "$m" "$d/{{gs2_dmg}}"
        rm -rf "$d/GSSquared.app"; cp -R "$m/GSSquared.app" "$d/"; hdiutil detach -quiet "$m"; rm "$d/{{gs2_dmg}}";;
      *) echo "GSSquared: no build for {{platform}} (the emulator recipes won't run; everything else does)";;
    esac

# gs2-mcp, the GSSquared MCP server used by .mcp.json
fetch-mcp:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -n "{{mcp_asset}}" ] || { echo "gs2-mcp: no build for {{platform}}"; exit 0; }
    {{fresh}} get {{gh}}/jawaidbazyar2/gssquared-mcp/releases/download/{{mcp_ver}}/gs2-mcp-{{mcp_asset}}-{{mcp_ver}} "{{vendor}}/bin/gs2-mcp" {{mcp_sha}}
    chmod 755 "{{vendor}}/bin/gs2-mcp"

# Run gs2-mcp over stdio (.mcp.json launches this, so it needs no absolute paths).
# No dependencies: stdout is the MCP channel and must carry nothing else.
[no-exit-message]
mcp:
    @GS2_BIN="{{justfile_directory()}}/{{gs2}}" exec "{{justfile_directory()}}/{{vendor}}/bin/gs2-mcp"

# CiderPress II CLI (ProDOS disk images, ShrinkIt archives)
fetch-cp2:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -x "{{cp2}}" ] && exit 0
    [ -n "{{cp2_zip}}" ] || { echo "CiderPress II: no build for {{platform}} (the disk recipes won't run)"; exit 0; }
    zip=cp2_{{cp2_ver}}_{{cp2_zip}}.zip
    mkdir -p "{{vendor}}/cp2"
    {{fresh}} get {{gh}}/fadden/CiderPress2/releases/download/v{{cp2_ver}}/$zip "{{vendor}}/$zip" {{cp2_sha}}
    unzip -q -o "{{vendor}}/$zip" -d "{{vendor}}/cp2" && rm "{{vendor}}/$zip"
    chmod 755 "{{cp2}}"

# GeneralUser GS soundfont (General MIDI listening tests)
fetch-soundfont:
    @{{fresh}} get "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/{{sf2_rev}}/GeneralUser-GS.sf2" "{{sf2}}" {{sf2_sha}}

# Munt (MT-32 emulator): build libmt32emu + mt32emu-smf2wav from source (again when munt_tag changes)
fetch-munt:
    @{{fresh}} run "{{vendor}}/munt/.inputs" --out "{{smf2wav}}" "text:{{munt_tag}} {{munt_sha}}" -- just _build-munt

_build-munt:
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    d="{{vendor}}/munt"
    {{fresh}} get {{gh}}/munt/munt/archive/refs/tags/{{munt_tag}}.tar.gz "$d/src.tar.gz" {{munt_sha}}
    rm -rf "$d/src" "$d/build" && mkdir "$d/src" && tar -xzf "$d/src.tar.gz" -C "$d/src" --strip-components=1
    cmake -S "$d/src" -B "$d/build" -DCMAKE_BUILD_TYPE=Release \
      -Dmunt_WITH_MT32EMU_QT=OFF -Dmunt_WITH_MT32EMU_SMF2WAV=ON \
      -DCMAKE_INSTALL_PREFIX="$PWD/$d"
    cmake --build "$d/build" -j {{num_cpus()}}   # a bare -j is GNU make only
    cmake --install "$d/build"

# synthfile 1.5 (Dave Tribby): MIDI Synth file-format reference source
fetch-synthfile: fetch-cp2
    #!/usr/bin/env bash
    set -euo pipefail
    d="{{vendor}}/synthfile"; [ -d "$d/src" ] && exit 0
    mkdir -p "$d"
    {{fresh}} get "{{synthfile_url}}" "$d/synthfile15.shk" {{synthfile_sha}} "{{wb}}/20240709014101id_/{{synthfile_url}}"
    mkdir -p "$d/src"
    "$PWD/{{cp2}}" extract --exdir="$d/src" "$d/synthfile15.shk"

# NinjaTrackerPlus: player sources + MOD->NTP converter, demo disk, SenseiPlay player disk
fetch-ntp:
    #!/usr/bin/env bash
    set -euo pipefail
    d="{{vendor}}/ntp"
    nf=http://www.ninjaforce.com/downloads
    {{fresh}} get $nf/ntpsources.zip "$d/ntpsources.zip" {{ntpsrc_sha}} "{{wb}}/20251219162829id_/$nf/ntpsources.zip"
    {{fresh}} get $nf/ntp.2mg "$d/ntp.2mg" {{ntpdemo_sha}} "{{wb}}/20251101061135id_/$nf/ntp.2mg"
    {{fresh}} get {{gh}}/digarok/senseiplay/releases/download/{{sensei_ver}}/senseiplay800.2mg "$d/senseiplay800.2mg" {{sensei_sha}}
    [ -d "$d/src" ] || unzip -q -o "$d/ntpsources.zip" -d "$d/src"

# Apple IIGS System 6.0.4 disks used by the synthLAB disk and stock-banks -> vendor/system6/
fetch-system6:
    #!/usr/bin/env bash
    set -euo pipefail
    for e in {{s6_files}}; do
      n=${e%%:*}; {{fresh}} get "{{s6_url}}/$n.po" "{{s6}}/$n.po" "${e#*:}"
    done

# Reference PDFs: MIDI Synth ERS and synthLAB manual (Brutal Deluxe scans)
# (reading only, nothing builds from them: a failed download warns instead of failing setup)
fetch-docs:
    @{{fresh}} get "{{ers_url}}" "{{vendor}}/docs/apda_midisynth_synthlab_ocr.pdf" {{ers_sha}} || echo "warning: skipped the MIDI Synth ERS PDF (download failed); the build doesn't need it"
    @{{fresh}} get "{{manual_url}}" "{{vendor}}/docs/synthlab_usersmanual.pdf" {{manual_sha}} "{{wb}}/20260514232548id_/{{manual_url}}" || echo "warning: skipped the synthLAB manual PDF (download failed); the build doesn't need it"

# GSSquared machine configs are generated into build/<game>/disks/<name>.gs2 by `gs2-config` (migs.gs2config),
# keeping the id/battery RAM GSSquared saves on quit. Battery RAM is patched so the IIgs
# boots straight from the right slot with the built-in 5.25" drives off (slot 6 = Your Card).
# speed: 1.024mhz | 2.8mhz | 7.159mhz | 14.3mhz | ludicrous. Default 7.159mhz (since 2026-11-11):
# an accelerated IIgs (2.8 MHz is stock), rather than ludicrous's unlimited speed.
emu_speed := "7.159mhz"
seed_bram := "config/emu/default-bram.hex"

# From the NTP disk (`just disk`) on a BazFast card, in GSSquared:
# Boot the IIgs straight into SenseiPlay with our NinjaTrackerPlus songs
emu speed=emu_speed: fetch-gssquared disk
    uv run gs2-config {{out}}/disks/{{lowercase(prefix)}}-ntp.gs2 --name "{{label}} NinjaTrackerPlus (SenseiPlay)" --speed {{speed}} \
      --card 7=bazfast3 --disk 7:1={{prefix}}.NTP.PO --startup-slot 7 --your-card 6 --your-card 7 --seed {{seed_bram}}
    @[ -x "{{gs2}}" ] || { echo "no GSSquared for {{platform}}"; exit 1; }
    "{{gs2}}" "$PWD/{{out}}/disks/{{lowercase(prefix)}}-ntp.gs2"

# Experimental aside: boot System 6 + synthLAB with the MIDI Synth sequences (`just synthlab-disk`)
emu-synthlab speed=emu_speed: fetch-gssquared synthlab-disk
    uv run gs2-config {{out}}/disks/{{lowercase(prefix)}}-synthlab.gs2 --name "{{label}} synthLAB (experimental, BazFast HD)" --speed {{speed}} \
      --card 7=bazfast3 --disk 7:1={{prefix}}.SYNTHLAB.PO --startup-slot 7 --your-card 6 --your-card 7 --seed {{seed_bram}}
    @[ -x "{{gs2}}" ] || { echo "no GSSquared for {{platform}}"; exit 1; }
    "{{gs2}}" "$PWD/{{out}}/disks/{{lowercase(prefix)}}-synthlab.gs2"

# ProDOS 8 image for BazFast, sized to the smallest power of two that holds it (4 MB in 2026-11):
# SenseiPlay (its PRODOS + player) and our .NTP songs in /songs,
# one folder per build variant.
# Build the NinjaTrackerPlus disk, build/<game>/disks/<prefix>.NTP.PO
disk: ntp
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    songs=(); for m in {{ntp_modes}}; do songs+=({{out}}/ntp/$m/*.ntp); done
    v={{prefix}}.NTP
    {{fresh}} run {{out}}/disks/$v.inputs --out {{out}}/disks/$v.PO --out {{out}}/dist/$v.zip --out {{out}}/dist/$v.SHK \
      "${songs[@]}" "{{vendor}}/ntp/senseiplay800.2mg" {{gconf}}/disk/README.TXT py:src/migs/diskreadme.py "text:{{ntp_modes}}" -- just _disk

_disk:
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    cp2="$PWD/{{cp2}}"; img="$PWD/{{out}}/disks/{{prefix}}.NTP.PO"; st="$PWD/{{out}}/disks/ntpstage"
    rm -rf "$st" "$img" && mkdir -p "$st"
    "$cp2" extract --preserve=naps --overwrite --exdir="$st" "{{vendor}}/ntp/senseiplay800.2mg" \
      PRODOS sensei.system ntpplayer dgr.radio.dgr >/dev/null
    {{ntp_folders}} "$st/songs"
    # what the variant folders are: the game's template, filled in from the staged songs
    # (count, file names, placeholders); ProDOS TXT, CR line ends
    r=$(uv run disk-readme --game {{game}} "$st/songs" "$st/README.lf"); echo "$r"
    ! LC_ALL=C grep -n '[^ -~]' "$st/README.lf" || { echo "{{gconf}}/disk/README.TXT: only printable 7-bit ASCII (no tabs, no accents or curly quotes)"; exit 1; }
    tr '\n' '\r' < "$st/README.lf" > "$st/README.TXT#040000"; rm "$st/README.lf"
    # songs first on the disk, so it's the first entry in SenseiPlay
    {{fit_image}} "$img" {{prefix}}.NTP "$st" '{{p8_tree}} "$cp2" "$img" songs; "$cp2" add "$img" README.TXT* PRODOS* sensei.system* ntpplayer* dgr.radio.dgr*'
    # the README names the placeholders p8_tree made (diskreadme.gap_names follows its rule)
    want=$(echo "$r" | sed -n 's/^placeholders: //p'); got=$("$cp2" list "$img" | sed -n 's/.*:\(GAP\.[0-9]*\)$/\1/p' | sort -u -t. -k2n | tr '\n' ' ' | sed 's/ $//')
    [ "${got:-none}" = "$want" ] || { echo "README names placeholders '$want', the image has '${got:-none}'"; exit 1; }
    rm -rf "$st"
    {{dist_pack}} "$img"
    "$cp2" catalog "$img" | head -12

# Render an MT-32 MIDI file to WAV with Munt (needs ROMs)
mt32-wav mid wav: fetch-munt
    {{nice}} "{{smf2wav}}" -m "{{roms}}" -f -o "{{wav}}" "{{mid}}"

# Render a General MIDI file to WAV with FluidSynth
gm-wav mid wav: fetch-soundfont
    {{nice}} fluidsynth -ni -F "{{wav}}" "{{sf2}}" "{{mid}}"

# Extract the MT-32 soundtrack: build/<game>/extracted/midi/{mt32,gm}/*.mid + .../extracted/patches/*.toml
extract:
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    [ -d "{{data}}" ] || { echo "no game data for {{game}} in '{{data}}' (see original files/README.md)"; exit 1; }
    {{fresh}} run {{out}}/extracted/.inputs --out {{out}}/extracted/midi/mt32 \
      "{{data}}" {{gconf}}/game.toml opt:{{gconf}}/song_names.toml py:src/migs/extract_music.py pyproject.toml uv.lock \
      -- uv run extract-music --game {{game}} "{{data}}" {{out}}/extracted

# Render every extracted song to WAV via both paths (build/<game>/extracted/wav/{mt32,gm}/)
render: extract fetch-munt fetch-soundfont
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    m={{out}}/extracted
    mkdir -p $m/wav/mt32 $m/wav/gm
    for f in $m/midi/mt32/*.mid; do
      n=$(basename "$f" .mid)
      "{{smf2wav}}" --quiet -m "{{roms}}" -f -o "$m/wav/mt32/$n.wav" "$f"
      fluidsynth -ni -q -F "$m/wav/gm/$n.wav" "{{sf2}}" "$m/midi/gm/$n.mid" >/dev/null
    done
    ls $m/wav/*/ | head -50

# Stock synthLAB banks/waves/sample sequences, extracted from System 6.0.4's synthLAB disk
stock-banks: fetch-cp2 fetch-system6
    #!/usr/bin/env bash
    set -euo pipefail
    d="{{vendor}}/synthlab"; [ -f "$d/COMBO.BNK" ] && exit 0
    rm -rf "$d.tmp" && mkdir -p "$d.tmp" "$d"
    "$PWD/{{cp2}}" extract --exdir="$d.tmp" "{{s6}}/synthLAB.po" SEQ.AND.INSTR >/dev/null
    for f in "$d.tmp"/Seq.and.Instr/*; do cp "$f" "$d/$(basename "$f" | tr a-z A-Z)"; done
    rm -rf "$d.tmp"; ls "$d"

# Convert the GM MIDI files to MIDI Synth sequences, per export profile (build/<game>/seq/<profile>/MInnn.SEQ)
# Only the songs' own sequences are replaced: seq/stock/ also holds the drum auditions
# (AUDITnn.SEQ, `midisynth-drum-audition`) and seq/mt32/ the bank.
seq: stock-banks mt32bank
    rm -f {{out}}/seq/stock/{{prefix}}[0-9]*.SEQ && {{nice}} uv run mid2seq --game {{game}} {{out}}/extracted/midi/gm/*.mid -p stock -o {{out}}/seq/stock -b "{{vendor}}/synthlab"
    rm -f {{out}}/seq/mt32/{{prefix}}[0-9]*.SEQ && {{nice}} uv run mid2seq --game {{game}} {{out}}/extracted/midi/mt32/*.mid -p mt32 -o {{out}}/seq/mt32 -b {{out}}/seq/mt32

# Sample the MT-32 (Munt) into the game's bank (MI1) per its mt32sounds.toml -> build/<game>/seq/mt32/MI1.{BNK,WAV,map.toml}
mt32bank: extract fetch-munt
    {{nice}} uv run mt32bank --game {{game}} {{out}}/extracted/midi/mt32/*.mid -o {{out}}/seq/mt32

# Estimated MIDI Synth voice pressure per sequence (voices free only when envelopes reach 0)
midisynth-voices: seq
    @echo "== stock"; uv run midisynth-voices {{out}}/seq/stock/{{prefix}}*.SEQ -b "{{vendor}}/synthlab"
    @echo "== mt32";  uv run midisynth-voices {{out}}/seq/mt32/{{prefix}}*.SEQ -b {{out}}/seq/mt32

# MT-32 MIDI -> ProTracker MODs within NinjaTrackerPlus limits (build/<game>/ntp/<mode>/MInnn.mod), for
# each sample-set mode (default both), plus libopenmpt previews in .../ntp/<mode>/wav/. Config: the game's ntp.toml
ntpmod modes=ntp_modes: extract fetch-ntp fetch-munt
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    # variants build concurrently, each only if what it reads changed (logs in build/<game>/ntp/<mode>.log)
    # (the build recipe's own text counts too: changing how it runs rebuilds)
    n={{out}}/ntp; mkdir -p $n; pids=(); recipe=$(just --show _ntpmod-one)
    for m in {{modes}}; do
      {{fresh}} run "$n/$m/.inputs" {{ntp_inputs}} "text:$m" "text:$recipe" -- just _ntpmod-one "$m" > "$n/$m.log.new" 2>&1 & pids+=($!)
    done
    fail=0; for p in "${pids[@]}"; do wait "$p" || fail=1; done
    # a variant that was up to date keeps the log of the build that made it
    for m in {{modes}}; do
      if [ "$(cat "$n/$m.log.new")" = "$n/$m: up to date" ] && [ -f "$n/$m.log" ]; then
        echo "== samples: $m: up to date (its build log: $n/$m.log)"; rm "$n/$m.log.new"
      else
        mv "$n/$m.log.new" "$n/$m.log"; echo "== samples: $m"; cat "$n/$m.log"
      fi
    done
    exit $fail

# One variant's MODs and their previews (all cores)
_ntpmod-one mode:
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    d="{{out}}/ntp/{{mode}}"; rm -rf "$d" && mkdir -p "$d/wav"
    uv run ntpmod --game {{game}} {{out}}/extracted/midi/mt32/*.mid -o "$d" --samples "{{mode}}"
    # (not grep -q: it exits at the first match, ffmpeg dies of SIGPIPE, and pipefail fails the test)
    if ffmpeg -hide_banner -demuxers 2>/dev/null | grep libopenmpt >/dev/null; then
      ls $d/*.mod | xargs -P {{num_cpus()}} -I{} sh -c \
        'ffmpeg -hide_banner -loglevel error -y -f libopenmpt -i "$1" -ac 2 -ar 44100 "$2/wav/$(basename "$1" .mod).wav"' _ {} "$d"
      echo "previews: $d/wav/"
    else
      echo "previews: skipped (no ffmpeg with libopenmpt)"
    fi

# Rebuild the MODs even if their inputs are unchanged (so does `just clean`)
ntpmod-force modes=ntp_modes:
    for m in {{modes}}; do rm -f "{{out}}/ntp/$m/.inputs"; done
    just ntpmod "{{modes}}"

# Loop lab (research, not part of the build): attack detection + loop-construction candidates
# measured on the MT-32 sample set -> build/<game>/looplab/ (report.md, wav/, plots/). docs/sample-construction.md
looplab mode="shared": fetch-ntp fetch-munt
    {{nice}} uv run --group lab looplab --game {{game}} {{out}}/extracted/midi/mt32/*.mid --samples "{{mode}}"

# MOD -> .NTP with Ninjaforce's converter (needs php-cli). Streaming forbidden: everything in DOC RAM.
# Converts only MODs newer than their .ntp (ntpmod rewrites a mode's folder when it rebuilds it).
ntp modes=ntp_modes: (ntpmod modes)
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    command -v php >/dev/null || { echo "php not found (see just doctor)"; exit 1; }
    for m in {{modes}}; do
      # all cores; a failed conversion prints its log and stops the batch (xargs exit 255)
      ls {{out}}/ntp/$m/*.mod | xargs -P {{num_cpus()}} -I{} sh -c \
        '[ "${2%.mod}.ntp" -nt "$2" ] && exit 0; php "$1" "$2" STREAM_FORBIDDEN > "${2%.mod}.log" || { echo "$2:"; cat "${2%.mod}.log"; exit 255; }' \
        _ "{{vendor}}/ntp/src/ntpconverter.php" {}
      echo "$m: $(ls {{out}}/ntp/$m/*.ntp | wc -l) .NTP files in {{out}}/ntp/$m/"
      case "$m" in *shared*)      # resident bank: identical instruments + samples in every song
        uv run ntpcheck --shared {{out}}/ntp/$m/*.ntp | tail -1;; esac
    done

# Tuning experiment bank/sequence (build/<game>/seq/tune/TUNE.*, for the synthLAB disk), see src/migs/midisynth_tunetest.py
midisynth-tunetest:
    uv run midisynth-tunetest -o {{out}}/seq/tune

# Boot the NTP disk and sample the DOC oscillators to build/<game>/disks/docscope.jsonl while you play something
docscope seconds="240": fetch-gssquared fetch-mcp disk
    uv run gs2-config {{out}}/disks/{{lowercase(prefix)}}-ntp.gs2 --name "{{label}} NinjaTrackerPlus (SenseiPlay)" --speed {{emu_speed}} \
      --card 7=bazfast3 --disk 7:1={{prefix}}.NTP.PO --startup-slot 7 --your-card 6 --your-card 7 --seed {{seed_bram}}
    uv run gs2-docscope --config {{out}}/disks/{{lowercase(prefix)}}-ntp.gs2 --seconds {{seconds}} --log {{out}}/disks/docscope.jsonl

# Drum-kit audition sequences (build/<game>/seq/stock/AUDITnn.SEQ + .txt key tables) for the default bank's kits
midisynth-drum-audition: stock-banks
    uv run midisynth-audition "{{vendor}}/synthlab/COMBO.BNK" 0 --wave "{{vendor}}/synthlab/COMBO.WAV" -o {{out}}/seq/stock --lo 28 --hi 100
    uv run midisynth-audition "{{vendor}}/synthlab/COMBO.BNK" 9 --wave "{{vendor}}/synthlab/COMBO.WAV" -o {{out}}/seq/stock --lo 28 --hi 100

# Experimental aside (MIDI Synth): a ProDOS hard disk image for BazFast, sized like `disk`
# (build/<game>/disks/<prefix>.SYNTHLAB.PO, volume /<prefix>.SYNTHLAB): System 6.0.4 merged from the
# boot disk + SystemTools1 (Finder, CDevs, DAs, FSTs, tools, fonts; not its SCSI/printer
# drivers) + SystemTools3 (sounds, fonts), Tool035, and /synthLAB with a folder per export
# profile (Stock, ...), the tuning test (Tune), and /SenseiPlay with our NTP songs (/SenseiPlay/<prefix>).
# Experimental aside: build the synthLAB disk, build/<game>/disks/<prefix>.SYNTHLAB.PO
synthlab-disk: fetch-system6 seq midisynth-drum-audition midisynth-tunetest
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    shopt -s extglob                       # !(SenseiPlay) below
    cp2="$PWD/{{cp2}}"; img="$PWD/{{out}}/disks/{{prefix}}.SYNTHLAB.PO"; st="$PWD/{{out}}/disks/synthlabstage"
    rm -rf "$st" "$img" && mkdir -p "$st/synthLAB"
    x() { "$cp2" extract --preserve=naps --overwrite --exdir="$1" "${@:2}" >/dev/null; }
    x "$st" "{{s6}}/System.Disk.po"
    x "$st" "{{s6}}/SystemTools1.po" System/Finder System/CDevs System/Desk.Accs System/FSTs System/Tools System/Fonts Icons
    x "$st" "{{s6}}/SystemTools3.po" System
    x "$st" "{{s6}}/synthLAB.po" System/Tools/Tool035
    x "$st/synthLAB" "{{s6}}/synthLAB.po" synthLAB Icons Reference
    # A folder per export profile (sequences + the banks they reference) and the tuning test
    typed() {   # copy file $1 into dir $2 with its ProDOS type (NAPS suffix)
      case "$1" in *.SEQ) t=d50001;; *.BNK) t=d60001;; *.WAV) t=d80004;; *) return;; esac
      cp "$1" "$2/$(basename "$1")#$t"
    }
    for prof in $(uv run python -I -c "import tomllib;c=tomllib.load(open('{{gconf}}/midisynth.toml','rb'));print(' '.join(f'{k}:{v[\"folder\"]}:{c[\"banks\"][v[\"default_bank\"]][\"file\"]}:{c[\"banks\"][v[\"default_bank\"]][\"wave_file\"]}' for k,v in c['profiles'].items()))"); do
      IFS=: read -r name folder bnk wav <<< "$prof"
      d="$st/synthLAB/$folder"; mkdir -p "$d"
      for f in {{out}}/seq/$name/*.SEQ; do typed "$f" "$d"; done
      for f in "{{vendor}}/synthlab/$bnk" "{{vendor}}/synthlab/$wav" "{{out}}/seq/$name/$bnk" "{{out}}/seq/$name/$wav"; do
        [ -f "$f" ] && typed "$f" "$d"
      done
    done
    mkdir -p "$st/synthLAB/Tune"; for f in {{out}}/seq/tune/*; do typed "$f" "$st/synthLAB/Tune"; done
    # NinjaTrackerPlus: SenseiPlay (ProDOS 8 player) + our songs, if `just ntp` has run
    if ls {{out}}/ntp/*/*.ntp >/dev/null 2>&1; then
      mkdir -p "$st/SenseiPlay"
      x "$st/SenseiPlay" "{{vendor}}/ntp/senseiplay800.2mg" sensei.system ntpplayer dgr.radio.dgr
      {{ntp_folders}} "$st/SenseiPlay/{{prefix}}"
    fi
    # /SenseiPlay goes in folder by folder in sorted order (p8_tree)
    {{fit_image}} "$img" {{prefix}}.SYNTHLAB "$st" '"$cp2" add "$img" !(SenseiPlay); if [ -d SenseiPlay ]; then {{p8_tree}} "$cp2" "$img" SenseiPlay; fi'
    rm -rf "$st"
    {{dist_pack}} "$img"
    "$cp2" catalog "$img" | grep -E '^Disk|blocks free'
    "$cp2" list "$img" | grep -c . | sed 's/^/files: /'

# Check that the fetched tools run on this machine, without the game data: CiderPress II makes
# a ProDOS image and lists a file on it, Munt's smf2wav starts, GSSquared and gs2-mcp are
# there where they have a build. (`just test` covers PHP and the NTP converter.)
smoke: fetch-cp2 fetch-munt fetch-gssquared fetch-mcp
    #!/usr/bin/env bash
    set -euo pipefail
    t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
    cp2="$PWD/{{cp2}}"
    "$cp2" create-disk-image "$t/t.po" 140k prodos >/dev/null
    echo hello > "$t/HELLO"; (cd "$t" && "$cp2" add t.po HELLO >/dev/null)
    "$cp2" list "$t/t.po" | grep -q HELLO && echo "CiderPress II: OK ($("$cp2" version | head -1))"
    "{{smf2wav}}" --help 2>&1 | grep -q "Munt" && echo "Munt smf2wav: OK"
    if [ -x "{{gs2}}" ]; then echo "GSSquared: present"; else echo "GSSquared: none for {{platform}}"; fi
    if [ -x "{{vendor}}/bin/gs2-mcp" ]; then echo "gs2-mcp: present"; else echo "gs2-mcp: none for {{platform}}"; fi

# Catches what works here only because vendor/ and build/ already exist: in a clean clone of
# HEAD (committed work only) with your original files linked in, run `just setup`, the tests,
# then `target` twice; the second run must rewrite nothing.
# Build from scratch in build/selftest/ to check a fresh checkout works
selftest target="disk":
    #!/usr/bin/env bash
    set -euo pipefail
    {{lowprio}}
    [ -z "$(git status --porcelain)" ] || echo "note: uncommitted changes are not part of the self-test"
    d="$PWD/build/selftest"; rm -rf "$d"
    git clone -q . "$d"
    for f in "original files"/*; do [ "$f" = "original files/README.md" ] || ln -s "$PWD/$f" "$d/$f"; done
    cd "$d"
    just setup                  # one command per line: `set -e` ignores failures inside a && chain
    just test -q
    just {{target}}
    touch .mark
    just {{target}}
    changed=$(find build vendor -type f -newer .mark ! -name '*.log')
    [ -z "$changed" ] || { echo "selftest: the second run rewrote:"; echo "$changed"; exit 1; }
    echo "selftest: OK ($d)"

# Run the unit tests
test *args:
    uv run pytest {{args}}

# Remove generated output (build/), keeping the slow Munt render cache (build/cache/)
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -d build ] || exit 0
    find build -mindepth 1 -maxdepth 1 ! -name cache -exec rm -rf {} +
    echo "build/ cleaned (kept build/cache/)"

# Remove all of build/, including the Munt render cache (re-rendering needs the MT-32 ROMs)
clean-all:
    rm -rf build
