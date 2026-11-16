"""Build evals/dashboard.html: where tool routing stands now, and how it got there.

    python -m evals.dashboard            # reads history.jsonl + results/*.json, writes evals/dashboard.html

Why "combined status" and not "score per run": runs can be partial (a provider's daily limit, `--rerun-errors`,
`--tag regression`), so one run's score says little on its own. For every case in routing_cases.json the dashboard
takes the most recent run that actually got an answer for it. That is the honest current state, and replaying it after
each run gives the trend. Cases no run has answered yet are shown as "not run", never as failures.

The page is self-contained (inline SVG, no scripts, no external requests). Provider error messages are never copied
into it: they can contain account identifiers.
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from evals.common import CASES_PATH, cost_summary, load_cases

HERE = Path(__file__).parent
STATUS = ("pass", "fail", "not run")
SYMBOL = {"pass": "✓", "fail": "✗", "not run": "–", "error": "·", None: ""}  # display symbols, not credentials  # nosec B105


@dataclass
class Run:
    at: str
    model: str
    n: int
    note: str = ""
    unsafe: int = 0
    results: dict[str, dict] = field(default_factory=dict)  # case id -> result (empty if the results file is missing)

    @property
    def when(self) -> str:
        return datetime.strptime(self.at, "%Y%m%d-%H%M%S").strftime("%d %b %H:%M")

    @property
    def answered(self) -> int:
        return sum(1 for r in self.results.values() if r.get("outcome") != "error")

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results.values() if r.get("passed"))

    @property
    def cost(self) -> dict:
        return cost_summary(list(self.results.values()))


def load_runs(history: Path, results_dir: Path) -> list[Run]:
    runs = []
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        h = json.loads(line)
        run = Run(h["at"], h.get("model", "?"), h.get("n", 0), h.get("note", ""), h.get("unsafe", 0))
        f = results_dir / f"{h['at']}.json"
        if f.exists():
            run.results = {r["id"]: r for r in json.loads(f.read_text(encoding="utf-8"))["results"]}
        runs.append(run)
    return sorted(runs, key=lambda r: r.at)


def combined(cases: list[dict], runs: list[Run]) -> dict[str, dict | None]:
    """Latest answered result per current case (None = no run has answered it yet)."""
    state: dict[str, dict | None] = {c["id"]: None for c in cases}
    for run in runs:
        for cid, r in run.results.items():
            if cid in state and r.get("outcome") != "error":
                state[cid] = {**r, "run": run.at}
    return state


def status_of(r: dict | None) -> str:
    return "not run" if r is None else ("pass" if r.get("passed") else "fail")


def counts(cases: list[dict], state: dict, key) -> dict[str, Counter]:
    out: dict[str, Counter] = {}
    for c in cases:
        for k in key(c):
            out.setdefault(k, Counter())[status_of(state[c["id"]])] += 1
    return dict(sorted(out.items()))


# ------------------------------------------------------------------------------------------------ rendering
CSS = """
:root{--bg:#fcfcfb;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;--grid:#efeeea;--accent:#2a78d6;
--pass:#0ca30c;--fail:#d03b3b;--none:#b9b8b2;--passbg:#e5f5e5;--failbg:#fbe8e8;--nonebg:#f0efec}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--card:#222221;--ink:#f0efec;--ink2:#c3c2b7;--line:#383835;--grid:#2c2c2a;
--accent:#3987e5;--pass:#0ca30c;--fail:#e66767;--none:#6b6a64;--passbg:#1d331d;--failbg:#3a2222;--nonebg:#2c2c2a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:28px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 10px}
.sub{color:var(--ink2);margin:0 0 20px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.tile b{display:block;font-size:28px;line-height:1.2}
.tile span{color:var(--ink2);font-size:13px}.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow-x:auto}
.card h3{font-size:14px;margin:0 0 6px;font-weight:600}svg text{fill:var(--ink2);font-size:11px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink2);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--ink2);margin:0 0 8px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.bar{display:flex;height:14px;border-radius:4px;overflow:hidden;gap:2px;min-width:120px}.bar i{display:block}
.s-pass{background:var(--pass)}.s-fail{background:var(--fail)}.s-none{background:var(--none)}
.cell{display:inline-block;min-width:26px;text-align:center;border-radius:4px;font-weight:600}
.c-pass{background:var(--passbg);color:var(--pass)}.c-fail{background:var(--failbg);color:var(--fail)}.c-none{color:var(--ink2)}
code{font-size:12px}.muted{color:var(--ink2)}
"""


def dash(value, unit: str = "") -> str:
    return "–" if value is None else f"{value:,}{unit}"


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def line_chart(title: str, points: list[tuple[str, float]], *, ymax: float, fmt, ylabel_fmt) -> str:
    w, h, left, right, top, bottom = 460, 190, 44, 14, 12, 34
    pw, ph = w - left - right, h - top - bottom
    n = len(points)
    xs = [left + (pw * i / (n - 1) if n > 1 else pw / 2) for i in range(n)]
    ys = [top + ph * (1 - (v / ymax if ymax else 0)) for _, v in points]
    grid = "".join(
        f'<line x1="{left}" x2="{w - right}" y1="{top + ph * (1 - f):.1f}" y2="{top + ph * (1 - f):.1f}" stroke="var(--grid)"/>'
        f'<text x="{left - 6}" y="{top + ph * (1 - f) + 4:.1f}" text-anchor="end">{ylabel_fmt(ymax * f)}</text>'
        for f in (0, 0.5, 1)
    )
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys, strict=True)))
    marks = "".join(
        f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="var(--accent)" stroke="var(--card)" stroke-width="2"/>'
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="14" fill="transparent"><title>{esc(label)}: {esc(fmt(v))}</title></circle></g>'
        for x, y, (label, v) in zip(xs, ys, points, strict=True)
    )
    last = (f'<text x="{xs[-1]:.1f}" y="{ys[-1] - 10:.1f}" text-anchor="end" style="fill:var(--ink);font-weight:600">'
            f"{esc(fmt(points[-1][1]))}</text>") if points else ""
    def anchor(i: int) -> str:  # keep the end labels inside the chart
        return "start" if i == 0 and n > 1 else ("end" if i == n - 1 and n > 1 else "middle")

    xlab = "".join(f'<text x="{x:.1f}" y="{h - 12}" text-anchor="{anchor(i)}">{esc(label)}</text>'
                   for i, (x, (label, _)) in enumerate(zip(xs, points, strict=True)) if n <= 8 or i % 2 == 0 or i == n - 1)
    return (f'<div class="card"><h3>{esc(title)}</h3><svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{esc(title)}">'
            f'{grid}<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2"/>{marks}{last}{xlab}</svg></div>')


def bar_rows(rows: dict[str, Counter]) -> str:
    body = []
    for name, c in rows.items():
        segs = "".join(
            f'<i class="s-{"none" if s == "not run" else s}" style="flex:{c[s]}" title="{esc(s)}: {c[s]}"></i>' for s in STATUS if c[s]
        )
        rate = f"{c['pass'] / (c['pass'] + c['fail']):.0%}" if c["pass"] + c["fail"] else "–"
        body.append(f"<tr><td>{esc(name)}</td><td style='width:45%'><div class='bar'>{segs}</div></td>"
                    f"<td class='num'>{c['pass']}</td><td class='num'>{c['fail']}</td><td class='num'>{c['not run']}</td>"
                    f"<td class='num'>{rate}</td></tr>")
    return ("<table><tr><th></th><th></th><th class='num'>✓ pass</th><th class='num'>✗ fail</th><th class='num'>– not run</th>"
            "<th class='num'>pass rate</th></tr>" + "".join(body) + "</table>")


LEGEND = ('<div class="legend"><span><i class="sw s-pass"></i>✓ pass</span><span><i class="sw s-fail"></i>✗ fail</span>'
          '<span><i class="sw s-none"></i>– not run yet</span></div>')


def render(cases: list[dict], runs: list[Run]) -> str:
    detailed = [r for r in runs if r.results]
    state = combined(cases, detailed)
    total = Counter(status_of(r) for r in state.values())
    answered = total["pass"] + total["fail"]
    unsafe_now = sum(1 for r in state.values() if r and r.get("unsafe"))
    bad_args = sum(1 for r in state.values() if r and r.get("outcome") == "bad_args")
    model = runs[-1].model if runs else "?"

    trend_rate, trend_cov = [], []
    for i, run in enumerate(detailed):
        s = Counter(status_of(r) for r in combined(cases, detailed[: i + 1]).values())
        done = s["pass"] + s["fail"]
        trend_rate.append((run.when, s["pass"] / done if done else 0.0))
        trend_cov.append((run.when, float(done)))

    tiles = (
        f'<div class="tile"><b>{total["pass"] / answered:.0%}</b><span>{total["pass"]} of {answered} answered cases pass</span></div>'
        if answered else '<div class="tile"><b>–</b><span>no answered cases yet</span></div>'
    ) + (
        f'<div class="tile"><b>{unsafe_now}</b><span>unsafe calls (a write or delete that wasn\'t acceptable)</span></div>'
        f'<div class="tile"><b>{bad_args}</b><span>right tool, wrong arguments</span></div>'
        f'<div class="tile"><b>{total["not run"]}</b><span>of {len(cases)} cases not answered yet</span></div>'
    )

    charts = ""
    if trend_rate:
        charts = '<div class="charts">' + line_chart(
            "Pass rate of answered cases, after each run", trend_rate, ymax=1.0, fmt=lambda v: f"{v:.0%}", ylabel_fmt=lambda v: f"{v:.0%}"
        ) + line_chart(
            f"Cases answered so far (of {len(cases)})", trend_cov, ymax=float(len(cases)), fmt=lambda v: f"{v:.0f}", ylabel_fmt=lambda v: f"{v:.0f}"
        ) + "</div>"

    # Regression cases (and anything failing now) across runs.
    watch = [c for c in cases if "regression" in c.get("tags", []) or status_of(state[c["id"]]) == "fail"]
    head = "".join(f"<th class='num'>{esc(r.when)}</th>" for r in detailed)
    grid_rows = []
    for c in watch:
        cells = []
        for r in detailed:
            res = r.results.get(c["id"])
            st = None if res is None else ("error" if res.get("outcome") == "error" else status_of(res))
            cls = {"pass": "c-pass", "fail": "c-fail"}.get(st or "", "c-none")  # CSS class names  # nosec B105
            tip = {None: "not in this run", "error": "no answer (provider error)"}.get(st, f"{st}: {res.get('got') or 'text answer'}" if res else "")
            cells.append(f"<td class='num'><span class='cell {cls}' title='{esc(tip)}'>{SYMBOL[st]}</span></td>")
        now = status_of(state[c["id"]])
        grid_rows.append(f"<tr><td><code>{esc(c['id'])}</code></td>{''.join(cells)}"
                         f"<td class='num'><span class='cell {'c-' + ('none' if now == 'not run' else now)}'>{SYMBOL[now]}</span></td></tr>")
    grid = (f"<div class='card'><table><tr><th>case</th>{head}<th class='num'>now</th></tr>{''.join(grid_rows)}</table>"
            "<p class='muted'>✓ pass · ✗ fail · · no answer (provider error) · blank: not in that run. Hover a cell for the tool it chose.</p></div>")

    fails = [(c, state[c["id"]]) for c in cases if status_of(state[c["id"]]) == "fail"]
    fail_rows = "".join(
        f"<tr><td><code>{esc(c['id'])}</code></td><td>{esc(c['prompt'])}</td><td>{esc(r.get('outcome'))}</td>"
        f"<td>{esc(r.get('got') or '(text answer)')}</td><td>{esc(', '.join(c.get('expect', [])) or '(no tool)')}</td></tr>"
        for c, r in fails
    ) or "<tr><td colspan='5'>No failing cases.</td></tr>"

    run_rows = "".join(
        f"<tr><td>{esc(r.when)}</td><td class='num'>{r.n}</td><td class='num'>{r.answered if r.results else '–'}</td>"
        f"<td class='num'>{r.passed if r.results else '–'}</td><td class='num'>{r.unsafe}</td>"
        f"<td class='num'>{dash(r.cost['latency_ms_p50'], ' ms')}</td><td class='num'>{dash(r.cost['latency_ms_p90'], ' ms')}</td>"
        f"<td class='num'>{dash(r.cost['input_tokens_p50'])}</td><td class='num'>{dash(r.cost['output_tokens_p50'])}</td>"
        f"<td>{esc(r.note)}</td></tr>"
        for r in reversed(runs)
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Keel routing eval</title><style>{CSS}</style></head><body><main>
<h1>Keel routing eval</h1>
<p class="sub">Does the model pick the right tool, with the right arguments? {len(cases)} cases, model <code>{esc(model)}</code>.
Each case shows its most recent answered run, so partial runs (rate limits, re-runs) still add up.
Generated by <code>python -m evals.dashboard</code>.</p>
<div class="tiles">{tiles}</div>
<h2>Over time</h2>{charts or '<p class="muted">No run results yet.</p>'}
<h2>By category</h2><div class="card">{LEGEND}{bar_rows(counts(cases, state, lambda c: [c['category']]))}</div>
<h2>By tag</h2><div class="card">{LEGEND}{bar_rows(counts(cases, state, lambda c: c.get('tags', [])))}</div>
<h2>Regression cases across runs</h2>{grid}
<h2>Failing now</h2><div class="card"><table><tr><th>case</th><th>prompt</th><th>outcome</th><th>got</th><th>expected one of</th></tr>{fail_rows}</table></div>
<h2>Runs</h2><div class="card"><table><tr><th>run (UTC)</th><th class="num">cases</th><th class="num">answered</th><th class="num">passed</th>
<th class="num">unsafe</th><th class="num">median latency</th><th class="num">90th pct latency</th>
<th class="num">tokens in / case</th><th class="num">tokens out / case</th><th>note</th></tr>{run_rows}</table>
<p class="muted">Latency is one LLM request per case, measured from this machine. Tokens are the provider's own counts (median per case):
"in" is the system prompt plus the tool definitions plus the message. Runs from before these were recorded show –.</p></div>
</main></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "dashboard.html")
    args = ap.parse_args(argv)
    page = render(load_cases(CASES_PATH), load_runs(HERE / "history.jsonl", HERE / "results"))
    args.out.write_text(page, encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
