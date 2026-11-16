import json

import pytest

from evals.common import check_args, full_registry, judge, load_cases, run_eval, score_case
from evals.run_routing import main
from tests.fakes import text, tool_call


class RoutingStub:
    """An LLM that answers every case with a fixed policy: first acceptable tool, or text when none is expected."""

    def __init__(self, cases, wrong=()):
        self.by_prompt = {c["prompt"]: c for c in cases}
        self.wrong = set(wrong)

    def complete(self, *, system, messages, tools):
        case = self.by_prompt[messages[0]["content"][0]["text"]]
        if case["id"] in self.wrong:
            return tool_call("delete_memory", {"memory_id": 1})
        if not case["expect"]:
            return text("ok")
        tool = case["expect"][0]
        return tool_call(tool, satisfying(case.get("args", {}).get(tool, {})))


def satisfying(spec: dict) -> dict:
    """Arguments that meet a case's argument checks (what a perfect model would send)."""
    out = {}
    for name, rule in spec.items():
        if not isinstance(rule, dict):
            out[name] = rule
        elif rule.get("absent"):
            continue
        else:
            key = next(k for k in ("any", "contains", "date", "datetime", "min", "max") if k in rule)
            value = rule[key]
            out[name] = value[0] if isinstance(value, list) else value
    return out


def test_dataset_is_well_formed_and_every_expected_tool_exists():
    cases, tools = load_cases(), {t.name for t in full_registry().all()}
    assert len(cases) >= 150
    assert len({c["id"] for c in cases}) == len(cases) and len({c["prompt"] for c in cases}) == len(cases)
    for c in cases:
        assert c["prompt"].strip() and c["category"] and isinstance(c["expect"], list)
        assert set(c["expect"]) <= tools, (c["id"], set(c["expect"]) - tools)
        if not c["expect"]:
            assert c.get("allow_none"), f"{c['id']} expects nothing, so it must set allow_none"


def test_every_tool_is_exercised_by_at_least_one_case():
    cases = load_cases()
    covered = {t for c in cases for t in c["expect"]}
    uncovered = {t.name for t in full_registry().all()} - covered - {"load_tools"}  # load_tools is a helper, scored via lean mode
    assert uncovered == set(), f"add routing cases for: {sorted(uncovered)}"


def test_judge_semantics():
    assert judge({"expect": ["a", "b"]}, "b") and not judge({"expect": ["a"]}, "c")
    assert not judge({"expect": ["a"]}, None) and judge({"expect": ["a"], "allow_none": True}, None)
    assert not judge({"expect": [], "allow_none": True}, "a")  # "must not call a tool" really means it


def test_scoring_and_report_breakdown():
    cases = load_cases()
    perfect = run_eval(RoutingStub(cases), cases, "stub")
    assert perfect.score == 1.0 and perfect.failures() == []
    bad = run_eval(RoutingStub(cases, wrong={"task-list-1", "chat-hi"}), cases, "stub")
    assert [f.id for f in bad.failures()] == ["task-list-1", "chat-hi"] and bad.score == pytest.approx(1 - 2 / len(cases))
    cat = bad.by_category()
    assert cat["tasks"][0] == cat["tasks"][1] - 1 and cat["no_tool"][0] == cat["no_tool"][1] - 1


def test_provider_errors_count_as_failures_not_crashes():
    class Down:
        def complete(self, **_):
            raise RuntimeError("503")

    r = run_eval(Down(), load_cases()[:3], "x")
    assert r.score == 0 and all(x.error and "503" in x.error for x in r.results)


def test_full_profile_hands_the_model_every_tool_lean_a_subset():
    seen = {}

    class Spy:
        def complete(self, *, system, messages, tools):
            seen["tools"], seen["system"] = {t["name"] for t in tools}, system
            return text("ok")

    domain = {t.name for t in full_registry().all()} - {"load_tools"}
    run_eval(Spy(), load_cases()[:1], "x", profile="full")
    assert seen["tools"] == domain and "Monday, 09 November 2026" in seen["system"] and "More tools" not in seen["system"]
    run_eval(Spy(), [{"id": "x", "category": "c", "prompt": "Hi!", "expect": [], "allow_none": True}], "x", profile="lean")
    assert "create_task" in seen["tools"] and "load_tools" in seen["tools"] and "web_search" not in seen["tools"] and "More tools" in seen["system"]


def test_load_tools_for_the_right_group_counts_as_a_pass_in_lean_mode():
    case = {"expect": ["create_reminder"]}
    assert judge(case, "load_tools", ["reminders"]) and not judge(case, "load_tools", ["web"]) and not judge(case, "load_tools", [])
    assert not judge({"expect": [], "allow_none": True}, "load_tools", ["web"])  # small talk must not load anything


def test_cli_runs_saves_results_and_enforces_min_score(tmp_path, monkeypatch, capsys):
    cases = load_cases()
    out = tmp_path / "res"
    monkeypatch.setattr("evals.run_routing.HERE", tmp_path)  # keep history.jsonl out of the repo
    assert main(["--out", str(out), "--min-score", "0.9"], llm=RoutingStub(cases)) == 0
    saved = list(out.glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["score"] == 1.0
    assert json.loads((tmp_path / "history.jsonl").read_text().splitlines()[-1])["score"] == 1.0
    assert main(["--out", str(out), "--no-save", "--min-score", "0.9"], llm=RoutingStub(cases, wrong={c["id"] for c in cases[:20]})) == 1
    assert "FAILED" in capsys.readouterr().out
    assert main(["--category", "nonexistent", "--no-save"], llm=RoutingStub(cases)) == 2


def test_pause_between_cases_is_applied_between_but_not_before_the_first(monkeypatch):
    cases = load_cases()[:3]
    waits: list[float] = []
    run_eval(RoutingStub(cases), cases, "stub", pause_seconds=20, sleep=waits.append)
    assert waits == [20, 20]
    run_eval(RoutingStub(cases), cases, "stub", sleep=waits.append)  # default: no pause
    assert waits == [20, 20]
    import evals.common as common
    monkeypatch.setattr(common.time, "sleep", waits.append)
    from evals.run_routing import main
    assert main(["--limit", "2", "--sleep", "5", "--no-save"], llm=RoutingStub(cases)) == 0
    assert waits == [20, 20, 5]


MATCHERS = {"any", "contains", "date", "datetime", "min", "max", "absent"}
TAGS = {"regression", "deadline-nl", "ambiguous", "multi-step", "safety", "urdu", "ask-first"}


def test_argument_checks_name_real_fields_of_an_acceptable_tool_and_use_known_matchers():
    from datetime import date, datetime

    tools = {t.name: t for t in full_registry().all()}
    for c in load_cases():
        assert set(c.get("tags", [])) <= TAGS, (c["id"], set(c["tags"]) - TAGS)
        for tool, spec in c.get("args", {}).items():
            assert tool in c["expect"], f"{c['id']}: args for {tool}, which is not an acceptable tool"
            fields = tools[tool].input_model.model_json_schema()["properties"]
            for name, rule in spec.items():
                assert name in fields, f"{c['id']}: {tool} has no argument {name!r}"
                if isinstance(rule, dict):
                    kinds = {"range" if k in ("min", "max") else k for k in rule}  # min/max together are one range matcher
                    assert set(rule) <= MATCHERS and len(kinds) == 1, (c["id"], rule)
                    for d in [rule["date"]] if isinstance(rule.get("date"), str) else rule.get("date", []):
                        date.fromisoformat(d)
                    for dt in [rule["datetime"]] if isinstance(rule.get("datetime"), str) else rule.get("datetime", []):
                        datetime.fromisoformat(dt)


def test_dataset_covers_the_hard_cases_the_review_asked_for():
    cases = load_cases()
    tags = [t for c in cases for t in c.get("tags", [])]
    assert tags.count("deadline-nl") >= 25 and tags.count("multi-step") >= 6 and tags.count("ambiguous") >= 5
    assert tags.count("safety") >= 4 and tags.count("urdu") >= 6
    assert sum("args" in c for c in cases) >= 50
    # every case that has failed in a real run stays in the set as a regression case
    regression = {c["id"] for c in cases if "regression" in c.get("tags", [])}
    assert {"remind-create", "plan-create", "plan-get", "commit-move", "gh-repos", "commit-complete"} <= regression


def test_check_args_matchers():
    spec = {"remind_at": {"datetime": "2026-11-10T09:00"}, "text": {"contains": ["khan", "hod"]}, "minutes": {"min": 60, "max": 60},
            "priority": "High", "status": {"any": ["open", "todo"]}, "due": {"date": ["2026-11-13", "2026-11-20"]}, "owner": {"absent": True}}
    good = {"remind_at": "2026-11-10T09:00:00", "text": "Email Dr. KHAN", "minutes": 60, "priority": "high", "status": "todo", "due": "2026-11-20"}
    assert check_args(spec, good) == []
    assert check_args(spec, {**good, "remind_at": "2026-11-10T04:00:00Z"}) == []  # same instant, given in UTC
    assert check_args(spec, {**good, "remind_at": "2026-11-10T09:00+05:00"}) == []
    bad = check_args(spec, {**good, "remind_at": "2026-11-11T09:00", "text": "call mom", "minutes": 45, "owner": "me", "due": "nope"})
    assert len(bad) == 5 and any("remind_at" in b for b in bad) and any("owner should be unset" in b for b in bad)
    assert check_args({"minutes": 90}, {}) == ["minutes missing"]


def test_outcomes_separate_missed_wrong_bad_args_and_flag_unsafe_writes():
    perms = {"create_reminder": "write", "list_reminders": "read", "delete_task": "high_impact", "list_tasks": "read"}
    case = {"expect": ["create_reminder"], "args": {"create_reminder": {"remind_at": {"datetime": "2026-11-10T09:00"}}}}
    ok = score_case(case, "create_reminder", {"remind_at": "2026-11-10T09:00"}, [], None, perms)
    assert ok["outcome"] == "pass" and ok["passed"] and ok["routing_ok"]
    wrong_time = score_case(case, "create_reminder", {"remind_at": "2026-11-10T21:00"}, [], None, perms)
    assert wrong_time["outcome"] == "bad_args" and wrong_time["routing_ok"] and not wrong_time["passed"]
    assert score_case(case, None, {}, [], None, perms)["outcome"] == "missed"
    read_detour = score_case(case, "list_reminders", {}, [], None, perms)
    assert read_detour["outcome"] == "wrong_tool" and not read_detour["unsafe"]  # wrong, but harmless
    asked_advice = {"expect": ["list_tasks"], "allow_none": True}
    deleted = score_case(asked_advice, "delete_task", {"task_id": 1}, [], None, perms)
    assert deleted["outcome"] == "wrong_tool" and deleted["unsafe"]
    assert score_case(case, None, {}, [], "RuntimeError: 503", perms)["outcome"] == "error"


def test_report_counts_outcomes_tags_and_unsafe_calls(capsys):
    cases = load_cases()
    report = run_eval(RoutingStub(cases, wrong={"task-delete-unsure", "chat-hi"}), cases, "stub")
    assert report.outcomes()["wrong_tool"] == 2 and {r.id for r in report.unsafe()} == {"task-delete-unsure", "chat-hi"}
    assert report.routing_score == report.score == pytest.approx(1 - 2 / len(cases))
    assert report.by_tag()["safety"][0] == report.by_tag()["safety"][1] - 1
    assert main(["--tag", "regression", "--no-save"], llm=RoutingStub(cases)) == 0
    out = capsys.readouterr().out
    assert "Routing:" in out and "Unsafe wrong calls" in out and "By tag:" in out and "regression" in out


def test_rerun_errors_runs_only_the_cases_that_got_no_answer(tmp_path, capsys):
    cases = load_cases()
    saved = tmp_path / "run.json"
    saved.write_text(json.dumps({"results": [{"id": cases[0]["id"], "outcome": "pass"}, {"id": cases[1]["id"], "outcome": "error", "error": "429"}]}))
    assert main(["--rerun-errors", str(saved), "--no-save"], llm=RoutingStub(cases)) == 0
    out = capsys.readouterr().out
    assert "Routing eval: 1 cases" in out and cases[1]["id"] in out and cases[0]["id"] not in out


def test_provider_errors_are_reported_separately_from_answered_cases(capsys):
    class HalfDown(RoutingStub):
        def complete(self, *, system, messages, tools):
            if messages[0]["content"][0]["text"] == self.down:
                raise RuntimeError("429 rate limit")
            return super().complete(system=system, messages=messages, tools=tools)

    cases = load_cases()[:4]
    llm = HalfDown(cases)
    llm.down = cases[3]["prompt"]
    from unittest.mock import patch

    with patch("evals.run_routing.load_cases", return_value=cases):
        main(["--no-save"], llm=llm)
    out = capsys.readouterr().out
    assert "1 case(s) got no answer" in out and "100% of the 3 answered cases passed" in out
