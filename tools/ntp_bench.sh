#!/usr/bin/env bash
# Build a few songs in one NTP variant, away from the real build, and print what the planner did.
#   tools/ntp_bench.sh TAG GAME MODE SONG...      e.g.  tools/ntp_bench.sh try1 atlantis move-per-song 48
# Output: build/bench/TAG/GAME-MODE/ (MODs) and build/bench/TAG/GAME-MODE.log (the whole log).
# Prints errors, the loop planner's lines and each song's summary. --only still reads every
# song, so the MODs equal a full build's.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ $# -lt 4 ]; then sed -n 2,6p "$0"; exit 2; fi
tag=$1; game=$2; mode=$3; shift 3
d=build/bench/$tag/$game-$mode
rm -rf "$d"; mkdir -p "$d"
status=0
nice -n "${MIGS_NICE:-10}" uv run ntpmod --game "$game" build/"$game"/extracted/midi/mt32/*.mid -o "$d" \
  --only "$@" --samples "$mode" > "$d.log" 2>&1 || status=$?
grep -n "Traceback\|Error\|fail" "$d.log" | head -20 || true
grep "  loop \(set\|value\|moves\|sizes\|blocked\|takes\)\|^[A-Z0-9]*: [0-9]* samples\|^[A-Z0-9]*: rates" "$d.log" | cut -c1-260 || true
echo "exit status $status; log: $d.log"
exit $status
