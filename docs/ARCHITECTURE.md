# Personal AI Agent — Architecture & Roadmap

A single-user agent that remembers commitments, plans realistic days, tracks whether work actually gets done, and follows up. The design rule: **the LLM decides and talks; code stores, computes, enforces and schedules.** Anything that must be reliable (deadlines, counts, permissions, reminders) lives in the database and in code, not in the prompt.

---

## 1. System architecture

```
                      ┌────────────────────────── Clients ───────────────────────────┐
                      │  Dashboard (React)   ·   CLI   ·   Telegram bot (Phase 4)     │
                      └───────────────┬──────────────────────────────────────────────┘
                                      │ HTTP/JSON
┌─────────────────────────────────────▼─────────────────────────────────────────────────┐
│ FastAPI  (app/api)  — thin: validation, HTTP mapping, no business logic                │
└───────┬───────────────────────────────────────────────────────────┬───────────────────┘
        │ chat                                                      │ CRUD / confirm / stats
┌───────▼──────────────┐    tool calls     ┌───────────────────────▼──────────────────┐
│ Agent  (app/agent)   │──────────────────▶│ ToolExecutor (app/tools/executor.py)      │
│  prompt + context    │◀──────────────────│  validate → permission policy → run →     │
│  bounded tool loop   │    tool results   │  audit.  Every call passes through here.  │
└───────┬──────────────┘                   └───────┬───────────────────────────────────┘
        │                                          │ uses
┌───────▼──────────────┐   ┌───────────────────────▼──────────┐   ┌────────────────────┐
│ LLM client           │   │ Domain services + Tools          │   │ Security            │
│ (app/llm) swappable  │   │ tasks · goals · projects ·       │   │ permissions, audit, │
│ Anthropic / other    │   │ commitments · memory · planner · │   │ untrusted-content   │
└──────────────────────┘   │ calendar · research · files · git│   │ fencing + taint     │
                           └───────────────┬──────────────────┘   └────────────────────┘
                                           │
                        ┌──────────────────▼───────────────────┐
                        │ SQLite → PostgreSQL (SQLAlchemy 2.0) │
                        └──────────────────▲───────────────────┘
                                           │ same services, same tools
                        ┌──────────────────┴───────────────────┐
                        │ Scheduler (APScheduler, Phase 4)      │
                        │ detectors (SQL) → notify → agent run  │
                        └──────────────────────────────────────┘
```

**Key decisions**

| Decision | Why |
|---|---|
| One agent with native tool calling, not multi-agent | Simplest thing that works; multi-agent adds failure modes without benefit at this scale. |
| Structured data in relational tables; embeddings only for free text | "What did I promise this week?" is a SQL query, not a similarity search. |
| Permissions and confirmation enforced in `ToolExecutor`, not in the prompt | A prompt can be talked out of a rule; code cannot. |
| Proactivity = deterministic detectors + LLM for wording | SQL decides *whether* something is overdue or neglected; the model only decides *how to say it*. Cheaper, testable, no hallucinated nags. |
| Messages stored in provider block format | Lets the agent replay tool exchanges exactly. A new provider needs one adapter (`app/llm`). |
| Same `Container` for API, CLI and scheduler | One code path → identical behaviour and one place to test. |

---

## 2. Database schema

`✔` = implemented in Phase 1. Everything else is added by Alembic migration in the phase shown.

| Table | Key columns | Phase |
|---|---|---|
| `conversations` ✔ | id, created_at | 1 |
| `messages` ✔ | id, conversation_id, role, content (JSON blocks), created_at | 1 |
| `tasks` ✔ | id, title, description, status (todo/in_progress/done/cancelled), priority, due_date, estimated_minutes, **postponement_count**, created_at, updated_at, completed_at | 1 |
| `pending_actions` ✔ | id, conversation_id, tool_name, tool_input (JSON), summary, status (pending/confirmed/rejected/failed), created_at, resolved_at | 1 |
| `audit_log` ✔ | id, created_at, actor (agent/user), tool_name, permission, params, status, detail, conversation_id, pending_action_id | 1 |
| `goals` | id, title, description, horizon (short/long), category (career/education/personal), status, target_date, parent_goal_id, last_activity_at | 2 |
| `milestones` | id, goal_id, title, order, status | 2 |
| `projects` | id, name, description, purpose, status, priority, deadline, goal_id, progress (derived) | 2 |
| `tasks` (+cols) | + project_id, milestone_id, goal_id | 2 |
| `commitments` | id, text, deadline, importance, project_id, status (open/done/missed/dropped), created_at, completed_at, postponement_count, source_message_id, confirmed (bool) | 2 |
| `memories` | id, kind (preference/context/fact), content, importance, source_message_id, created_at, last_used_at, is_active | 2 |
| `task_events` | id, task_id, type (created/postponed/completed/reopened), old_value, new_value, reason, at | 3 |
| `plans` / `plan_items` | plan(date, capacity_minutes) · item(plan_id, task_id, planned_minutes, status, order) | 3 |
| `work_sessions` | id, task_id, project_id, started_at, ended_at, minutes, note | 3 |
| `checkins` | id, kind (morning/evening/weekly), date, summary, user_response | 4 |
| `reminders` | id, text, fire_at, status, linked_type, linked_id | 4 |
| `notifications` | id, kind, priority, sent_at, channel, dedupe_key | 4 |
| `documents` / `chunks` | file path, hash, text chunks (+ embedding column when needed) | 5 |

**Why `task_events` matters:** `postponement_count` is a cache. The event history (when, why, how far) is what lets the accountability layer say *"you've postponed GRE four times this week"* accurately and analyse patterns in Phase 6.

---

## 3. Agent architecture

One turn (`app/agent/loop.py`):

```
user message
   │ persist
   ▼
build context ──▶ system prompt (persona + rules + current time)
                  + [Phase 2+] context pack (see §5)
   ▼
┌─▶ LLM call (with tool specs)
│      ├─ text only ───────────────▶ persist, reply   (done)
│      └─ tool_use ▶ ToolExecutor.run(...) for each call
│                    persist assistant tool_use + tool_result together
└──────────────── repeat, max_agent_steps (default 8) ──▶ safe fallback reply
```

Properties already implemented and tested:
- **Bounded loop** — cannot run away; ends with a well-formed history.
- **Atomic history** — a `tool_use` is never saved without its `tool_result`; truncated `tool_use` blocks are dropped; the history window never starts mid-exchange.
- **Honesty rule** — the prompt forbids claiming an action without a successful tool result, and tool results carry `is_error`.
- **Persona** lives in `app/agent/prompts.py` (supportive-but-direct, no generic hype, at most 3 priorities, ask *why* when a task keeps slipping).

**Decision-making (spec §10):** tool routing is done by the model from tool descriptions. Descriptions are written as "use when…" so `list_tasks` answers "what should I work on", `search_commitments` (Phase 2) answers "what did I promise", `web_search` (Phase 5) answers research questions. In Phase 6 we add a routing eval set (≈40 prompts → expected tool) to measure this instead of guessing.

**Proactive runs (Phase 4):** the scheduler triggers the *same* agent with a system-initiated message (e.g. `morning_checkin`) plus pre-computed facts. Detectors and a **notification budget** (max N/day, quiet hours, priority threshold, dedupe keys) decide whether anything is sent at all.

---

## 4. Tool architecture

```python
Tool(name, description, permission, input_model (pydantic), handler(ctx, params) -> dict,
     summarize=..., returns_untrusted=False)
```
- **Input schema is generated from the pydantic model** → the model sees a JSON schema, and the same model validates the call. No drift between the two.
- **Handlers are plain functions** over a `ToolContext` (db session, timezone, conversation id, taint flag). They call domain logic; they do not talk to the LLM.
- **Adding a tool = one function + one `register` call.** Nothing else changes.

**Permission tiers (enforced in code by `PermissionPolicy`)**

| Tier | Examples | Behaviour |
|---|---|---|
| READ | list_tasks, read_calendar, read_file, github_read | Runs immediately |
| WRITE | create/update/complete task, save_memory, create_event | Runs immediately; audited. Setting `CONFIRM_WRITES=true` makes it ask first |
| HIGH_IMPACT | delete_*, send_message, publish, external changes | **Never runs from the model.** Queued as `pending_action`; only the user (API/CLI/UI) can confirm it |

Confirmation is enforced by the executor, not requested in the prompt: the model *cannot* execute a high-impact action even if it wants to. Confirmed actions are marked and executed in one transaction, so they cannot run twice.

**Tool roadmap:** tasks ✔ → goals/projects/commitments/memory (2) → planner, time tracking (3) → reminders, calendar (4) → web_search, file_search, github (5).

---

## 5. Memory architecture

Four layers, each with the right storage:

| Layer | What | Storage | Retrieval |
|---|---|---|---|
| **Working** | The current conversation | `messages`, windowed (last 40) | Replayed each turn |
| **Structured** | Goals, projects, tasks, commitments — the source of truth | Relational tables | Tools + SQL |
| **Semantic** | Preferences and personal context ("I focus best in the morning", "FYP supervisor is …") | `memories` table | SQLite FTS5 first; embeddings only if FTS proves insufficient |
| **Behavioural** | Postponement patterns, completion rate, neglected goals | *Computed* from `task_events`, `work_sessions`, `plan_items` | SQL aggregates — never LLM-written |

**Context pack (Phase 2 ✔ — `app/agent/context.py`; memory relevance is keyword overlap for now, FTS5 is a later upgrade):** before each LLM call, code assembles a compact block — today's plan, top open tasks, overdue/at-risk commitments, most-postponed items, preferences, and the top-k memories relevant to the message. The model gets the essentials without having to remember to look them up, and can still call tools for depth.

**Write policy**
- *Commitments:* the model flags candidates ("I'll finish this by Friday"); if deadline or scope is ambiguous it asks once, then stores with `confirmed=true`.
- *Preferences/context:* saved only when useful for future help (the tool description and prompt say so); each save is shown to the user in the reply.
- Every memory has a source message, can be viewed/edited/deleted in the dashboard, and each change is audited.
- A weekly job merges near-duplicates and archives stale items (`is_active=false`, not deleted).

---

## 6. MVP feature list (spec §17)

1. Chat (API + CLI, later dashboard) — **Phase 1 ✔**
2. Persistent memory
3. Goals (+ decomposition into milestones/tasks)
4. Projects
5. Tasks — **create/list/update/complete/delete ✔**
6. Commitments (detection, confirmation, postponement tracking)
7. Daily planning (realistic, ≤3 major priorities, capacity-aware)
8. Accountability check-ins (morning/evening, pattern diagnosis)
9. Basic reminders
10. Simple dashboard (Today, Goals, Projects, Commitments, Memory, Chat)

Also already in place from Phase 1: permission tiers, human confirmation, audit log, untrusted-content fencing.

**Explicitly deferred:** calendar sync, web research, GitHub, file search, analytics, multi-user, mobile app.

---

## 7. 8-week roadmap

| Week | Milestone | Done when |
|---|---|---|
| **1** | **Phase 1** — chat, agent loop, tools, DB, permissions, audit | Can create/list/complete tasks by chatting; delete needs approval; tests green *(this repo)* |
| **2** | **Phase 2a** — Alembic, goals, milestones, projects, `memories` + memory CRUD tools, context pack | Agent remembers a preference across restarts; user can list/edit/delete memories |
| **3** | **Phase 2b/3a** — commitments (detection + confirm), `task_events`, goal decomposition ("become good at pentesting" → milestones → tasks) | "I'll do GRE tonight" becomes a tracked commitment; decomposition produces reviewable tasks |
| **4** | **Phase 3b** — daily planner (capacity + priority cap), work sessions, accountability queries ("what have I been avoiding?") | Plan never exceeds available time; postponement diagnosis works from real data |
| **5** | **Dashboard MVP** (React + Vite, consumes the existing API): Today, Goals, Projects, Commitments, Memory, Chat | All spec §13 views except Analytics |
| **6** | **Phase 4** — APScheduler, morning/evening check-ins, reminders, Telegram delivery, notification budget; Google Calendar (read) | Gets a useful morning message and evening review without opening the app; ≤N notifications/day |
| **7** | **Phase 5** — web research tool (fenced as untrusted), file search (FTS5), GitHub read-only | Answers "what should I read on explainable IDS" with sources; searches own notes |
| **8** | **Phase 6** — prompt-injection test suite, tool-routing eval, analytics view, auth on the API, Docker, README + demo for the portfolio | Injection suite passes; eval score tracked; one-command deploy |

The week-6 schedule is the riskiest part (delivery channel, timezones, duplicates), so it has its own week. If time gets tight, cut Calendar, not the accountability features.

---

## 8. Recommended stack

| Concern | Choice | Notes |
|---|---|---|
| Language / API | Python 3.11+, FastAPI | Async-capable, auto docs at `/docs` |
| DB | SQLite → PostgreSQL | SQLAlchemy 2.0 keeps the switch to a URL change; Alembic from Phase 2 |
| Validation | Pydantic v2 | Also generates the tool JSON schemas |
| LLM | Anthropic API behind `LLMClient` | Model is one env var (`AGENT_MODEL`) |
| Search | SQLite FTS5 → embeddings only if needed | Avoids a vector DB entirely at first |
| Scheduler | APScheduler (in-process) | Move to a worker/queue only if you outgrow it |
| Notifications | Telegram bot | Free, instant on phone, simple API |
| Frontend | React + Vite (week 5) | Next.js SSR isn't needed; the API is already JSON |
| Tests | pytest + a scripted fake LLM | Agent logic is tested without network or cost |
| Deploy | Docker, single container + volume | Bind to localhost / add API key before exposing |

---

## 9. Folder structure

```
personal-agent/
├── app/
│   ├── config.py            # settings from env/.env
│   ├── container.py         # wires everything; used by API, CLI, scheduler
│   ├── errors.py
│   ├── cli.py               # terminal chat + approve/reject
│   ├── api/                 # main.py (app factory) · routes.py · schemas.py
│   ├── agent/               # loop.py (turn loop) · prompts.py
│   ├── llm/                 # base.py (interface) · anthropic_client.py · factory.py
│   ├── tools/               # base.py · executor.py · tasks.py · system.py   (+ one module per domain)
│   ├── security/            # permissions.py · audit.py · untrusted.py
│   └── db/                  # models.py · session.py                         (+ migrations/ from Phase 2)
├── tests/                   # fakes.py (scripted LLM) · test_tools · test_agent · test_api
├── docs/ARCHITECTURE.md
├── requirements.txt · requirements-dev.txt · pytest.ini · .env.example
```
Later phases add: `app/services/` (domain logic shared by tools and API), `app/memory/`, `app/planning/`, `app/scheduler/`, `app/integrations/` (calendar, github, telegram), `frontend/`, `evals/`.

---

## 10. Prompt-injection defence (built from day one)

1. **Fence:** tools marked `returns_untrusted=True` have their output wrapped in `<untrusted_content>` tags; the closing tag inside content is neutralised so it can't break out.
2. **Taint:** once untrusted content has entered the context (this turn *or* still in the history window), WRITE tools require user confirmation. READ tools still work. A poisoned web page therefore cannot silently create, change or send anything.
3. **Tier enforcement in code:** HIGH_IMPACT is never model-executable.
4. **Prompt rule:** external text is data, never instructions — a backup, not the primary defence.
5. **Audit:** every call is logged with actor, params, outcome.

Phase 6 adds a red-team suite (poisoned pages, PDFs, GitHub READMEs, emails) that runs in CI, plus egress limits on the research tool.

## 11. Known limitations of Phase 1 (honest list)

- ~~No memory beyond the conversation window yet.~~ Fixed in Phase 2: `memories` table, memory tools, and the per-turn context pack (`app/agent/context.py`).
- The Anthropic client is not covered by automated tests (it is a thin adapter; agent logic is tested with a scripted LLM). Run one real chat to verify your key/model.
- ~~No authentication: bind to localhost only until Phase 6.~~ Fixed: optional `X-API-Key` auth via `API_KEY` (see README § Security notes). Still no per-user auth or rate limiting — fine for a single-user local/self-hosted deployment, not for multi-tenant use.
- ~~Pending actions never expire.~~ Fixed: `PENDING_ACTION_TTL_HOURS` (default 24) auto-expires unanswered high-impact actions.
- ~~Long chats just truncate at `history_message_limit` instead of degrading gracefully.~~ Fixed: messages that age out of the window are folded into `conversations.summary` (`SUMMARIZE_DROPPED_HISTORY`).
- The tainted-session rule is coarse (any untrusted content in the history window). Correct but sometimes over-cautious.
- ~~`Base.metadata.create_all` is used for schema creation.~~ Fixed in Phase 2: schema is managed by Alembic migrations.
