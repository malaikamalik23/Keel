"""Shared pieces for the tool-routing eval: dataset loading, a registry with every tool, scoring."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent.prompts import build_system_prompt
from app.integrations.github import GitHubClient
from app.integrations.gmail import GmailClient
from app.integrations.safe_http import SafeFetcher
from app.integrations.websearch import BraveSearch
from app.llm.base import LLMClient
from app.tools import build_default_registry
from app.tools.groups import LOAD_TOOLS, group_of, not_loaded, select_groups

CASES_PATH = Path(__file__).parent / "routing_cases.json"
FIXED_NOW = datetime(2026, 11, 9, 9, 30, tzinfo=ZoneInfo("Asia/Karachi"))  # a Monday; keeps prompts reproducible


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def full_registry():
    """A registry with every optional integration switched on. Only the specs matter: nothing is ever executed."""
    return build_default_registry(
        calendar_source=lambda: "", fetcher=SafeFetcher(), search=BraveSearch("unused"), github=GitHubClient(),
        github_user="me", notes_enabled=True, notes_dir="notes",
        gmail=GmailClient("unused.apps.googleusercontent.com", None, Path("unused-token.json")),
    )


OUTCOMES = ("pass", "missed", "wrong_tool", "bad_args", "error")


@dataclass
class CaseResult:
    id: str
    category: str
    prompt: str
    expected: list[str]
    allow_none: bool
    got: str | None  # first tool the model called, or None if it answered in text
    passed: bool  # right tool (or no tool where allowed) AND its arguments match the case's "args" checks
    error: str | None = None
    loaded_groups: list[str] = field(default_factory=list)  # groups requested when `got` is load_tools
    routing_ok: bool = False  # right tool, ignoring arguments (comparable with runs before argument checks existed)
    outcome: str = "pass"  # one of OUTCOMES
    arg_problems: list[str] = field(default_factory=list)
    unsafe: bool = False  # called a tool that writes or deletes when that tool was not acceptable
    tags: list[str] = field(default_factory=list)
    got_input: dict = field(default_factory=dict)
    latency_ms: int | None = None  # wall-clock time of the one LLM request (None if it errored)
    input_tokens: int | None = None  # as reported by the provider: system prompt + tool definitions + the message
    output_tokens: int | None = None


@dataclass
class EvalReport:
    model: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def routing_score(self) -> float:
        return sum(r.routing_ok for r in self.results) / len(self.results) if self.results else 0.0

    def outcomes(self) -> dict[str, int]:
        return {o: sum(r.outcome == o for r in self.results) for o in OUTCOMES}

    def unsafe(self) -> list[CaseResult]:
        return [r for r in self.results if r.unsafe]

    def by_category(self) -> dict[str, tuple[int, int]]:
        return self._group(lambda r: [r.category])

    def by_tag(self) -> dict[str, tuple[int, int]]:
        return self._group(lambda r: r.tags)

    def _group(self, keys) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for r in self.results:
            for k in keys(r):
                d = out.setdefault(k, [0, 0])
                d[0] += r.passed
                d[1] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]


def judge(case: dict, got: str | None, loaded_groups: list[str] | None = None) -> bool:
    """Pass if the first call is an acceptable tool, or no tool where that's acceptable. In the lean profile a
    load_tools call for the group that holds an acceptable tool also passes: it is the intended route to a tool that
    was not advertised in the first request."""
    expect, allow_none = case.get("expect", []), case.get("allow_none", False)
    if got == LOAD_TOOLS:
        return any(group_of(t) in (loaded_groups or []) for t in expect)
    return (got in expect) if got is not None else allow_none


def _as_local_minute(value) -> str | None:
    """'2026-11-10T09:00', '...T09:00:00', '...+05:00' or '...Z' -> 'YYYY-MM-DDTHH:MM' in the eval's timezone."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(FIXED_NOW.tzinfo)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _as_date(value) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def check_args(spec: dict, given: dict) -> list[str]:
    """Compare a tool call's arguments with a case's expectations. Matchers (per field):
    a plain value (strings compared case-insensitively) · {"any": [...]} · {"contains": "text"} or {"contains": [...]}
    (any one, case-insensitive) · {"date": "YYYY-MM-DD" or [...]} · {"datetime": "YYYY-MM-DDTHH:MM" or [...]} (local
    time; offsets converted) · {"min": n, "max": n} · {"absent": true} (must be missing or null)."""
    problems = []
    for name, rule in spec.items():
        value = given.get(name)
        if isinstance(rule, dict) and rule.get("absent"):
            if value not in (None, "", []):
                problems.append(f"{name} should be unset, got {value!r}")
            continue
        if value in (None, ""):
            problems.append(f"{name} missing")
            continue
        if not isinstance(rule, dict):
            ok = str(value).strip().lower() == rule.strip().lower() if isinstance(rule, str) else value == rule
        elif "any" in rule:
            ok = any((str(value).lower() == v.lower()) if isinstance(v, str) else value == v for v in rule["any"])
        elif "contains" in rule:
            needles = rule["contains"] if isinstance(rule["contains"], list) else [rule["contains"]]
            ok = any(n.lower() in str(value).lower() for n in needles)
        elif "date" in rule:
            want = rule["date"] if isinstance(rule["date"], list) else [rule["date"]]
            ok = _as_date(value) in want
        elif "datetime" in rule:
            want = rule["datetime"] if isinstance(rule["datetime"], list) else [rule["datetime"]]
            ok = _as_local_minute(value) in want
        elif "min" in rule or "max" in rule:
            try:
                ok = rule.get("min", float("-inf")) <= float(value) <= rule.get("max", float("inf"))
            except (TypeError, ValueError):
                ok = False
        else:
            raise ValueError(f"unknown matcher for {name}: {rule}")
        if not ok:
            problems.append(f"{name}={value!r} does not match {rule}")
    return problems


def score_case(case: dict, got: str | None, got_input: dict, loaded: list[str], error: str | None, permissions: dict[str, str]) -> dict:
    """Decide one case's outcome. Argument checks apply only when the first call is a tool the case has 'args' for."""
    routing_ok = error is None and judge(case, got, loaded)
    problems = check_args(case["args"][got], got_input) if routing_ok and got in case.get("args", {}) else []
    if error is not None:
        outcome = "error"
    elif not routing_ok:
        outcome = "missed" if got is None else "wrong_tool"
    else:
        outcome = "bad_args" if problems else "pass"
    unsafe = outcome == "wrong_tool" and permissions.get(got or "", "read") != "read"
    return {"routing_ok": routing_ok, "passed": outcome == "pass", "outcome": outcome, "arg_problems": problems, "unsafe": unsafe}


def run_eval(
    llm: LLMClient, cases: list[dict], model: str = "?", on_result=None, profile: str = "lean", pause_seconds: float = 0, sleep=None
) -> EvalReport:
    """Score the model's first decision, using exactly the tools and system prompt the app would send for each prompt.
    pause_seconds waits between cases (not before the first), for token-per-minute limited free tiers."""
    registry = full_registry()
    available = registry.available_groups()
    permissions = {t.name: t.permission.value for t in registry.all()}
    report = EvalReport(model=model)
    for n, case in enumerate(cases):
        if n and pause_seconds > 0:
            (sleep or time.sleep)(pause_seconds)
        got, error, loaded, got_input = None, None, [], {}
        latency_ms = tokens_in = tokens_out = None
        try:
            if profile == "lean":
                active = select_groups(case["prompt"], set(), available, case.get("projects", ()))
                system, tools = build_system_prompt(FIXED_NOW, more_tools=not_loaded(active, available)), registry.specs(active)
            else:
                system, tools = build_system_prompt(FIXED_NOW), registry.specs()
            started = time.perf_counter()
            resp = llm.complete(system=system, messages=[{"role": "user", "content": [{"type": "text", "text": case["prompt"]}]}], tools=tools)
            latency_ms = round((time.perf_counter() - started) * 1000)
            tokens_in, tokens_out = getattr(resp, "input_tokens", None), getattr(resp, "output_tokens", None)
            uses = resp.tool_uses if resp.stop_reason == "tool_use" else []
            got = uses[0]["name"] if uses else None
            got_input = dict(uses[0].get("input") or {}) if uses else {}
            if got == LOAD_TOOLS:
                loaded = list(got_input.get("groups", []))
        except Exception as exc:  # noqa: BLE001 - a provider hiccup is a failed case, not a crashed run
            error = f"{type(exc).__name__}: {exc}"[:200]
        verdict = score_case(case, got, got_input, loaded, error, permissions)
        res = CaseResult(
            case["id"], case["category"], case["prompt"], case.get("expect", []), case.get("allow_none", False), got,
            verdict["passed"], error, loaded, verdict["routing_ok"], verdict["outcome"], verdict["arg_problems"],
            verdict["unsafe"], list(case.get("tags", [])), got_input, latency_ms, tokens_in, tokens_out,
        )
        report.results.append(res)
        if on_result:
            on_result(res)
    return report


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0-100) of the values that are present; None if there are none."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))]


def cost_summary(results: list) -> dict:
    """Latency and token use of the answered cases. Accepts CaseResult objects or their saved dicts."""
    get = (lambda r, k: r.get(k)) if results and isinstance(results[0], dict) else (lambda r, k: getattr(r, k, None))
    done = [r for r in results if get(r, "outcome") != "error"]
    lat = [get(r, "latency_ms") for r in done]
    tin, tout = [get(r, "input_tokens") for r in done], [get(r, "output_tokens") for r in done]
    return {
        "latency_ms_p50": percentile(lat, 50), "latency_ms_p90": percentile(lat, 90),
        "input_tokens_p50": percentile(tin, 50), "output_tokens_p50": percentile(tout, 50),
        "input_tokens_total": sum(v for v in tin if v is not None) if any(v is not None for v in tin) else None,
        "output_tokens_total": sum(v for v in tout if v is not None) if any(v is not None for v in tout) else None,
    }
