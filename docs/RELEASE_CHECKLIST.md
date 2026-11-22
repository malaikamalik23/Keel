# Release checklist

Run this before tagging a version. Most of it is automated; the rest needs a person, a real account or a real
device. Record the results in the release notes (the script saves its own to `data/release-checks/`, without secrets).

## 1. CI is green (automatic)

Every push runs: lint, ~880 tests on Python 3.11 and 3.12, the red-team suite (268 tests), dashboard tests and build,
real-Chromium end-to-end tests (dashboard and Keel's browser, including attack pages), dependency audit, secret scan,
Bandit and Semgrep, and the Docker deployment with a plain and an encrypted backup and restore.

- [ ] The CI run for the release commit is green on all jobs.

## 2. On your own machine (automatic, nothing changes)

```powershell
python -m scripts.release_check
```

| Section | What it checks |
|---|---|
| local | A copy of **your** database upgrades with no rows changed; an encrypted backup round trip; an approval and a workflow step waiting across a restart; two schedulers on one database (one sends, the other stands aside); no internet (the model and Google give a clear error, the model's marked so the fallback can answer) |
| live | One read-only request per configured service (model, Gmail, Google Calendar, iCal, GitHub, notes, email login, Telegram's `getMe`, the browser starting); one tiny real chat with each configured model, with its time; a made-up key is refused with a clear error |

- [ ] 0 failed. Every SKIP is a service you haven't set up, not one that broke.
- [ ] `python -m evals.run_routing --sleep 20` on the main model: no unsafe calls, and the score is within the range in
      the README's "Honest status" (re-run misses once before calling them regressions; tag real ones `regression`).

## 3. Things that send or change something (guided, each needs your yes)

```powershell
python -m scripts.release_check --interactive
```

It runs Keel's real tools against a scratch copy of the database, so each step goes through the same approval as in
the app and shows you the approval text first. Type `yes` to do a step; anything else skips it.

- [ ] **Gmail draft** to yourself appears in Gmail's Drafts.
- [ ] **Gmail send**: one approved email to yourself arrives, exactly once.
- [ ] **Google Calendar**: a test event tomorrow at 23:00 is added, moved to 23:30 and deleted, each after approval.
- [ ] **Browser**: Keel reads a real application portal page you name (its form fields are listed); nothing is typed
      or submitted.
- [ ] **Messages**: one test message arrives by Telegram or email.

## 4. Only a person can check these

- [ ] **Dashboard**: open every tab with your real data; no errors in the browser console.
- [ ] **Approvals**: ask Keel in Chat to delete a test task; it waits under Approvals; approving it deletes it once.
- [ ] **Restart Keel** while something waits for approval: after the restart it is still there and approvable.
- [ ] **Reminders over time**: with `SCHEDULER_ENABLED=true`, a reminder set for a few hours later arrives on time; the
      morning and evening check-ins arrive on two consecutive days; a reminder due while the laptop slept arrives when
      it wakes (within a day).
- [ ] **Telegram over several days** (if you use it): replies from your phone are answered; a message from another
      account gets no answer.
- [ ] **Voice**: dictation in Chrome or Edge with a real microphone (English and Urdu), and read-aloud.
- [ ] **Phone**: the dashboard at phone width (and on a real phone if you reach Keel from one): navigation, Chat,
      Approvals.
- [ ] **Model switch**: change `LLM_PROVIDER` to each provider you use, restart, and send one message; with
      `LLM_FALLBACK` set, the second model answers while the first is rate-limited.
- [ ] **Security demo**: `python -m scripts.security_demo` shows every attack blocked.
- [ ] **Docs**: README "Honest status" and [STATUS.md](STATUS.md) match what this run found; new limitations are in
      [ROADMAP.md](ROADMAP.md).

## 5. Tag

- [ ] Bump the version in `pyproject.toml` and the API (`app/api/main.py`).
- [ ] Release notes: what changed, what was verified live in this checklist, what wasn't, known limitations.
- [ ] Take an encrypted backup before upgrading your own install (`python -m scripts.backup`).
