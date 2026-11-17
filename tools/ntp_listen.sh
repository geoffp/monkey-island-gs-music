#!/usr/bin/env bash
# Build NTP variants for listening, with a WAV preview of every MOD in <variant>/wav/.
#   tools/ntp_listen.sh GAME "MODE..." [SONG...]
# No songs: the whole game through `just ntpmod` (skips variants whose inputs are unchanged).
# With songs: only those, straight into build/GAME/ntp/MODE/ (replacing what is there), log in
# build/GAME/ntp/MODE.log. The WAVs are DOC renders (migs.docrender).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ $# -lt 2 ]; then sed -n 2,6p "$0"; exit 2; fi
game=$1; modes=$2; shift 2
if [ $# -eq 0 ]; then
  just game="$game" ntpmod "$modes"
fi
for m in $modes; do
  d=build/$game/ntp/$m
  if [ $# -gt 0 ]; then
    rm -rf "$d"; mkdir -p "$d"
    nice -n "${MIGS_NICE:-10}" uv run ntpmod --game "$game" build/"$game"/extracted/midi/mt32/*.mid -o "$d" \
      --only "$@" --samples "$m" > "$d.log" 2>&1 || { echo "$m: build failed, see $d.log"; tail -5 "$d.log"; exit 1; }
  fi
  mkdir -p "$d/wav"; n=0; todo=()
  for f in "$d"/*.mod; do
    [ -f "$f" ] || continue
    w="$d/wav/$(basename "$f" .mod).wav"
    if [ ! -f "$w" ] || [ "$f" -nt "$w" ]; then todo+=("$f"); fi
    n=$((n + 1))
  done
  # as the IIgs's DOC plays them (migs.docrender), not a desktop player's smoothed mix
  if [ ${#todo[@]} -gt 0 ]; then
    printf '%s\n' "${todo[@]}" | xargs -P 8 -n 4 nice -n "${MIGS_NICE:-10}" uv run docrender -o "$d/wav"
  fi
  echo "$m: $n songs, WAVs in $d/wav/"
  grep -c "^[A-Z0-9]*: [0-9]* samples" "$d.log" | sed "s/^/$m: songs built per the log: /" || true
  grep -n "Traceback\|fail" "$d.log" | head -5 || true
done
