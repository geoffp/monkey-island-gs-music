# SPDX-License-Identifier: GPL-3.0-or-later
"""A self-contained HTML report of a DOC volume sweep measurement, for sharing (emulator authors).

Input: the CSV `doc-sweep-analyse --csv` wrote for a recording of the sweep (docs/es5503.md).
Output: one HTML file, with the data and the drawing code inline, no libraries or network: it
opens in any browser, and follows the reader's light or dark setting. Charts: the measured
curve against a linear DOC, the gap from linear, the neighbour effect; then the table and the
method. Colours are the dataviz reference palette's first three slots (validated both modes).
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path


def doc_volume(mod: int) -> int:
    return mod * 1020 // 256


def load(path: Path) -> dict:
    rows = list(csv.DictReader(path.open()))
    a = {int(r["test"]): float(r["test_db"]) for r in rows if r["section"] == "A"}
    top = a[64]
    curve = [{"doc": doc_volume(m), "mod": m, "db": round(a[m] - top, 1),
              "lin": round(20 * math.log10(doc_volume(m) / 255), 1)} for m in range(1, 65)]
    alone, nb = {}, []
    for r in rows:
        if r["section"] != "B":
            continue
        test = doc_volume(int(r["test_b"]))
        side = "before" if r["neighbour_before"] != "" else "after"
        n = doc_volume(int(r[f"neighbour_{side}"]))
        lv = float(r["test_db"]) - top
        if n == 0:
            alone[test] = lv
        else:
            nb.append({"test": test, "side": side, "n": n, "db": lv})
    for x in nb:
        x["gain"] = round(x["db"] - alone[x["test"]], 1)
    full = {c["doc"]: c["db"] for c in curve}
    half = [float(r["test_db"]) - top - full[doc_volume(int(r["test_half"]))]
            for r in rows if r["section"] == "C"]
    return {"curve": curve, "neighbour": nb, "floor": round(a[0] - top, 1),
            "half_drop": round(sum(half) / len(half), 1),
            "next_max": round(max(abs(x["gain"]) for x in nb if x["side"] == "after"), 1)}


def _m(v: float) -> str:
    """A signed number with a real minus sign."""
    t = f"{v:.1f}"
    return "0.0" if t == "-0.0" else t.replace("-", "−")


def render(d: dict, title: str = "Apple IIgs Ensoniq DOC: measured volume curve") -> str:
    by = {c["doc"]: c for c in d["curve"]}
    rows = "\n".join(
        f"<tr><td>{c['doc']}</td><td>{c['mod']}</td><td>{_m(c['db'])}</td><td>{_m(c['lin'])}</td>"
        f"<td>{_m(c['db'] - c['lin'])}</td></tr>" for c in reversed(d["curve"]))
    fill = {
        "TITLE": html.escape(title),
        "LVL127": _m(by[127]["db"]), "LIN127": _m(by[127]["lin"]),
        "GAP31": _m(by[31]["db"] - by[31]["lin"]), "LVL23": _m(by[23]["db"]),
        "NEXTMAX": _m(d["next_max"]), "HALF": _m(d["half_drop"]), "FLOOR": _m(-d["floor"]).lstrip("−"),
        "ROWS": rows, "DATA": json.dumps({"curve": d["curve"], "neighbour": d["neighbour"]}),
    }
    out = TEMPLATE
    for k, v in fill.items():
        out = out.replace("{{" + k + "}}", v)
    return out


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
  :root {
    color-scheme: light;
    --surface: #fcfcfb; --page: #f9f9f7; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --band: #ecebe6;
    --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --surface: #1a1a19; --page: #0d0d0d; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --band: #262624;
      --s1: #3987e5; --s2: #d95926; --s3: #199e70;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--ink);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 980px; margin: 0 auto; padding: 32px 20px 48px; display: flex; flex-direction: column; gap: 20px; }
  h1 { font-size: 26px; font-weight: 600; margin: 0; line-height: 1.25; }
  h2 { font-size: 17px; font-weight: 600; margin: 0 0 4px; }
  .lede { margin: 0; max-width: 72ch; color: var(--ink); }
  .sub { margin: 0 0 10px; color: var(--ink-2); font-size: 13px; max-width: 78ch; }
  .num { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; }
  .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; color: var(--ink-2); margin-bottom: 6px; }
  .key { display: inline-flex; align-items: center; gap: 6px; }
  .key i { display: inline-block; width: 16px; height: 2px; border-radius: 1px; }
  .chart { position: relative; }
  .chart svg { display: block; width: 100%; overflow: visible; }
  .chart text { fill: var(--ink-2); font-size: 12px; font-family: inherit; }
  .tip { position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--border);
    box-shadow: 0 4px 16px rgba(0,0,0,0.12); border-radius: 8px; padding: 8px 10px; font-size: 12px;
    white-space: nowrap; z-index: 2; color: var(--ink); }
  .tip b { font-weight: 600; } .tip .m { color: var(--ink-2); }
  .tablewrap { overflow: auto; max-height: 420px; }
  table { border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; width: 100%; }
  th, td { text-align: right; padding: 4px 10px; border-bottom: 1px solid var(--grid); }
  th { position: sticky; top: 0; background: var(--surface); font-weight: 600; }
  details { max-width: 78ch; } details p, details li { color: var(--ink-2); font-size: 14px; }
  summary { cursor: pointer; font-weight: 600; }
  footer { color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<main>
  <header>
    <h1>{{TITLE}}</h1>
    <p class="lede">On a real Apple IIgs, DOC volume register <span class="num">127</span> plays at
      <span class="num">{{LVL127}} dB</span>, where a linear multiply gives <span class="num">{{LIN127}} dB</span>.
      At volume <span class="num">31</span> it is <span class="num">{{GAP31}} dB</span> below linear, and volumes
      <span class="num">23</span> and lower are effectively silent (<span class="num">{{LVL23}} dB</span>).
      The emulators we checked treat the register as an exact linear multiply.</p>
  </header>

  <section class="card">
    <h2>Level by volume register: real IIgs against a linear DOC</h2>
    <p class="sub">dB relative to volume 255, for a steady 452.5 Hz sine on oscillator 0. Volumes are those the
      NinjaTrackerPlus player writes (every 4th value). Hover for values.</p>
    <div class="legend" aria-hidden="true">
      <span class="key"><i style="background:var(--s1)"></i>Real IIgs (measured)</span>
      <span class="key"><i style="background:var(--s2)"></i>Linear (emulators): 20·log10(v / 255)</span>
    </div>
    <div class="chart" id="curve"></div>
  </section>

  <section class="card">
    <h2>How far below linear the real chip plays</h2>
    <p class="sub">Measured level minus the linear level, in dB. Above about volume 40 the chip behaves roughly as if
      it subtracted 28 from the register; below that it drops away to a null near volume 20.</p>
    <div class="chart" id="gap"></div>
  </section>

  <section class="card">
    <h2>Neighbour effect: a loud oscillator in the previous slot raises a quiet one</h2>
    <p class="sub">Change in a test tone's level (oscillator 2) when oscillator 1, the slot scanned just before it,
      plays another tone. A loud oscillator in the next slot (oscillator 3) changes it by at most
      <span class="num">{{NEXTMAX}} dB</span>.</p>
    <div class="legend" aria-hidden="true">
      <span class="key"><i style="background:var(--s1)"></i>Test at volume 23</span>
      <span class="key"><i style="background:var(--s2)"></i>Test at volume 39</span>
      <span class="key"><i style="background:var(--s3)"></i>Test at volume 79</span>
    </div>
    <div class="chart" id="neighbour"></div>
  </section>

  <section class="card">
    <h2>The curve belongs to the register, not to sample × volume</h2>
    <p class="lede">A half-amplitude copy of the test table plays <span class="num">{{HALF}} dB</span> relative to the
      full one on average, across volumes 255 down to 15, the same at every volume. So the loss depends on the
      volume register alone.</p>
  </section>

  <section class="card">
    <h2>The measurements</h2>
    <div class="tablewrap">
      <table>
        <thead><tr><th>DOC volume</th><th>NTP (MOD) volume</th><th>Measured, dB</th><th>Linear, dB</th><th>Measured − linear, dB</th></tr></thead>
        <tbody>
{{ROWS}}
        </tbody>
      </table>
    </div>
  </section>

  <details>
    <summary>How this was measured, and its limits</summary>
    <p>A test song for the NinjaTrackerPlus player holds a 256-byte sine table (7 cycles) looped on oscillator 0 and
      steps it through every volume the player can write: MOD volume 64 down to 0, about one second each, giving DOC
      volume = MOD × 1020 ÷ 256. A constant pilot tone (22 cycles, 1422 Hz) on oscillator 9, far from the test tone
      in scan order, plays throughout; every level is measured against it, so any gain change after the DOC cancels.
      All 32 oscillators are enabled, as NinjaTrackerPlus does.</p>
    <p>The machine: one Apple IIgs with an Applied Engineering Audio Animator stereo card. Its Line Out (fed from the
      DOC's output on the J25 sound connector, so the Control Panel volume plays no part) went through a small digital
      mixer into a 44.1 kHz, 24-bit recording. Levels are Hann-windowed DFT bins over the middle of each step. With
      the test tone at volume 0, its bin read {{FLOOR}} dB below volume 255.</p>
    <ul>
      <li>One chip, one card, ROM version not recorded. Other machines may differ in detail.</li>
      <li>The player writes only every 4th volume (3, 7, 11 … 255), so the exact edge of the silent region can't be seen here.</li>
      <li>Below volume 19, volumes 3–15 all give the same faint output, which halves with a half-amplitude table: a
        small signal of opposite sign, cancelling near volume 20.</li>
      <li>The same test in GSSquared 1.0 measures linear to within about 1 dB.</li>
    </ul>
  </details>
  <footer>Measured 2026-11-15.</footer>
</main>

<script>
(() => {
  const DATA = {{DATA}};
  const NS = 'http://www.w3.org/2000/svg';
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const el = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  };
  const minus = (v) => (v < 0 ? '−' : '') + Math.abs(v);
  const fmt = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + ' dB';
  const ticks = (lo, hi, step) => { const t = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) t.push(Math.round(v * 10) / 10); return t; };

  function textWidth(svg, strings) {
    const t = el('text', { visibility: 'hidden' }, svg);
    let w = 0;
    for (const s of strings) { t.textContent = s; w = Math.max(w, t.getComputedTextLength()); }
    t.remove();
    return w;
  }

  // a line chart: series = [{color, pts:[{x,y}], label?, labelAt?}], plus axes, grid and a hover tooltip
  function chart(host, o) {
    host.replaceChildren();
    const W = host.clientWidth, H = o.height(W);
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': o.aria }, host);
    const yt = ticks(o.y[0], o.y[1], o.ystep), ylab = yt.map(o.yfmt || minus);
    const M = { t: o.ylabel ? 26 : 12, r: o.right || 14, b: 48, l: textWidth(svg, ylab) + 14 };
    const sx = (v) => M.l + (v - o.x[0]) / (o.x[1] - o.x[0]) * (W - M.l - M.r);
    const sy = (v) => H - M.b - (v - o.y[0]) / (o.y[1] - o.y[0]) * (H - M.t - M.b);
    if (o.band) {
      el('rect', { x: sx(o.band[0]), y: M.t, width: sx(o.band[1]) - sx(o.band[0]), height: H - M.t - M.b, fill: css('--band') }, svg);
      if (textWidth(svg, [o.bandLabel]) + 6 < sx(o.band[1]) - sx(o.band[0]))   // only where it fits
        el('text', { x: (sx(o.band[0]) + sx(o.band[1])) / 2, y: M.t + 14, 'text-anchor': 'middle' }, svg).textContent = o.bandLabel;
    }
    yt.forEach((v, i) => {
      el('line', { x1: M.l, x2: W - M.r, y1: sy(v), y2: sy(v), stroke: css('--grid') }, svg);
      el('text', { x: M.l - 8, y: sy(v) + 4, 'text-anchor': 'end' }, svg).textContent = ylab[i];
    });
    el('line', { x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b, stroke: css('--axis') }, svg);
    o.xticks.forEach((v) => {
      el('line', { x1: sx(v), x2: sx(v), y1: H - M.b, y2: H - M.b + 5, stroke: css('--axis') }, svg);
      el('text', { x: sx(v), y: H - M.b + 18, 'text-anchor': 'middle' }, svg).textContent = v;
    });
    el('text', { x: W - M.r, y: H - 6, 'text-anchor': 'end' }, svg).textContent = o.xlabel;
    if (o.ylabel) el('text', { x: M.l - 8, y: M.t - 2, 'text-anchor': 'end', dy: '-0.4em' }, svg).textContent = o.ylabel;
    if (o.zero) el('line', { x1: M.l, x2: W - M.r, y1: sy(0), y2: sy(0), stroke: css('--axis') }, svg);
    for (const s of o.series) {
      const d = s.pts.map((p, i) => (i ? 'L' : 'M') + sx(p.x).toFixed(1) + ',' + sy(p.y).toFixed(1)).join('');
      el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
      if (s.dots !== false) for (const p of s.pts)
        el('circle', { cx: sx(p.x), cy: sy(p.y), r: s.r || 3, fill: s.color, stroke: css('--surface'), 'stroke-width': 1.5 }, svg);
    }
    if (o.labels) o.labels(svg, sx, sy, W, M);
    const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; host.appendChild(tip);
    const cross = el('line', { y1: M.t, y2: H - M.b, stroke: css('--axis'), visibility: 'hidden' }, svg);
    const hit = el('rect', { x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: 'transparent' }, svg);
    hit.addEventListener('pointermove', (ev) => {
      const r = svg.getBoundingClientRect(), mx = (ev.clientX - r.left) * W / r.width, my = (ev.clientY - r.top) * H / r.height;
      const xs = o.hoverXs, xv = xs.reduce((a, b) => (Math.abs(sx(b) - mx) < Math.abs(sx(a) - mx) ? b : a));
      cross.setAttribute('x1', sx(xv)); cross.setAttribute('x2', sx(xv)); cross.setAttribute('visibility', 'visible');
      tip.replaceChildren(...o.tip(xv).map(([k, v, cls]) => {
        const row = document.createElement('div'), a = document.createElement(cls === 'h' ? 'b' : 'span');
        a.textContent = k; if (cls === 'm') a.className = 'm'; row.appendChild(a); if (v != null) row.append(' ', v); return row;
      }));
      tip.hidden = false;
      tip.style.left = Math.min(Math.max(0, sx(xv) * r.width / W + 12), host.clientWidth - tip.offsetWidth) + 'px';
      tip.style.top = Math.max(0, my * r.height / H - 10) + 'px';
    });
    hit.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); });
  }

  const C = DATA.curve, byDoc = Object.fromEntries(C.map((c) => [c.doc, c]));
  const xt = [0, 32, 64, 96, 128, 160, 192, 224, 255], docs = C.map((c) => c.doc);

  function draw() {
    const s1 = css('--s1'), s2 = css('--s2'), s3 = css('--s3');
    const lin = []; for (let v = 3; v <= 255; v++) { const y = 20 * Math.log10(v / 255); if (y >= -55) lin.push({ x: v, y }); }
    chart(document.getElementById('curve'), {
      height: (W) => Math.max(260, Math.min(400, W * 0.5)), x: [0, 255], y: [-55, 0], ystep: 10, xticks: xt,
      xlabel: 'DOC volume register', ylabel: 'dB', band: [0, 25], bandLabel: 'silent',
      aria: 'Line chart: measured DOC level in dB against volume register 3 to 255, compared with a linear multiply. The measured curve falls further below linear as the volume drops, to about −52 dB at volumes 19 and 23, where linear gives about −20.',
      series: [{ color: s2, pts: lin, dots: false }, { color: s1, pts: C.map((c) => ({ x: c.doc, y: c.db })) }],
      labels: (svg, sx, sy, W) => {
        if (W < 380) return;
        const p = byDoc[47];
        el('text', { x: sx(47) - 8, y: sy(p.lin) - 8, 'text-anchor': 'end' }, svg).textContent = 'Linear';
        el('text', { x: sx(47) + 8, y: sy(p.db) + 16 }, svg).textContent = 'Real IIgs';
      },
      hoverXs: docs,
      tip: (v) => { const c = byDoc[v]; return [[`DOC volume ${v}`, null, 'h'], [`NTP volume ${c.mod}`, null, 'm'],
        ['Measured', fmt(c.db)], ['Linear', fmt(c.lin)], ['Difference', fmt(c.db - c.lin)]]; },
    });
    const gap = C.map((c) => ({ x: c.doc, y: Math.round((c.db - c.lin) * 10) / 10 }));
    const gmin = Math.floor(Math.min(...gap.map((g) => g.y)) / 10) * 10;
    chart(document.getElementById('gap'), {
      height: (W) => Math.max(200, Math.min(290, W * 0.36)), x: [0, 255], y: [gmin, 0], ystep: 10, xticks: xt,
      xlabel: 'DOC volume register', zero: true,
      aria: 'Line chart of measured minus linear level in dB against DOC volume: near 0 dB at 255, about −5 at 63, −14 at 31, about −30 at 19 to 23.',
      series: [{ color: s1, pts: gap }], hoverXs: docs,
      tip: (v) => [[`DOC volume ${v}`, null, 'h'], ['Measured − linear', fmt(byDoc[v].db - byDoc[v].lin)]],
    });
    const tests = [23, 39, 79], cols = [s1, s2, s3], ns = [0, 63, 127, 255];
    const series = tests.map((t, i) => ({ t, color: cols[i], r: 4,
      pts: [{ x: 0, y: 0 }, ...DATA.neighbour.filter((n) => n.test === t && n.side === 'before').map((n) => ({ x: n.n, y: n.gain }))].sort((a, b) => a.x - b.x) }));
    const gmax = Math.ceil(Math.max(...series.flatMap((s) => s.pts.map((p) => p.y))) / 5) * 5;
    chart(document.getElementById('neighbour'), {
      height: (W) => Math.max(200, Math.min(290, W * 0.36)), x: [0, 255], y: [0, gmax], ystep: 5, xticks: ns,
      yfmt: (v) => (v > 0 ? '+' : '') + v, right: 84, xlabel: 'previous oscillator’s volume',
      aria: 'Line chart: gain in dB of a quiet test tone against the volume of the oscillator in the previous slot, for test volumes 23, 39 and 79. Volume 23 gains about 13 dB with a full-volume neighbour, 39 about 3 dB, 79 under 1 dB.',
      series,
      labels: (svg, sx, sy, W, M) => {
        if (W < 380) return;
        const ends = series.map((s) => ({ s, y: sy(s.pts[s.pts.length - 1].y) })).sort((a, b) => a.y - b.y);
        for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 15);
        for (const e of ends) el('text', { x: W - M.r + 8, y: e.y + 4 }, svg).textContent = `volume ${e.s.t}`;
      },
      hoverXs: ns,
      tip: (n) => [[`Previous oscillator at ${n}`, null, 'h'], ...series.map((s) => [`Test at ${s.t}`, fmt((s.pts.find((p) => p.x === n) || { y: 0 }).y)])],
    });
  }
  draw();
  let w = document.documentElement.clientWidth;
  window.addEventListener('resize', () => { const n = document.documentElement.clientWidth; if (n !== w) { w = n; draw(); } });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
})();
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("csv", type=Path, help="measurements: doc-sweep-analyse --csv output")
    ap.add_argument("out", type=Path, help="HTML file to write")
    a = ap.parse_args(argv)
    a.out.write_text(render(load(a.csv)))
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
