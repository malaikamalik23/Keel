# Tool-routing eval

Keel's model decides which tool to call from the tool descriptions and the system prompt. This eval **measures** that
instead of guessing: `routing_cases.json` holds 160 realistic user messages, the tool(s) that are acceptable as the
model's first call (or "no tool" for small talk and for questions the agent should ask back, like "plan my day" with
no time given), and for 55 of them what the call's **arguments** must be.

```bash
python -m evals.run_routing --sleep 20         # all 160 cases (160 API calls; the pause keeps Groq's free tier under its TPM limit)
python -m evals.run_routing --tag regression   # only cases that have failed in a real run before
python -m evals.run_routing --category web     # one category
python -m evals.run_routing --profile full     # every tool advertised (default: your TOOL_PROFILE, lean)
python -m evals.run_routing --min-score 0.85   # non-zero exit if below (use in a scheduled job)
```

## What a run reports

| Line | Meaning |
|---|---|
| **Score** | right tool **and** right arguments (the headline number) |
| **Routing** | right tool, arguments not checked (comparable with runs before argument checks existed) |
| **Outcomes** | `pass` · `missed` (answered in text when a tool was needed) · `wrong_tool` · `bad_args` (right tool, wrong arguments) · `error` |
| **Unsafe wrong calls** | a `wrong_tool` that writes or deletes, e.g. deleting a task when the user only asked for advice. These matter most. |
| By category / by tag | where the misses are |

Each run also saves `results/<timestamp>.json` (every call and its arguments) and appends a line to `history.jsonl`.

## Writing cases

```json
{"id":"remind-tonight","category":"reminders","prompt":"Remind me tonight at 9 to call Ammi","expect":["create_reminder"],
 "tags":["deadline-nl"],"args":{"create_reminder":{"remind_at":{"datetime":"2026-11-09T21:00"},"text":{"contains":"ammi"}}}}
```

- Prompts are judged as if it is **Monday 9 November 2026, 09:30 in Asia/Karachi** (`FIXED_NOW`), so "tomorrow", "Friday" and
  "in 2 hours" have exact answers.
- `expect`: acceptable first tools. Include sensible look-ups (e.g. `list_tasks` before `update_task`, which needs an id).
  `allow_none: true` makes a text answer acceptable too.
- `args` (optional): checks per acceptable tool, applied only when the model's first call is that tool. Matchers: a plain value
  (strings case-insensitive), `{"any": [...]}`, `{"contains": "x"}` or a list of alternatives, `{"date": ...}`, `{"datetime": ...}`
  (local time; offsets are converted), `{"min": n, "max": n}`, `{"absent": true}`.
- `tags`: `regression` (failed in a real run before: never delete these), `deadline-nl` (natural-language dates), `ambiguous`,
  `multi-step` (only the first step is judged, any sensible first step passes), `safety` (read-only intent), `urdu` (Roman Urdu), `ask-first`.

When a failure turns out to be a *reasonable* alternative, add it to that case's `expect` list; when it's a real mistake, fix
the tool description or the system prompt, not the case. Every case that fails in a real run gets the `regression` tag.

In the **lean** profile the eval uses exactly the tools and system prompt the app would send for each prompt, and a
`load_tools` call for the group that contains an acceptable tool counts as a pass. The dataset is validated offline in
`tests/test_evals.py` (tools and argument names exist, matchers and dates are valid, every tool is covered, the regression
cases are present), so CI never needs an API key.

**Limits:** only the model's *first* decision is judged. A multi-step request passes if it starts sensibly; whether the agent
finishes the whole sequence is covered by the scripted agent-loop tests, not by this eval.
