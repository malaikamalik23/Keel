"""Shared pieces for the tool-routing eval: dataset loading, a registry with every tool, scoring."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent.prompts import build_system_prompt
from app.integrations.github import GitHubClient
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
        github_user="me", notes_enabled=True,
    )


@dataclass
class CaseResult:
    id: str
    category: str
    prompt: str
    expected: list[str]
    allow_none: bool
    got: str | None  # first tool the model called, or None if it answered in text
    passed: bool
    error: str | None = None
    loaded_groups: list[str] = field(default_factory=list)  # groups requested when `got` is load_tools


@dataclass
class EvalReport:
    model: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for r in self.results:
            d = out.setdefault(r.category, [0, 0])
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


def run_eval(
    llm: LLMClient, cases: list[dict], model: str = "?", on_result=None, profile: str = "lean", pause_seconds: float = 0, sleep=None
) -> EvalReport:
    """Score the model's first decision, using exactly the tools and system prompt the app would send for each prompt.
    pause_seconds waits between cases (not before the first), for token-per-minute limited free tiers."""
    registry = full_registry()
    available = registry.available_groups()
    report = EvalReport(model=model)
    for n, case in enumerate(cases):
        if n and pause_seconds > 0:
            (sleep or time.sleep)(pause_seconds)
        got, error, loaded = None, None, []
        try:
            if profile == "lean":
                active = select_groups(case["prompt"], set(), available)
                system, tools = build_system_prompt(FIXED_NOW, more_tools=not_loaded(active, available)), registry.specs(active)
            else:
                system, tools = build_system_prompt(FIXED_NOW), registry.specs()
            resp = llm.complete(system=system, messages=[{"role": "user", "content": [{"type": "text", "text": case["prompt"]}]}], tools=tools)
            uses = resp.tool_uses if resp.stop_reason == "tool_use" else []
            got = uses[0]["name"] if uses else None
            if got == LOAD_TOOLS:
                loaded = list(uses[0]["input"].get("groups", []))
        except Exception as exc:  # noqa: BLE001 - a provider hiccup is a failed case, not a crashed run
            error = f"{type(exc).__name__}: {exc}"[:200]
        passed = error is None and judge(case, got, loaded)
        res = CaseResult(case["id"], case["category"], case["prompt"], case.get("expect", []), case.get("allow_none", False), got, passed, error, loaded)
        report.results.append(res)
        if on_result:
            on_result(res)
    return report
