# Demo script

A ~10 minute walkthrough that shows what Keel does and, more importantly, what it refuses to do. Use it for a
portfolio screen-recording or as a first-run checklist with your own keys.

## 0. Setup (once)

```bash
cp .env.example .env     # set ANTHROPIC_API_KEY (or GROQ_API_KEY) and AGENT_TIMEZONE
(cd frontend && npm ci && npm run build)
uvicorn app.api.main:create_app --factory
```
Open http://127.0.0.1:8000. Optional: set `NOTES_DIR` to a folder with a few `.md`/`.pdf` files first.

## 1. It turns an ambition into a plan (Chat tab)

1. `I want to get good at web penetration testing`: watch the tool chips: `create_goal`, `add_milestones`, `milestone_to_tasks`.
2. Open **Goals**: the goal and its ordered milestones are there. Open **Today**: the first tasks are listed.
3. `Plan my day, I have 4 hours`: it builds a plan that fits (15% buffer, ≤3 major priorities). If you ask for far
   more than fits, the reply explains what was **deferred and why**.

## 2. It keeps your promises on the record

1. `I'll finish the GRE quant set by Friday`: it proposes a commitment and asks you to confirm.
2. `Yes`: it becomes active (**Commitments** tab).
3. `Move the GRE task to next Monday, it's too big`: it records a postponement **with your reason**.
4. Repeat once more, then ask `What have I been avoiding?` The answer cites the real count and the reasons you gave. Open **Insights** → "Why things slip".

## 3. It can't do anything destructive on its own

1. `Delete the GRE task`: the reply says it is **waiting for your approval**. Open **Approvals**: the exact action is there. Nothing was deleted.
2. Click **Approve**: now it's gone. Open the audit log table: both steps are recorded (`pending_confirmation`, then `ok` by `user`).

## 4. It stays useful while you're away

1. Set `SCHEDULER_ENABLED=true` (and optionally the Telegram variables), restart, then `Remind me in 2 minutes to stretch` (it resolves the time itself), then wait two minutes.
2. **Activity** → *Run scheduler now* runs one scheduler pass immediately: you'll see the reminder, and the morning/evening check-in if it's within its window. Without Telegram, messages are stored in the *Sent notifications* table and logged.

## 5. Research, with a seatbelt

1. (`BRAVE_API_KEY` set) `Search for recent papers on explainable intrusion detection and summarise the top two.`
2. Watch `web_search` → `web_fetch`. Then ask it to **create a task from what it read**: because it just read the web, the write is **queued for approval**. That is the taint rule at work.
3. `Fetch http://127.0.0.1:8000/api/memories`: refused (private address). So is `http://169.254.169.254/`.
4. With `NOTES_DIR` set: `What did I write about phishing detection?` (Urdu notes work too).

## 6. Prove the security claims

```bash
pytest tests/injection -q
```
The 113 red-team tests pass (7 channels × 7 payloads, plus structural checks). For a more convincing demo, **break a defence and watch it fail**: in `app/security/permissions.py` change
`taint_escalation: bool = True` to `False`, re-run, then `git checkout app/security/permissions.py`.

## 7. Measure the model, not your hopes

```bash
python -m evals.run_routing --limit 15
```
Prints a per-category score and every misrouted prompt.

## First real-keys smoke test (checklist)

The integrations are tested against mocks. Confirm each once with your own credentials:

- [ ] Telegram: bot token + chat id set; `Run scheduler now` delivers a message; reply from your phone gets an answer; a message from another account gets none
- [ ] Brave: `web_search` returns results
- [ ] GitHub: `List my GitHub repositories`; `Show open issues on <your repo>`
- [ ] Calendar: `What's on my calendar tomorrow?` (iCal secret address)
- [ ] Notes: `/index` reports files added; a search finds them
- [ ] Docker: `docker compose up --build`, then `curl -H "X-API-Key: ..." http://127.0.0.1:8000/api/tasks`
