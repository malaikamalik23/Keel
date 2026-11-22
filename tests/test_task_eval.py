"""The task benchmark (evals/tasks.py), offline: the dataset is valid, and the harness really runs the agent, checks the
database and the reply, catches unsafe behaviour, and counts tokens and cost. A scripted model stands in for the real one."""
from __future__ import annotations

import pytest

from app.llm.base import LLMResponse
from evals.tasks import DATE_FIELDS, _tables, load_cases, run_case, summarize
from tests.fakes import text, tool_call

CASES = load_cases()
CATEGORIES = {"followup", "multi", "intent", "ask-first", "memory"}


class Scripted:
    def __init__(self, steps, tokens=(1000, 50)):
        self.steps, self.tokens = list(steps), tokens

    def complete(self, **_):
        r = self.steps.pop(0)
        return LLMResponse(r.content, r.stop_reason, *self.tokens)


def test_the_dataset_is_valid():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) and len(CASES) >= 25
    tables = _tables()
    for c in CASES:
        assert c["category"] in CATEGORIES and c["turns"], c["id"]
        assert c.get("expect_state") or c.get("unchanged") or c.get("reply"), f"{c['id']} checks nothing"
        for table, rows in c.get("setup", {}).items():
            cols = {col.key for col in tables[table].__table__.columns}
            for row in rows:
                assert set(row) - {"created_days_ago"} <= cols, (c["id"], set(row) - cols)
        for e in c.get("expect_state", []):
            cols = {col.key for col in tables[e["table"]].__table__.columns}
            assert set(e.get("where", {})) | set(e.get("fields", {})) <= cols, (c["id"], e)
        assert set(c.get("unchanged", [])) <= set(tables), c["id"]
    assert {c["category"] for c in CASES} == CATEGORIES
    assert DATE_FIELDS  # seeded relative dates ("+2") resolve against the fixed Monday


def test_a_follow_up_yes_is_followed_through_and_scored_as_completed():
    case = next(c for c in CASES if c["id"] == "fu-promise-yes")
    llm = Scripted([
        tool_call("propose_commitment", {"text": "Finish the GRE quant set", "deadline": "2026-11-13"}), text("Shall I track it?"),
        tool_call("confirm_commitment", {"commitment_id": 1}), text("Tracking it."),
    ])
    r = run_case(case, llm, prices={"m": {"input_per_million": 1.0, "output_per_million": 2.0}}, model="m")
    assert r.completed and r.safe and r.error is None, (r.problems, r.safety_problems, r.error)
    assert r.tools == ["propose_commitment:ok", "confirm_commitment:ok"] and r.llm_calls == 4
    assert (r.input_tokens, r.output_tokens) == (4000, 200) and r.cost_usd == pytest.approx(0.0044)


def test_stopping_halfway_is_not_completed():
    case = next(c for c in CASES if c["id"] == "fu-promise-yes")
    llm = Scripted([tool_call("propose_commitment", {"text": "Finish the GRE quant set", "deadline": "2026-11-13"}), text("Ok?"),
                    text("Great, good luck!")])  # never confirmed
    r = run_case(case, llm)
    assert not r.completed and r.safe and "status='proposed'" in " ".join(r.problems)


def test_guessing_which_task_to_delete_is_unsafe_even_though_it_only_queues():
    case = next(c for c in CASES if c["id"] == "ask-which-task")
    r = run_case(case, Scripted([tool_call("delete_task", {"task_id": 2}), text("Queued.")]))
    assert not r.safe and not r.completed and "pending_actions changed" in r.safety_problems[0]
    assert run_case(case, Scripted([text("Which one: the FYP intro, the GRE set or calling the bank?")])).completed


def test_an_approved_delete_happens_and_the_other_task_stays():
    case = next(c for c in CASES if c["id"] == "fu-delete-then-approve")
    r = run_case(case, Scripted([tool_call("delete_task", {"task_id": 1}), text("Waiting for your approval.")]))
    assert r.completed, r.problems
    r = run_case(case, Scripted([text("Sure, I deleted it.")]))  # claims it, did nothing
    assert not r.completed and any("waiting for approval" in p for p in r.problems)


def test_memory_answers_are_checked_against_what_was_saved():
    case = next(c for c in CASES if c["id"] == "mem-contradiction-newer-wins")
    assert run_case(case, Scripted([text("Your CGPA is 3.6 (it was 3.4 earlier).")])).completed
    assert not run_case(case, Scripted([text("Your CGPA is 3.4.")])).completed
    invented = next(c for c in CASES if c["id"] == "mem-not-known")
    assert not run_case(invented, Scripted([text("It's AB1234567.")])).completed
    assert run_case(invented, Scripted([text("I don't have your passport number saved.")])).completed


def test_a_provider_error_is_an_unanswered_case_not_a_failure():
    class Down:
        def complete(self, **_):
            from app.errors import LLMError
            raise LLMError("rate limited", retryable=True)

    r = run_case(CASES[0], Down())
    assert r.error and not r.completed
    s = summarize([r, run_case(next(c for c in CASES if c["id"] == "mem-reworded"), Scripted([text("You study at NUST.")]))])
    assert s["errors"] == 1 and s["answered"] == 1 and s["completed"] == 1.0 and s["memory"] == 1.0
