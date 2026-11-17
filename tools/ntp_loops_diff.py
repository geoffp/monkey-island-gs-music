#!/usr/bin/env python3
"""What changed between NTP build variants, song by song (standard library only).

    tools/ntp_loops_diff.py GAME BASE VARIANT...      variants under build/GAME/ntp/
    tools/ntp_loops_diff.py --dirs BASE_DIR DIR...    any build directories (their log is DIR.log)

For every song whose MOD differs from the base's: DOC pages, sample count and each loop that
changed ("57:512" -> "57:8192r", r = raw). Songs whose loops are the same but whose bytes differ
are listed as such. Reads the build logs' "<song>: N samples, DOC p/256 pages ...; loops ..." lines.
"""
import re
import sys
from pathlib import Path

LINE = re.compile(r"(\w+?\d+): (\d+) samples, DOC (\d+)/256 pages \(reserve \d+\); loops (.*)")


def loops(log: Path) -> dict:
    out = {}
    for line in log.read_text(errors="replace").splitlines():
        m = LINE.match(line)
        if m:
            pairs = re.findall(r"(\S.*?):(\d+r?)(?: |$)", m[4])       # "Elec Organ 60:8192r Sax 62:512"
            out[m[1]] = (int(m[3]), dict(pairs), int(m[2]))
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "--dirs":
        dirs = [Path(a) for a in argv[1:]]
    elif len(argv) >= 3:
        dirs = [Path("build") / argv[0] / "ntp" / v for v in argv[1:]]
    else:
        print(__doc__)
        return 2
    base, rest = dirs[0], dirs[1:]
    missing = [str(p) for d in dirs for p in (d, Path(f"{d}.log")) if not p.exists()]
    if missing:
        print("missing: " + ", ".join(missing))
        return 1
    b = loops(Path(f"{base}.log"))
    for d in rest:
        v = loops(Path(f"{d}.log"))
        print(f"\n== {d.name} vs {base.name}")
        only = sorted(set(b) ^ set(v))
        if only:
            print("built in one only (not compared): " + " ".join(only))
        changed = 0
        for song in sorted(set(b) & set(v)):
            fa, fb = base / f"{song}.mod", d / f"{song}.mod"
            if not fa.exists() or not fb.exists():
                print(f"{song}: MOD missing ({'base' if not fa.exists() else d.name}): the song failed to build?")
                continue
            if fa.read_bytes() == fb.read_bytes():
                continue
            changed += 1
            la, lb = b[song][1], v[song][1]
            diff = [f"{n} {la.get(n, '-')}->{lb.get(n, '-')}" for n in sorted(set(la) | set(lb)) if la.get(n) != lb.get(n)]
            print(f"{song}: DOC {b[song][0]}->{v[song][0]} pages, samples {b[song][2]}->{v[song][2]}; "
                  + ("; ".join(diff) if diff else "same loops (other bytes differ)"))
        print(f"{changed} of {len(set(b) & set(v))} songs differ")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
