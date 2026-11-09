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
import contextlib
import json
import os
import re
import struct
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ENSONIQ_DEVICE_ID = 22
OSCILLATORS = 32
HEADER_SIZE, RECORD_SIZE = 16, 24
BLOB_SIZE = HEADER_SIZE + OSCILLATORS * RECORD_SIZE
BLOB_RE = re.compile(rf"\b([0-9a-fA-F]{{{2 * BLOB_SIZE},}})\b")
HALTED = 0x01                       # control register bit 0: oscillator stopped
MCP_PROTOCOL = "2025-06-18"


class GS2:
    """A gs2-mcp process, driven by JSON-RPC requests over its stdin/stdout. Use as a context
    manager (or call close()) so the emulator quits."""

    def __init__(self, mcp_bin: str, gs2_bin: str):
        self.p = subprocess.Popen([mcp_bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=dict(os.environ, GS2_BIN=gs2_bin),
                                  text=True, bufsize=1)
        self._id = 0
        self._rpc("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "migs", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def __enter__(self) -> GS2:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _send(self, msg: dict) -> None:
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def _rpc(self, method: str, params: dict) -> dict:
        """Send a request and return its result, skipping notifications and other replies."""
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        while line := self.p.stdout.readline():
            msg = json.loads(line)
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]
        raise RuntimeError("gs2-mcp exited")

    def call(self, tool: str, **args) -> str:
        """Call an MCP tool; returns its text content."""
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        text = "".join(c.get("text", "") for c in res.get("content", []))
        if res.get("isError"):
            raise RuntimeError(f"{tool}: {text}")
        return text

    def ensoniq(self) -> tuple[dict, list[Osc]]:
        text = self.call("state_get", device_id=ENSONIQ_DEVICE_ID)
        m = BLOB_RE.search(text)
        if not m:
            raise RuntimeError(f"no ensoniq blob in: {text[:200]}")
        return parse_ensoniq(bytes.fromhex(m.group(1)))

    def close(self) -> None:
        with contextlib.suppress(RuntimeError, OSError, ValueError):
            self.call("quit")
        self.p.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.p.wait(timeout=5)


@dataclass(frozen=True)
class Osc:
    """One oscillator's registers (field names are the JSONL log's)."""
    osc: int
    freq: int
    wtsize: int
    control: int
    vol: int
    ptr: int            # wave table pointer
    size: int           # wave table size code
    res: int            # resolution
    acc: int            # accumulator

    @property
    def running(self) -> bool:
        return not self.control & HALTED


def parse_ensoniq(b: bytes) -> tuple[dict, list[Osc]]:
    """(header {oscs_enabled, output_rate}, the 32 oscillators) from a state blob."""
    if len(b) < BLOB_SIZE:
        raise ValueError(f"ensoniq blob is {len(b)} bytes, need {BLOB_SIZE}")
    hdr = {"oscs_enabled": b[10], "output_rate": struct.unpack_from("<I", b, 12)[0]}
    oscs = []
    for o in range(OSCILLATORS):
        at = HEADER_SIZE + o * RECORD_SIZE
        freq, wtsize, control, vol = struct.unpack_from("<HHBB", b, at)
        ptr, = struct.unpack_from("<I", b, at + 8)
        acc, = struct.unpack_from("<I", b, at + 16)
        oscs.append(Osc(o, freq, wtsize, control, vol, ptr, b[at + 12], b[at + 13], acc))
    return hdr, oscs


def pass_hz(rate: int, freq: int, res: int) -> float:
    """Passes per second through a wave, at output `rate` Hz."""
    return rate * freq / 2 ** (17 + res)


def scope(gs: GS2, seconds: float, interval: float, log: Path) -> None:
    """Log every change of (freq, res, size, ptr, sounding, running) per oscillator, as JSONL."""
    last: dict[int, tuple] = {}
    t0 = time.monotonic()
    with log.open("w") as f:
        while (now := time.monotonic() - t0) < seconds:
            try:
                hdr, oscs = gs.ensoniq()
            except RuntimeError:            # e.g. not booted far enough yet
                time.sleep(interval)
                continue
            for o in oscs:
                key = (o.freq, o.res, o.size, o.ptr, o.vol > 0, o.running)
                if last.get(o.osc) != key:
                    last[o.osc] = key
                    f.write(json.dumps({"t": round(now, 2), **hdr, **asdict(o), "running": o.running,
                                        "hz": round(pass_hz(hdr["output_rate"], o.freq, o.res), 3)}) + "\n")
                    f.flush()
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcp", default="vendor/bin/gs2-mcp")
    ap.add_argument("--gs2", default="vendor/gssquared/GSSquared-x86_64.AppImage")
    ap.add_argument("--config", type=Path, help=".gs2 machine config to boot")
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--log", type=Path, default=Path("build/disks/docscope.jsonl"))
    args = ap.parse_args(argv)
    with GS2(str(Path(args.mcp).resolve()), str(Path(args.gs2).resolve())) as gs:
        extra = [str(args.config.resolve())] if args.config else []
        print(gs.call("launch", platform="IIgs", extra_args=extra))
        print(f"sampling DOC every {args.interval}s for {args.seconds}s -> {args.log}", flush=True)
        scope(gs, args.seconds, args.interval, args.log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
