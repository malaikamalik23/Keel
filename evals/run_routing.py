"""Measure how often the model picks the right tool.

    python -m evals.run_routing                      # run all cases against the configured provider (costs API calls)
    python -m evals.run_routing --limit 10           # quick smoke run
    python -m evals.run_routing --min-score 0.85     # exit 1 if the score is lower (for scheduled CI runs)
    python -m evals.run_routing --limit 15 --sleep 20  # pause between cases (Groq free tier: 8,000 tokens/minute)

Only the model's FIRST tool decision is judged: it is the one that reveals whether the tool descriptions and system
prompt steer the model correctly, and it needs no database or side effects. Results are written to
evals/results/<timestamp>.json and one line is appended to evals/history.jsonl so changes can be tracked over time.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.llm.factory import build_llm, model_name
from evals.common import CASES_PATH, cost_summary, load_cases, run_eval

HERE = Path(__file__).parent


def main(argv: list[str] | None = None, llm=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=Path, default=CASES_PATH)
    ap.add_argument("--limit", type=int, default=None, help="only run the first N cases")
    ap.add_argument("--category", default=None, help="only run one category")
    ap.add_argument("--tag", default=None, help="only run cases with this tag (e.g. regression, deadline-nl)")
    ap.add_argument("--ids", default=None, help="only run these case ids (comma-separated)")
    ap.add_argument("--min-score", type=float, default=None, help="exit non-zero below this score (0-1)")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    ap.add_argument("--profile", choices=["lean", "full"], default=None, help="tool profile to evaluate (default: TOOL_PROFILE from .env)")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--sleep", type=float, default=0, metavar="SECONDS", help="pause between cases, for rate-limited free tiers")
    ap.add_argument("--rerun-errors", type=Path, default=None, metavar="RESULTS_JSON",
                    help="only re-run the cases that ended in an error (e.g. a provider's daily limit) in that saved run")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    if args.rerun_errors:
        errored = {r["id"] for r in json.loads(args.rerun_errors.read_text(encoding="utf-8"))["results"] if r.get("outcome") == "error" or r.get("error")}
        cases = [c for c in cases if c["id"] in errored]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.tag:
        cases = [c for c in cases if args.tag in c.get("tags", [])]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            print(f"Unknown case id(s): {', '.join(sorted(unknown))}")
            return 2
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases selected.")
        return 2

    settings = get_settings()
    llm = llm or build_llm(settings)
    profile = args.profile or settings.tool_profile
    print(f"Routing eval: {len(cases)} cases, model={model_name(settings)}, tool profile={profile}, temperature={settings.llm_temperature}")
    def show(r) -> None:
        mark = "PASS" if r.passed else ("UNSAFE" if r.unsafe else r.outcome.upper())
        detail = f"   [{r.error}]" if r.error else (f"   [{'; '.join(r.arg_problems)}]" if r.arg_problems else "")
        print(f"  {mark:10} {r.id:24} -> {r.got or '(text)'}{detail}")

    report = run_eval(llm, cases, model_name(settings), profile=profile, pause_seconds=args.sleep, on_result=show)

    n = len(report.results)
    errors = report.outcomes()["error"]
    print(f"\nScore: {report.score:.0%} ({sum(r.passed for r in report.results)}/{n})   right tool and right arguments")
    if errors:
        answered = n - errors
        rate = sum(r.passed for r in report.results) / answered if answered else 0.0
        print(f"   {errors} case(s) got no answer (provider error, e.g. a rate limit): {rate:.0%} of the {answered} answered cases passed."
              " Re-run just those with --rerun-errors <this run's results file>.")
    print(f"Routing: {report.routing_score:.0%} ({sum(r.routing_ok for r in report.results)}/{n})   right tool, arguments not checked")
    print("Outcomes: " + ", ".join(f"{k} {v}" for k, v in report.outcomes().items()))
    cost = cost_summary(report.results)
    if cost["latency_ms_p50"] is not None:
        print(f"Latency per case: median {cost['latency_ms_p50']} ms, 90th percentile {cost['latency_ms_p90']} ms   (one LLM request each)")
    if cost["input_tokens_p50"] is not None:
        print(f"Tokens per case: median {cost['input_tokens_p50']} in, {cost['output_tokens_p50']} out   "
              f"(total {cost['input_tokens_total']} in, {cost['output_tokens_total']} out)")
    unsafe = report.unsafe()
    print(f"Unsafe wrong calls (a write/delete tool that was not acceptable): {len(unsafe)}" + (f"  -> {', '.join(r.id for r in unsafe)}" if unsafe else ""))
    print("By category:")
    for cat, (ok, total) in sorted(report.by_category().items()):
        print(f"  {cat:15} {ok}/{total}")
    if report.by_tag():
        print("By tag:")
        for tag, (ok, total) in sorted(report.by_tag().items()):
            print(f"  {tag:15} {ok}/{total}")
    for f in report.failures():
        want = ", ".join(f.expected) or "(no tool)"
        print(f"\nFAILED {f.id} ({f.outcome}{', UNSAFE' if f.unsafe else ''}): {f.prompt!r}\n   got: {f.got or '(text answer)'}"
              f"   expected one of: {want}{' or no tool' if f.allow_none and f.expected else ''}")
        for problem in f.arg_problems:
            print(f"   argument: {problem}")

    if not args.no_save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        payload = {"model": report.model, "profile": profile, "temperature": settings.llm_temperature, "score": report.score, "routing_score": report.routing_score, "n": n,
                   "outcomes": report.outcomes(), "unsafe": len(unsafe), "cost": cost, "results": [r.__dict__ for r in report.results]}
        (args.out / f"{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        with (HERE / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": stamp, "model": report.model, "profile": profile, "temperature": settings.llm_temperature, "score": round(report.score, 3),
                                 "routing": round(report.routing_score, 3), "unsafe": len(unsafe), "errors": errors, "n": n}) + "\n")
        print(f"\nSaved {args.out / f'{stamp}.json'}")
        if args.out == HERE / "results":  # keep evals/dashboard.html in step with the real history
            from evals.dashboard import main as build_dashboard
            build_dashboard([])

    return 1 if args.min_score is not None and report.score < args.min_score else 0


if __name__ == "__main__":
    sys.exit(main())
