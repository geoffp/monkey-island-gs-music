# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal client for gs2-mcp (GSSquared's MCP server) over stdio JSON-RPC, plus a
"DOC scope" that samples the Ensoniq oscillator registers while something plays.

Ensoniq state blob (GSSquared src/devices/es5503/soundglu.cpp): 16-byte header
(byte 10 = oscillators enabled, 12-15 = output rate Hz, LE), then 32 x 24-byte records:
freq u16, wtsize u16, control, vol, data, -, wavetblpointer u32, wavetblsize, resolution,
irqpend, -, accumulator u32. One pass through a wave plays at rate * freq / 2^(17 + res) Hz
(ensoniq.cpp: address = acc >> (9 + res - size)).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

ENSONIQ_DEVICE_ID = 22


class GS2:
    def __init__(self, mcp_bin: str, gs2_bin: str):
        env = dict(os.environ, GS2_BIN=gs2_bin)
        self.p = subprocess.Popen([mcp_bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "migs", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("gs2-mcp exited")
            msg = json.loads(line)
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def call(self, tool: str, **args) -> str:
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        text = "".join(c.get("text", "") for c in res.get("content", []))
        if res.get("isError"):
            raise RuntimeError(f"{tool}: {text}")
        return text

    def ensoniq(self) -> tuple[dict, list[dict]]:
        text = self.call("state_get", device_id=ENSONIQ_DEVICE_ID)
        m = re.search(r"\b([0-9a-fA-F]{1552,})\b", text)       # 16 + 32*24 bytes
        if not m:
            raise RuntimeError(f"no ensoniq blob in: {text[:200]}")
        return parse_ensoniq(bytes.fromhex(m.group(1)))

    def close(self) -> None:
        try:
            self.call("quit")
        except Exception:
            pass
        self.p.terminate()


def parse_ensoniq(b: bytes) -> tuple[dict, list[dict]]:
    hdr = {"oscs_enabled": b[10], "output_rate": struct.unpack_from("<I", b, 12)[0]}
    oscs = []
    for o in range(32):
        r = b[16 + o * 24 : 40 + o * 24]
        freq, wtsize = struct.unpack_from("<HH", r, 0)
        oscs.append({"osc": o, "freq": freq, "wtsize": wtsize, "control": r[4], "vol": r[5],
                     "ptr": struct.unpack_from("<I", r, 8)[0], "size": r[12], "res": r[13],
                     "acc": struct.unpack_from("<I", r, 16)[0]})
    return hdr, oscs


def pass_hz(rate: int, freq: int, res: int) -> float:
    return rate * freq / 2 ** (17 + res)


def scope(gs: GS2, seconds: float, interval: float, log: Path) -> None:
    """Log every change of (freq, res, size, ptr, vol, running) per oscillator, as JSONL."""
    last: dict[int, tuple] = {}
    t0 = time.time()
    with log.open("w") as f:
        while time.time() - t0 < seconds:
            try:
                hdr, oscs = gs.ensoniq()
            except RuntimeError:
                time.sleep(interval)
                continue
            for o in oscs:
                running = not (o["control"] & 1)
                key = (o["freq"], o["res"], o["size"], o["ptr"], o["vol"] > 0, running)
                if last.get(o["osc"]) != key:
                    last[o["osc"]] = key
                    f.write(json.dumps({"t": round(time.time() - t0, 2), **hdr, **o,
                                        "running": running,
                                        "hz": round(pass_hz(hdr["output_rate"], o["freq"], o["res"]), 3)}) + "\n")
                    f.flush()
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcp", default="vendor/bin/gs2-mcp")
    ap.add_argument("--gs2", default="vendor/gssquared/GSSquared-x86_64.AppImage")
    ap.add_argument("--config", type=Path, help=".gs2 machine config to boot")
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--log", type=Path, default=Path("gs/disks/docscope.jsonl"))
    args = ap.parse_args(argv)
    gs = GS2(str(Path(args.mcp).resolve()), str(Path(args.gs2).resolve()))
    try:
        extra = [str(args.config.resolve())] if args.config else []
        print(gs.call("launch", platform="IIgs", extra_args=extra))
        print(f"sampling DOC every {args.interval}s for {args.seconds}s -> {args.log}", flush=True)
        scope(gs, args.seconds, args.interval, args.log)
    finally:
        gs.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
