# Keel: architecture

Keel is a single-user agent that remembers commitments, plans realistic days, tracks whether work actually gets
done, and follows up. The design rule: **the LLM decides and talks; code stores, computes, enforces and schedules.**
Anything that must be reliable (deadlines, counts, permissions, reminders, budgets, security checks) lives in the
database and in code, not in the prompt.

---

## 1. System overview

```
                 ┌──────────────────────── Clients ────────────────────────┐
                 │  Dashboard (React)  ·  CLI  ·  Telegram (inbound + push)  │
                 └──────────────┬───────────────────────────────────────────┘
                                │ HTTP/JSON
┌───────────────────────────────▼──────────────────────────────────────────────────────────┐
│ FastAPI (app/api): validation, auth (X-API-Key), rate limit, security headers. No logic.   │
└───────┬─────────────────────────────────────────────────────────────┬────────────────────┘
        │ chat                                                         │ CRUD · approve · stats
┌───────▼───────────────┐   tool calls    ┌────────────────────────────▼────────────────────┐
│ Agent (app/agent)     │────────────────▶│ ToolExecutor (app/tools/executor.py)             │
│  system prompt        │◀────────────────│  validate → permission policy → taint gate →     │
│  + context pack       │   tool results  │  run → audit.  EVERY call passes through here.   │
│  bounded tool loop    │                 └───────┬──────────────────────────────────────────┘
└───────┬───────────────┘                         │
        │                          ┌──────────────▼──────────────┐   ┌───────────────────────┐
┌───────▼───────────────┐          │ Tools (app/tools) +          │   │ Security (app/security)│
│ LLM client (app/llm)  │          │ services (app/services)      │   │ permissions · audit ·  │
│ Anthropic / Groq      │          │ tasks goals projects         │   │ untrusted fencing ·    │
└───────────────────────┘          │ commitments memory planning  │   │ sanitising             │
                                   │ reminders calendar web notes │   └───────────────────────┘
                                   │ github                       │
                                   └──────────────┬───────────────┘
                                                  │
                              ┌───────────────────▼────────────────────┐
                              │ SQLite (SQLAlchemy 2, Alembic) + FTS5   │
                              └───────────────────▲────────────────────┘
                                                  │ same services, same tools
┌─────────────────────────────────────────────────┴──────────────────────────────────────┐
│ Scheduler (APScheduler, app/scheduler): tick() → reminders · check-ins · detectors       │
│   → NotificationService (dedupe, priority, quiet hours, daily budget) → Telegram / log   │
│   → LLM is asked only to *word* the message (no tools; deterministic fallback text)      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### Key decisions

| Decision | Why |
|---|---|
| One agent with native tool calling, not multi-agent | Simplest thing that works; multi-agent adds failure modes without benefit at this scale. |
| Structured data in relational tables; embeddings avoided | "What did I promise this week?" is a SQL query. Free-text search uses SQLite FTS5 (BM25), not a vector DB. |
| Permissions, confirmation and taint enforced in `ToolExecutor`, not the prompt | A prompt can be talked out of a rule; code cannot. |
| Proactivity = deterministic detectors + LLM for wording | SQL decides *whether* something is overdue or neglected; the model only decides *how to say it*. Cheaper, testable, no hallucinated nags. |
| Messages stored in provider block format | Replay tool exchanges exactly; a new provider needs one adapter (`app/llm`). |
| Same `Container` for API, CLI and scheduler | One code path, identical behaviour, one place to test. |
| `tick()` is idempotent and catches up | Scheduling by *state* (due items, windows, dedupe keys), not exact timers: a sleeping laptop or a double run can't lose or duplicate a message. |
| Single process | The scheduler and rate limiter are in-process. Scale-out would need a worker/queue (see §10). |

---

## 2. Data model

Hand-written Alembic migrations `0001`–`0006`; SQLite today, SQLAlchemy keeps PostgreSQL a URL change (notes search needs FTS5, so it is SQLite-only).

| Table | Purpose |
|---|---|
| `conversations`, `messages` | Chat history in LLM block format; `conversations.summary` holds the rolling summary of aged-out turns |
| `tasks` | title, status, priority, due date, estimate, `postponement_count` (a cache of the event log), links to goal/milestone/project |
| `task_events` | **Append-only history**: created / postponed (old→new date + reason) / completed / reopened / cancelled / skipped. Survives task deletion. |
| `goals`, `milestones`, `projects` | The goal → milestone → task and project → task hierarchies |
| `commitments` | "I'll do X by Y": `proposed` until you confirm → `open` → `done` / `dropped`; "missed" is derived, not stored |
| `memories` | Durable facts about you: kind (preference / context / fact / decision), category (personal / academic / professional / project / general), an optional profile topic, source and review dates |
| `plans`, `plan_items` | One plan per local day; items snapshot the title so history stays readable |
| `work_sessions` | Time actually worked: the ground truth plans are compared against |
| `reminders`, `checkins`, `notifications` | Scheduler state. `notifications` *is* the budget: counts and dedupe keys are read from it, so limits survive restarts |
| `documents`, `chunks`, `chunk_fts` | Notes index (FTS5 virtual table, rowid = chunk id) |
| `pending_actions`, `audit_log` | The approval queue and the record of every tool call |

**Why `task_events` matters:** `postponement_count` is a cache. The event history (when, how far, *why*) is what lets the
accountability layer say "you've postponed GRE four times, citing 'too big' twice" without inventing anything.

---

## 3. The agent turn (`app/agent/loop.py`)

```
user message ─persist─▶ build context ─▶ system prompt (persona + rules + date) + summary + CONTEXT PACK
        ┌───────────────────────────────────────────────────────────────────────────┐
        ▼                                                                           │
   LLM call (tool specs) ── text only ─────────▶ persist, reply                     │
        └── tool_use ▶ ToolExecutor.run(...) for each call ─▶ persist tool_use + tool_result together
                                                                                    │
                       repeat, max_agent_steps ────────────────────────────────────┘ else safe fallback reply
```

- **Context pack** (`app/agent/context.py`): before every call, *code* assembles a compact, size-capped snapshot: today's plan,
  open tasks, overdue/at-risk commitments, items postponed ≥2×, your preferences, memories matching the message's keywords,
  and the last proactive message the agent sent you (so "didn't get to it" has context). No LLM involved.
- **Atomic history:** a `tool_use` is never saved without its `tool_result`; the replay window never starts mid-exchange.
- **Honesty rule:** the prompt forbids claiming an action without a successful tool result; results carry `is_error`.
- **Summarisation:** messages aging out of the window are folded into `conversations.summary` (one extra LLM call, incremental).

## 4. Tools and permissions

```python
Tool(name, description, permission, input_model, handler(ctx, params) -> dict,
     summarize=..., returns_untrusted=False, taint_gate=None)
```

The JSON schema the model sees is generated from the pydantic input model that also validates the call. All 43 tools (42 domain tools plus `load_tools`) are listed in
[TOOLS.md](TOOLS.md) (generated from the registry; a test fails if it goes stale).

| Tier | Behaviour |
|---|---|
| READ | Runs immediately |
| WRITE | Runs immediately and is audited. **Needs approval when the session is tainted**, or always with `CONFIRM_WRITES=true` |
| HIGH_IMPACT | Never runs from the model; queued as a `pending_action` that only you can approve (API/CLI/UI), executed once, in one transaction |

Tools that need configuration (calendar, web, notes, GitHub) are only advertised to the model when configured.

**Lean tool profile** (`app/tools/groups.py`, default). Tool definitions are ~7,100 tokens, sent with every LLM request. Tools are therefore split into 13 groups; a request carries the always-on `tasks` group plus groups chosen by keyword rules on the user's message, groups the model called recently (so "yes" keeps working), and any it requests with the `load_tools` meta-tool (the system prompt lists the unloaded groups). Result: ~2.6-3.3k tokens of overhead instead of ~8.4k. This is a *size* optimisation, not a security boundary: the executor enforces permissions, confirmation and taint on every call, advertised or not (tested).

## 5. Memory

| Layer | What | Storage | Retrieval |
|---|---|---|---|
| Working | The conversation | `messages`, windowed | Replayed each turn (+ rolling summary) |
| Structured | Goals, projects, tasks, commitments, plans | Relational tables | Tools + SQL |
| Semantic | Profile, preferences, context, decisions | `memories` | The context pack always shows the profile and preferences, plus the best-matching memories for the message (word overlap, then importance, then how recently confirmed); `search_memories` and `review_memories` for depth |
| Behavioural | Postponement patterns, completion rate, neglected goals, time worked | **Computed** from `task_events`, `work_sessions`, `plan_items` | SQL aggregates, never LLM-written |

Write policy: commitments are proposed, then confirmed; memories are saved only when useful, are visible in the dashboard, and every
change is audited. A profile topic holds one answer: saving a different value for an existing topic saves nothing and tells the
agent to ask the user which is right, so contradicting facts are never kept side by side. Each memory records its source (saved in
chat, approved by the user, added by the user) and when it was last confirmed; memories unconfirmed for six months are offered for
review in the dashboard and through `review_memories`. Retrieval is by words first; when a search shares no word with
any memory, the most important memories come back as candidates and the model judges them by meaning (no embeddings, no
extra call). Saving a memory also shows the model the others in its category, so a contradiction worded differently is
noticed and put to the user. Embeddings were deliberately skipped: keyword + FTS5 covers this scale and avoids a second datastore.

## 6. Planning and accountability (`app/services/planner.py`, `accountability.py`)

The planner is deterministic and unit-tested for its guarantees: usable time is capacity minus a 15% buffer; at most 3 *major*
priorities (high priority or ≥90 min); a task longer than the whole day is deferred with "split it"; every omission has a stated
reason; re-planning keeps finished items. Ranking: urgency (overdue > today > ≤3d > ≤7d) + priority + a nudge for tasks that keep slipping.

`accountability_report` aggregates plan adherence, most-postponed tasks (with the reasons given), old open tasks never worked on,
overdue commitments, neglected goals (no completion, creation or work in N days) and minutes per project. `analytics` adds weekly series,
streaks, promises kept, estimate accuracy and postponement reasons for the Insights tab.

## 7. Proactivity (`app/scheduler`)

`Scheduler.tick()` runs every minute and is safe to run twice:

1. **Reminders** due now are sent (exempt from budget/quiet hours; retried with backoff; dropped if >24h late).
2. **Check-ins** (morning, evening, weekly-replaces-evening) are sent if their time has passed, they're inside the grace window and none
   was sent today. Facts come from SQL; the LLM, with **no tools**, only words the message; if it fails, a deterministic text is sent.
3. **Detectors** (overdue commitments, overdue tasks, tasks slipping ≥3×, stalled plan after 14:00, neglected goals) produce nudges with
   stable dedupe keys. At most one nudge per tick.

`NotificationService` decides whether anything is sent: duplicate → priority threshold → quiet hours (high priority may interrupt) →
daily budget. Nudges draw from their own pool so they can never crowd out the check-ins. Delivery is a `Notifier` (Telegram or log).

## 8. Research tools and the injection defences

`web_fetch`, `web_search`, `github_*`, `notes_search`, `read_note` and `read_calendar` all return text written by third parties, so
they are `returns_untrusted`: output is sanitised, fenced in `<untrusted_content>`, and taints the conversation (WRITE tools then need approval until a new chat).
`web_fetch` additionally has an SSRF-hardened fetcher and an exfiltration `taint_gate`. The full threat model, the 14 enforced controls, how
they are tested (the red-team suite, mutation-checked) and the known limitations are in **[SECURITY.md](../SECURITY.md)**.

## 9. Quality gates

| Gate | What it covers |
|---|---|
| `pytest` + `npm test` (16 dashboard tests) + `tests/e2e` (real Chromium) | Tools, executor, agent loop, planner, scheduler, API, notes search, SSRF, GitHub/Brave/Telegram clients (all HTTP mocked, LLM scripted) |
| `tests/injection` | The red-team suite, a compromised model against 7 channels × 7 payloads |
| `evals/` | Tool-routing eval (160 cases, 55 with argument checks; reports missed, wrong and unsafe calls), scored per category and tag, history tracked; the dataset itself is validated offline |
| CI | ruff, Python 3.11/3.12, dashboard type-check + build, real-Chromium e2e, dependency audit, gitleaks secret scan, Docker build + `/health` |

## 10. Folder structure

```
app/{agent,api,db,integrations,llm,scheduler,security,services,tools}/   backend
frontend/         React + Vite + TypeScript dashboard (served by FastAPI from frontend/dist)
migrations/       hand-written Alembic migrations 0001-0006
tests/            unit/API tests; tests/injection = red team
evals/            tool-routing eval        scripts/   doc generators
docs/             ARCHITECTURE.md · TOOLS.md (generated) · DEMO.md
```

## 11. Known limitations and ideas

- **Single user, single process.** One API key, in-process scheduler and rate limiter. Multi-user would need accounts, per-user data
  isolation, and the scheduler moved to a worker.
- **Coarse, sticky taint**: it lasts for the rest of the conversation (see SECURITY.md). A finer-grained scheme (track which values came from where) would reduce approval prompts.
- **Keyword memory relevance.** Fine at this scale; embeddings are the upgrade if memories grow into the thousands.
- **Notes search is SQLite-only** (FTS5). A Postgres deployment would need `tsvector` instead.
- **Calendar is read-only and doesn't yet reduce planning capacity automatically**; `read_calendar` returns `busy_minutes` and the agent is
  told to subtract them.
- **Browser coverage is narrow**: two Chromium end-to-end tests (main flow, crash recovery) plus jsdom render tests; no Firefox/Safari runs.
- **The fetcher is not a firewall.** `web_fetch` pins DNS (the address that was checked is the address connected to), which closes
  DNS rebinding, but on a server you should still block private ranges with an egress firewall or network policy.
- **Following a link inside a page needs approval** once a conversation is tainted (only typed URLs and search results open freely).
  This closes the link-choice leak at the cost of an extra click when reading onward from a page (see SECURITY.md).
