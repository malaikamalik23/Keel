# Tool-routing eval

Keel's model decides which tool to call from the tool descriptions and the system prompt. This eval **measures** that
instead of guessing: `routing_cases.json` holds ~55 realistic user messages and the tool(s) that are acceptable as the
model's first call (or "no tool" for small talk and for questions the agent should ask back, like "plan my day" with
no time given).

```bash
python -m evals.run_routing                  # needs ANTHROPIC_API_KEY or GROQ_API_KEY; ~55 API calls
python -m evals.run_routing --category web   # one category
python -m evals.run_routing --min-score 0.85 # non-zero exit if below (use in a scheduled job)
```

Each run prints per-category scores and every failure (what the model did vs what was acceptable), saves
`results/<timestamp>.json`, and appends a line to `history.jsonl`. When you change a tool description or the system
prompt, re-run it and compare. When a failure turns out to be a *reasonable* alternative, add it to that case's
`expect` list; when it's a real mistake, fix the description.

The dataset itself is validated offline in `tests/test_evals.py` (every expected tool exists, no duplicates, every
tool is covered), so CI never needs an API key.
