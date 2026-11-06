import json

import pytest

from evals.common import full_registry, judge, load_cases, run_eval
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
        return tool_call(case["expect"][0], {}) if case["expect"] else text("ok")


def test_dataset_is_well_formed_and_every_expected_tool_exists():
    cases, tools = load_cases(), {t.name for t in full_registry().all()}
    assert len(cases) >= 40
    assert len({c["id"] for c in cases}) == len(cases) and len({c["prompt"] for c in cases}) == len(cases)
    for c in cases:
        assert c["prompt"].strip() and c["category"] and isinstance(c["expect"], list)
        assert set(c["expect"]) <= tools, (c["id"], set(c["expect"]) - tools)
        if not c["expect"]:
            assert c.get("allow_none"), f"{c['id']} expects nothing, so it must set allow_none"


def test_every_tool_is_exercised_by_at_least_one_case():
    cases = load_cases()
    covered = {t for c in cases for t in c["expect"]}
    uncovered = {t.name for t in full_registry().all()} - covered
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


def test_tool_specs_handed_to_the_model_include_every_tool():
    seen = {}

    class Spy:
        def complete(self, *, system, messages, tools):
            seen["tools"], seen["system"] = {t["name"] for t in tools}, system
            return text("ok")

    run_eval(Spy(), load_cases()[:1], "x")
    assert seen["tools"] == {t.name for t in full_registry().all()} and "Monday, 09 November 2026" in seen["system"]


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
