import json

from evals.dashboard import combined, load_runs, render, status_of

CASES = [
    {"id": "a", "category": "tasks", "prompt": "<b>Add</b> a task", "expect": ["create_task"], "tags": ["regression"]},
    {"id": "b", "category": "tasks", "prompt": "List tasks", "expect": ["list_tasks"]},
    {"id": "c", "category": "web", "prompt": "Search", "expect": ["web_search"]},
]
LEAK = "Rate limit reached for model in organization org_SECRET123"


def _result(cid, outcome, got=None):
    return {"id": cid, "passed": outcome == "pass", "outcome": outcome, "got": got,
            "error": LEAK if outcome == "error" else None, "unsafe": False}


def _write(tmp_path, runs):
    (tmp_path / "results").mkdir()
    with (tmp_path / "history.jsonl").open("w", encoding="utf-8") as fh:
        for at, results, note in runs:
            fh.write(json.dumps({"at": at, "model": "m", "n": len(results), "unsafe": 0, "note": note}) + "\n")
            (tmp_path / "results" / f"{at}.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    return load_runs(tmp_path / "history.jsonl", tmp_path / "results")


def test_combined_status_uses_the_latest_answered_result_and_ignores_errors(tmp_path):
    runs = _write(tmp_path, [
        ("20261101-100000", [_result("a", "wrong_tool", "list_tasks"), _result("b", "pass"), _result("c", "error")], "full run"),
        ("20261102-100000", [_result("a", "pass", "create_task"), _result("b", "error")], "re-run"),
    ])
    state = combined(CASES, runs)
    assert {cid: status_of(r) for cid, r in state.items()} == {"a": "pass", "b": "pass", "c": "not run"}
    # an error in a later run never overwrites a real answer
    assert state["b"]["run"] == "20261101-100000"


def test_page_shows_honest_totals_escapes_text_and_never_copies_provider_errors(tmp_path):
    runs = _write(tmp_path, [
        ("20261101-100000", [_result("a", "wrong_tool", "list_tasks"), _result("b", "pass"), _result("c", "error")], "limit <hit>"),
    ])
    page = render(CASES, runs)
    assert "1 of 2 answered cases pass" in page  # the errored case is "not run", not a failure
    assert "of 3 cases not answered yet" in page
    assert "<b>Add</b>" not in page and "&lt;b&gt;Add&lt;/b&gt;" in page
    assert "limit &lt;hit&gt;" in page
    assert "org_SECRET123" not in page and "Rate limit reached" not in page
    assert "<script" not in page and "http" not in page.split("<main>")[1]  # self-contained, no external requests


def test_runs_without_a_results_file_still_appear_but_add_no_case_data(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "history.jsonl").write_text(json.dumps({"at": "20261101-100000", "model": "m", "n": 57, "score": 0.9}) + "\n", encoding="utf-8")
    runs = load_runs(tmp_path / "history.jsonl", tmp_path / "results")
    page = render(CASES, runs)
    assert "No run results yet." in page
    assert "<td class='num'>57</td>" in page
