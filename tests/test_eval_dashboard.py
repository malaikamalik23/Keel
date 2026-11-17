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


def test_latency_and_tokens_are_recorded_per_case_and_shown_per_run(tmp_path):
    from app.llm.base import LLMResponse
    from evals.common import cost_summary, percentile, run_eval

    class Metered:
        def __init__(self):
            self.n = 0

        def complete(self, *, system, messages, tools):
            self.n += 1
            if self.n == 3:
                raise RuntimeError("429")
            return LLMResponse([{"type": "tool_use", "id": "1", "name": "create_task", "input": {"title": "x"}}], "tool_use",
                               input_tokens=3000 + self.n, output_tokens=40)

    report = run_eval(Metered(), CASES, profile="full")
    a, b, c = report.results
    assert a.input_tokens == 3001 and a.output_tokens == 40 and a.latency_ms is not None and a.latency_ms >= 0
    assert c.outcome == "error" and c.latency_ms is None and c.input_tokens is None  # a failed request costs nothing here

    cost = cost_summary(report.results)
    assert cost["input_tokens_p50"] == 3001 and cost["input_tokens_total"] == 6003 and cost["output_tokens_total"] == 80
    assert percentile([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 90) == 90 and percentile([None], 50) is None

    saved = [dict(r.__dict__, latency_ms=ms) for r, ms in zip(report.results, (800, 1200, None), strict=True)]
    runs = _write(tmp_path, [("20261101-100000", [_result("a", "pass")], "before these were recorded"),
                             ("20261102-100000", saved, "metered")])
    page = render(CASES, runs)
    assert "800 ms" in page and "1,200 ms" in page and "3,001" in page  # median and 90th percentile, tokens in
    old_row = page.split("before these were recorded")[0].rsplit("<tr>", 1)[1]
    assert old_row.count("–") == 4  # the older run shows dashes, not zeros


def test_another_models_answers_never_change_the_main_view_and_are_compared_separately(tmp_path):
    def run(model, at, results, note=""):
        (tmp_path / "results" / f"{at}.json").write_text(json.dumps({"results": results}), encoding="utf-8")
        return json.dumps({"at": at, "model": model, "n": len(results), "unsafe": 0, "note": note}) + "\n"

    (tmp_path / "results").mkdir()
    lines = [
        run("big", "20261101-100000", [_result("a", "wrong_tool", "list_tasks"), _result("b", "pass"), _result("c", "pass")]),
        run("big", "20261102-100000", [_result("a", "pass"), _result("b", "pass"), _result("c", "error")]),
        run("small", "20261103-100000", [_result("a", "wrong_tool", "list_tasks"), _result("b", "wrong_tool", "x"), _result("c", "pass")], "other model"),
    ]
    (tmp_path / "history.jsonl").write_text("".join(lines), encoding="utf-8")
    runs = load_runs(tmp_path / "history.jsonl", tmp_path / "results")
    page = render(CASES, runs)

    assert "model <code>big</code>" in page          # two runs beat one: "big" is the main view
    assert "3 of 3 answered cases pass" in page     # the later, worse "small" run did not overwrite big's answers
    compare = page.split("Models compared")[1].split("</table>")[0]
    assert "<code>big</code>" in compare and "3/3" in compare
    assert "<code>small</code>" in compare and "1/3" in compare
    assert "01 Nov 10:00: 2/3" in compare            # big's first run was a full run; its second was not (an error)
    assert "03 Nov 10:00: 1/3" in compare
    assert "Runs of other models" in page and "other model" in page
