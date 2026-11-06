"""Measure how often the model picks the right tool.

    python -m evals.run_routing                      # run all cases against the configured provider (costs API calls)
    python -m evals.run_routing --limit 10           # quick smoke run
    python -m evals.run_routing --min-score 0.85     # exit 1 if the score is lower (for scheduled CI runs)

Only the model's FIRST tool decision is judged: it is the one that reveals whether the tool descriptions and system
prompt steer the model correctly, and it needs no database or side effects. Results are written to
evals/results/<timestamp>.json and one line is appended to evals/history.jsonl so changes can be tracked over time.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.llm.factory import build_llm
from evals.common import CASES_PATH, load_cases, run_eval

HERE = Path(__file__).parent


def main(argv: list[str] | None = None, llm=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=Path, default=CASES_PATH)
    ap.add_argument("--limit", type=int, default=None, help="only run the first N cases")
    ap.add_argument("--category", default=None, help="only run one category")
    ap.add_argument("--min-score", type=float, default=None, help="exit non-zero below this score (0-1)")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases selected.")
        return 2

    settings = get_settings()
    llm = llm or build_llm(settings)
    print(f"Routing eval: {len(cases)} cases, model={settings.agent_model}")
    report = run_eval(llm, cases, settings.agent_model, on_result=lambda r: print(f"  {'PASS' if r.passed else 'FAIL'}  {r.id:20} -> {r.got or '(text)'}" + (f"   [{r.error}]" if r.error else "")))

    print(f"\nScore: {report.score:.0%} ({sum(r.passed for r in report.results)}/{len(report.results)})")
    for cat, (ok, n) in sorted(report.by_category().items()):
        print(f"  {cat:15} {ok}/{n}")
    for f in report.failures():
        want = ", ".join(f.expected) or "(no tool)"
        print(f"\nFAILED {f.id}: {f.prompt!r}\n   got: {f.got or '(text answer)'}   expected one of: {want}{' or no tool' if f.allow_none and f.expected else ''}")

    if not args.no_save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        payload = {"model": report.model, "score": report.score, "n": len(report.results), "results": [r.__dict__ for r in report.results]}
        (args.out / f"{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        with (HERE / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": stamp, "model": report.model, "score": round(report.score, 3), "n": len(report.results)}) + "\n")

    return 1 if args.min_score is not None and report.score < args.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
