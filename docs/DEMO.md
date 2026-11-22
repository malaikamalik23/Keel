# Demo script

A ~15 minute walkthrough that shows what Keel does and, more importantly, what it refuses to do. Use it for a
portfolio screen-recording or as a first-run checklist with your own keys.

## 0. Setup (once)

```bash
cp .env.example .env     # set GROQ_API_KEY (or GEMINI_API_KEY / ANTHROPIC_API_KEY, or LLM_PROVIDER=ollama) and AGENT_TIMEZONE
(cd frontend && npm ci && npm run build)
uvicorn app.api.main:create_app --factory
```
Open http://127.0.0.1:8000. Optional: set `NOTES_DIR` to a folder with a few `.md`/`.pdf`/`.docx` files first. Sections 6-9
need Gmail, Google Calendar or the browser connected ([INSTALL.md](INSTALL.md#6-optional-extras)); skip the ones you haven't set up.

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

1. Set `SCHEDULER_ENABLED=true` (and optionally the email variables), restart, then `Remind me in 2 minutes to stretch` (it resolves the time itself), then wait two minutes.
2. **Activity** → *Run scheduler now* runs one scheduler pass immediately: you'll see the reminder, and the morning/evening check-in if it's within its window. Without email set up, messages are stored in the *Sent notifications* table and logged.

## 5. Research, with a seatbelt

1. `Read https://arxiv.org/abs/2401.00001 and summarise it` (any public page you choose).
2. Watch `web_fetch`. Then ask it to **create a task from what it read**: because it just read the web, the write is **queued for approval**. That is the taint rule at work.
3. `Fetch http://127.0.0.1:8000/api/memories`: refused (private address). So is `http://169.254.169.254/`.
4. With `NOTES_DIR` set: `What did I write about phishing detection?` (Urdu notes work too).

## 6. Gmail, with you holding the send button

1. `What needs a reply in my inbox this week?`: `gmail_triage` sorts recent mail into deadlines, needs-a-reply, applications
   and newsletters. `Any deadlines in my email?` pulls out dates and action items.
2. `Draft a reply to Dr. Khan saying I'll send chapter 2 on Friday`: a draft appears in your Gmail Drafts. Nothing is sent.
3. `Send it`: it waits under **Approvals** with the exact recipients, subject, text and attachments (and a warning for an
   address you've never written to). Reject it for the demo, or approve it to send.
4. Because the conversation read email, a follow-up like `create a task from that email` also waits for approval.

## 7. Google Calendar changes, one approval at a time

1. `What's on my calendar this week?` (`gcal_list_events`).
2. `Add a 1-hour FYP meeting on Thursday at 3pm`: it waits under **Approvals**, with a warning if it overlaps something.
3. `Put today's plan in my calendar`: the plan's time blocks, once (asking again doesn't duplicate them).
4. Open the **Calendar** tab: meetings, plan blocks, deadlines and promises in one week view.

## 8. Applications, prepared but never submitted by Keel

1. `Track the DAAD EPOS scholarship, deadline 15 December, needs CGPA 3.0 and IELTS 6.5`.
2. `Am I eligible?`: checked against your saved profile; unknowns are marked, not guessed.
3. `What's ready for DAAD?`: documents found in your notes, linked documents, drafts, and what's missing.
   `Link my transcript to the DAAD application` connects a file (**Documents** tab shows it).
4. With `BROWSER_ENABLED=true`: `Open the DAAD portal page and tell me what the form asks for`. Typing into a field, attaching
   a file and submitting each wait for your approval; a button that would submit, pay or sign in is refused outright.

## 9. Your data, your switches

1. **Services** tab: switch Gmail off, then ask `check my email`: Keel says it's switched off instead of working around it.
   An email queued before you switched it off is refused when approved. Switch it back on.
2. **Data retention** card: set *Audit log* to 90 days and press *Delete expired now…*: it shows how many rows would go
   before deleting anything.
3. With `BACKUP_PASSPHRASE` set: **Back up now**, then `python -m scripts.backup verify <file>`.

## 10. Prove the security claims

```bash
pytest tests/injection -q
```
The red-team tests pass (14 channels × 7 payloads, plus memory poisoning, link choice and summary taint). For a more convincing demo, **break a defence and watch it fail**: in `app/security/permissions.py` change
`taint_escalation: bool = True` to `False`, re-run, then `git checkout app/security/permissions.py`.

## 11. Measure the model, not your hopes

```bash
python -m evals.run_routing --limit 15
```
Prints a per-category score and every misrouted prompt.

## First real-keys smoke test (checklist)

`python -m scripts.release_check` (and `--interactive` for the steps that send or change something) automates most of
this; the full list is in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). By hand, confirm each once with your own
credentials (what has already been confirmed is in [STATUS.md](STATUS.md)):

- [ ] Models: one chat on each provider you use; with `LLM_FALLBACK` set, the second one answers when the first is rate-limited
- [ ] Gmail: search, read, a draft appears in Drafts; one approved send to yourself arrives
- [ ] Google Calendar: list; one approved add, move and delete on a test event
- [ ] Browser: open a real portal page and read its form; stop before submitting
- [ ] Encrypted backup: back up, `verify`, restore onto a copy
- [ ] Restart Keel while an approval and a workflow are waiting: both are still there and can be answered
- [ ] Email: `Run scheduler now` delivers a reminder to your inbox
- [ ] GitHub: `List my GitHub repositories`; `Show open issues on <your repo>`
- [ ] Calendar: `What's on my calendar tomorrow?` (iCal secret address)
- [ ] Notes: `/index` reports files added; a search finds them
- [ ] Docker: `docker compose up --build`, then `curl -H "X-API-Key: ..." http://127.0.0.1:8000/api/tasks`
