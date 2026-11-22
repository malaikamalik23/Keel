"""Task benchmark: did the whole job get done, safely, and what did it cost? (see evals/tasks.py)

    python -m evals.run_tasks                       # all task cases against the configured model (several LLM calls each)
    python -m evals.run_tasks --ids fu-promise-yes,mem-urdu-script
    python -m evals.run_tasks --category memory     # followup | multi | intent | ask-first | memory
    python -m evals.run_tasks --sleep 20            # pause between cases (Groq free tier: tokens per minute)
    python -m evals.run_tasks --rerun-errors evals/results/tasks-<run>.json

Each case runs on a fresh in-memory database; nothing touches your data or any outside service. Results go to
evals/results/tasks-<UTC time>.json and one line in evals/history.jsonl (kind "tasks"), next to the routing eval's.
Prices for the cost column are read from evals/prices.json (set them for your model; unknown means no cost shown).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.llm.factory import build_llm, model_name
from evals.tasks import CASES_PATH, PRICES_PATH, load_cases, run_case, summarize

HERE = Path(__file__).parent


def _model_settings(s) -> dict:
    """Only what selects and configures the model: the benchmark never uses your integrations or data."""
    keys = ("anthropic_api_key", "groq_api_key", "gemini_api_key", "llm_provider", "llm_fallback", "agent_model", "gemini_model",
            "ollama_base_url", "ollama_model", "ollama_num_ctx", "ollama_timeout_seconds", "llm_temperature", "max_agent_steps",
            "max_output_tokens", "tool_profile", "history_message_limit")
    return {k: getattr(s, k) for k in keys}


def main(argv: list[str] | None = None, llm=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=Path, default=CASES_PATH)
    ap.add_argument("--ids", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0, metavar="SECONDS")
    ap.add_argument("--rerun-errors", type=Path, default=None, metavar="RESULTS_JSON")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    if args.rerun_errors:
        errored = {r["id"] for r in json.loads(args.rerun_errors.read_text(encoding="utf-8"))["results"] if r.get("error")}
        cases = [c for c in cases if c["id"] in errored]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            print(f"Unknown case id(s): {', '.join(sorted(unknown))}")
            return 2
        cases = [c for c in cases if c["id"] in wanted]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.tag:
        cases = [c for c in cases if args.tag in c.get("tags", [])]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No cases selected.")
        return 2

    s = get_settings()
    model = model_name(s)
    llm = llm or build_llm(s)
    prices = json.loads(PRICES_PATH.read_text(encoding="utf-8")) if PRICES_PATH.exists() else {}
    print(f"Task benchmark: {len(cases)} cases, model={model}")
    results = []
    for n, case in enumerate(cases):
        if n and args.sleep:
            time.sleep(args.sleep)
        r = run_case(case, llm, _model_settings(s), prices, model)
        results.append(r)
        mark = "ERROR" if r.error else "UNSAFE" if not r.safe else "DONE" if r.completed else "FAIL"
        cost = f" ${r.cost_usd:.4f}" if r.cost_usd is not None else ""
        print(f"  {mark:7} {r.id:30} {r.llm_calls} calls, {r.seconds:.1f} s, {r.input_tokens or '?'}+{r.output_tokens or '?'} tokens{cost}")
        for p in ([r.error] if r.error else []) + r.safety_problems + r.problems:
            print(f"          - {p}")

    summary = summarize(results)

    def pct(v):
        return "n/a" if v is None else f"{v:.0%}"

    print(f"\nCompleted: {pct(summary['completed'])} of {summary['answered']} answered cases   (the job actually got done)")
    print(f"Safe:      {pct(summary['safe'])}   (nothing forbidden ran, nothing unasked-for changed)")
    print(f"Memory:    {pct(summary['memory'])}   (memory cases: the right fact came back)")
    if summary["errors"]:
        print(f"{summary['errors']} case(s) got no answer (provider error); re-run them with --rerun-errors <this run's file>.")
    print(f"Time per case: median {summary['seconds_p50']} s, 90th percentile {summary['seconds_p90']} s")
    print(f"LLM calls: {summary['llm_calls_total']}; tokens: {summary['input_tokens_total']} in, {summary['output_tokens_total']} out"
          + (f"; cost ${summary['cost_usd_total']}" if summary["cost_usd_total"] is not None else "; cost: set a price in evals/prices.json"))
    print("By category: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in summary["by_category"].items()))

    if not args.no_save:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = args.out / f"tasks-{stamp}.json"
        path.write_text(json.dumps({"kind": "tasks", "model": model, "summary": summary, "results": [r.__dict__ for r in results]},
                                   indent=2, ensure_ascii=False), encoding="utf-8")
        with (HERE / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": stamp, "kind": "tasks", "model": model, "completed": summary["completed"], "safe": summary["safe"],
                                 "memory": summary["memory"], "errors": summary["errors"], "n": summary["n"]}) + "\n")
        print(f"\nSaved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
