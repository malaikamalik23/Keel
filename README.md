# Keel

**A self-hosted personal AI agent that remembers your commitments, plans realistic days, tracks what actually gets done, and follows up.**

Most assistants forget what you said yesterday and happily agree that you'll "start tomorrow". Keel keeps the books:
what you promised, what you planned, what slipped (and the reason you gave), and what you actually worked on. It uses
that record to plan days that fit, to ask *why* something keeps slipping, and to check in with you. It runs on your
machine, with your data in one SQLite file.

> **The LLM decides and talks. Code stores, computes and enforces.** Deadlines, counts, plans, permissions, notification
> limits and security checks are code with tests, not promises in a prompt.

## What it does

| | |
|---|---|
| **Chat** | Talk to it in the terminal, the web dashboard, or Telegram. It calls tools to read and change your data. |
| **Goals → milestones → tasks** | "I want to get good at web pentesting" becomes a goal, ordered milestones, and concrete first tasks. |
| **Commitments** | Notices "I'll finish the GRE set by Friday", asks you to confirm, tracks it, counts postponements. |
| **Realistic daily plans** | A deterministic planner: never exceeds your available time (15% buffer), at most 3 major priorities, and tells you what it left out and why. |
| **Accountability from facts** | "What have I been avoiding?" is answered from the event history: postponements with your reasons, old tasks never touched, neglected goals, time per project. No invented patterns. |
| **Memory you control** | Preferences and context it saves are visible, editable and deletable. |
| **Proactive, within a budget** | Morning/evening/weekly check-ins, reminders, and nudges for overdue or repeatedly-slipping items, limited by a daily budget, quiet hours and de-duplication. |
| **Research** | Web search and page reading, search over your own notes and papers (including Urdu), read-only GitHub and calendar access. |
| **Insights** | Weekly trends, streaks, promises kept, how long things really take versus your estimates. |

## Security is a feature

An agent that reads the web and can edit your data is a prompt-injection target. Keel assumes the model **cannot be
trusted once it has read external text** and enforces the rest in code:

- Deletes are **queued for your approval**; the model has no tool to approve anything.
- Reading external content **taints the session**: further writes also need approval.
- External text is **fenced and stripped of invisible characters and hidden HTML**.
- A URL the model *assembled itself* (how data is smuggled out) is **gated**; the fetcher refuses private networks, validates every redirect.
- Every action is **audited**.

This is verified by a red-team suite that drives a *fully compromised* model through 7 external channels × 7 attack
payloads and asserts nothing changes. It was mutation-checked: breaking any single defence makes it fail.
Details and honest limitations: **[SECURITY.md](SECURITY.md)**.

## Quick start

You need Python 3.11+, Node 20+ (only to build the dashboard), and a key for [Anthropic](https://console.anthropic.com/) **or** [Groq](https://console.groq.com/keys).

```bash
git clone https://github.com/malaikamalik23/Keel.git && cd Keel
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                      # Windows: copy .env.example .env
#   then edit .env: set ANTHROPIC_API_KEY (or GROQ_API_KEY) and AGENT_TIMEZONE

pytest -q                                                 # ~370 tests, no key or network needed

(cd frontend && npm ci && npm run build)                  # build the dashboard once
uvicorn app.api.main:create_app --factory                 # dashboard + API at http://127.0.0.1:8000
# or, terminal only:
python -m app.cli
```

The schema is created and migrated automatically on startup (Alembic, `data/agent.db`).

### Docker

```bash
cp .env.example .env     # set an LLM key and API_KEY (compose refuses to start without API_KEY)
docker compose up --build        # http://127.0.0.1:8000
```

The container runs as a non-root user with a read-only filesystem and no capabilities, and is published on localhost only.
Put a TLS reverse proxy in front if you want to reach it remotely.

> The Dockerfile and compose file are validated and the runtime stage was exercised in a clean virtualenv, but the image
> itself has not been built in this repository's own CI yet; the first CI run will tell.

### Try it

```
you> I want to get good at web penetration testing
you> Plan my day, I have 4 hours
you> I'll finish the GRE quant set by Friday
you> Move the GRE task to Monday, it's too big          (it should ask why, and the reason is recorded)
you> What have I been avoiding?
you> delete the literature review task                   (queued: approve with /confirm 1 or in the dashboard)
you> Remind me tomorrow at 9 to email my supervisor
```

More in **[docs/DEMO.md](docs/DEMO.md)**.

## Configuration

Everything is an environment variable (or a line in `.env`); every option is documented in [`.env.example`](.env.example).
The ones you'll touch first:

| Setting | Why |
|---|---|
| `ANTHROPIC_API_KEY` / `GROQ_API_KEY`, `AGENT_MODEL` | The LLM. Groq models must support tool calling (`openai/gpt-oss-120b` works on Groq's free tier; set `HISTORY_MESSAGE_LIMIT=12` there). |
| `AGENT_TIMEZONE` | "Today", check-in times and quiet hours use it. |
| `API_KEY` | **Set this before exposing the API anywhere.** |
| `SCHEDULER_ENABLED=true` | Turns on check-ins, reminders and nudges (run one process only). |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Deliver messages to, and chat from, your phone. |
| `NOTES_DIR` | A folder of notes/papers for `notes_search`. |
| `BRAVE_API_KEY` | Enables `web_search`. |
| `WEB_ALLOWED_DOMAINS` | Restrict the web fetcher to a short allow-list. |
| `CALENDAR_ICS_URL`, `GITHUB_USER`/`GITHUB_TOKEN` | Calendar and GitHub (both read-only). |

## How it works

```
Dashboard (React) · CLI · Telegram ──▶ FastAPI ──▶ Agent loop ──▶ ToolExecutor ──▶ tools ──▶ SQLite
                                                       │             │  validate → permission policy
                                                  LLM (Anthropic /   │  → taint gate → run → audit
                                                  Groq, swappable)   └─ queues HIGH_IMPACT for you
Scheduler ──▶ detectors (SQL) ──▶ notification budget ──▶ LLM only words the message ──▶ Telegram/log
```

- **One agent, native tool calling**, bounded loop (`MAX_AGENT_STEPS`), atomic history (a `tool_use` is never saved without its result).
- **43 tools** in three permission tiers (one is the small `load_tools` helper); see the generated **[docs/TOOLS.md](docs/TOOLS.md)**.
- **Lean tool profile (default).** A request that carries all 42 tool definitions is ~7,000 tokens before the conversation starts. Keel sends the core task tools plus only the groups the message needs (keywords, groups used recently, or a `load_tools` call), about 2.6-3.2k tokens. That is cheaper everywhere and is what makes it usable on Groq's free tier (8,000 tokens/minute). `TOOL_PROFILE=full` restores everything.
- **Proactivity = detectors + budget.** SQL decides *whether* something is overdue or neglected; the model only decides *how to say it*, and a deterministic fallback text is sent if the LLM is down.
- Full design: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## Testing

```bash
pytest -q                          # everything
pytest tests/injection -q          # the prompt-injection red-team suite
ruff check .                       # lint
python -m evals.run_routing        # how often does the model pick the right tool? (needs an API key)
```

- Agent logic is tested against a scripted fake LLM, and all HTTP (Telegram, Brave, GitHub, web pages, calendar) against mocked transports, so CI needs no keys and no network.
- `evals/` holds 57 routing cases scored per category, with history tracked in `evals/history.jsonl`. [More](evals/README.md).
- CI (GitHub Actions): lint, tests on Python 3.11 and 3.12, red-team suite, dashboard tests + build, real-Chromium end-to-end tests, dependency audit, secret scan, Docker build.

## Honest status

- **Real-model routing, measured: 52/57 (91%) on Groq `openai/gpt-oss-120b`** (lean profile, all 57 eval cases, Nov 2026). Two of the
  misses were then fixed: reminders (the tool schema demanded a stricter time format than Groq would accept) and "plan my day"
  (the model listed tasks first). Still weak: commitments phrased like tasks ("move my poster deadline") and, in some runs, "what's my
  plan" answered without calling the tool. Scores move by a case or two between runs. Re-measure with
  `python -m evals.run_routing --sleep 20` (the pause keeps Groq's free tier under its tokens-per-minute limit).

- **Live services: some verified, some only mocked.** GitHub (including your private repos when a token is set), the iCal calendar and
  the notes folder have been checked against the real services with `python -m scripts.smoke_integrations` (one read-only call each,
  never prints keys). Telegram and Brave Search are so far verified only against mocked HTTP; try them with your own keys
  (see [docs/DEMO.md](docs/DEMO.md)) and please open an issue if a real API behaves differently.
- The dashboard is type-checked, has 16 jsdom tests (every tab, empty states, a malformed response, the approval flow, skip-cancel) and 2 **real-Chromium end-to-end tests** (`tests/e2e`, Playwright; CI job `e2e`) that drive the real server through chat, plan, delete-and-approve and reminders and fail on any console error. The browser tests exist because the first manual run found a Chrome-only crash that jsdom could not see.
- Single-user by design: one API key, no accounts. Not multi-tenant.
- See [SECURITY.md](SECURITY.md#known-limitations-read-these) for the security limitations (sticky taint, DNS rebinding, and more).

## Project layout

```
app/
  agent/        the turn loop, system prompt, context pack, summariser
  tools/        one module per domain; every tool = pydantic model + handler + permission
  security/     permission policy, audit log, untrusted-content fencing and sanitising
  services/     planner, accountability, analytics, notifications, notes indexer (shared by tools and API)
  scheduler/    tick loop, detectors, check-ins
  integrations/ Telegram, calendar (iCal), safe HTTP fetcher, Brave search, GitHub
  llm/          provider-neutral interface + Anthropic and Groq adapters
  api/ db/      FastAPI app, SQLAlchemy models, Alembic session
frontend/       React + Vite + TypeScript dashboard
migrations/     hand-written Alembic migrations
tests/          unit/API tests + tests/injection (red team)
evals/          tool-routing eval
```

## Adding a tool

1. Define a pydantic input model and a handler `def my_tool(ctx, params) -> dict` in `app/tools/`.
2. Register it with a `Permission` (`READ` / `WRITE` / `HIGH_IMPACT`). If it returns text from outside, set `returns_untrusted=True`.
3. Add tests (and, if it touches external data, extend `tests/injection/`). Permissions, confirmation, audit and the OpenAPI/tool schemas pick it up automatically.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Built by [Malaika Adnan](https://github.com/malaikamalik23).
