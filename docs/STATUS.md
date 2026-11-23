# Feature status

What exists, and how far each part has been checked. "Tested" below means automated tests that run in CI with every
outside service replaced by a mock or a local fixture; that proves Keel's own logic, not that a real service behaves
the same way. "Live" means it was run against the real service with a real account.

| Mark | Meaning |
|---|---|
| ✅ Live | Run against the real service (and covered by automated tests) |
| 🧪 Tested | Automated tests only, against mocks or local fixtures; not yet tried against the real service |
| ⏳ Needs you | Built and tested, but checking it for real needs your account, device or an action only you should take |
| 🔬 Experimental | Works, with known gaps in quality |

Last updated: 23 November 2026. Re-check with `python -m scripts.release_check` ([RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)).

## Core

| Feature | Status | Evidence / what's missing |
|---|---|---|
| Personal onboarding and profile (name, time zone, hours, goals, preferences, check-in times) | 🧪 Tested | `tests/test_profile.py`, dashboard test; settings apply without a restart |
| Capture inbox (rule-based suggestions; filed only when you approve) | 🧪 Tested | `tests/test_inbox.py`, dashboard test; checked in a browser |
| Chat (dashboard, CLI), tool loop, approvals, audit log | ✅ Live | Daily use; every eval run; `tests/test_agent.py`, e2e chat → delete → approve |
| Tasks, goals, milestones, projects, commitments, routines, waiting on others | ✅ Live | Routing eval (242 cases) on Groq and Gemini; unit tests |
| Daily and weekly planning, accountability, insights | ✅ Live | Planner guarantees unit-tested; eval cases |
| Memory lifecycle: confidence, facts no longer true kept as history, end dates, links | 🧪 Tested | `tests/test_memory_lifecycle.py`, dashboard test |
| Memory: profile, preferences, decisions, review, contradictions | ✅ Live | Eval cases; red-team memory-poisoning tests |
| Applications: tracking, drafts, eligibility, readiness | ✅ Live | Eval cases on Groq and qwen2.5:3b; unit tests |
| Workflows ("whenever X, do Y"), retries, approval steps | 🧪 Tested | `tests/test_workflows.py`; not yet run for days on a real schedule |
| Documents: PDF/Word/text, pages, deadlines, compare | ✅ Live | Real notes folder indexed and searched |
| File management: link, move, create, edit notes | 🧪 Tested | `tests/test_files.py`; 7/7 eval cases on Groq |
| Voice: dictation and read-aloud | ⏳ Needs you | jsdom tests with a fake speech API; needs a real microphone in Chrome or Edge |

## Models

| Feature | Status | Evidence / what's missing |
|---|---|---|
| Groq (`openai/gpt-oss-120b`) | ✅ Live | Routing eval, 94-98% across runs (see the README's Honest status) |
| Gemini (AI Studio key), as main model or fallback | ✅ Live | Eval: 17/18 answered on `gemini-3.6-flash`; a live fallback continued a Groq-started tool call |
| Ollama (local) | ✅ Live | `qwen2.5:3b` on a laptop CPU: 22/25 routing cases; slow (5 s median, a minute cold) |
| Anthropic | 🧪 Tested | Adapter tests only; no Anthropic key has been used yet |
| Switching provider when one is rate-limited | ✅ Live | Groq's daily limit → Gemini answered; cooldown parsed from Groq's message |
| Expired or wrong key, no internet | 🧪 Tested | Error paths unit-tested (Google sign-in expiry, early rejection and revocation included); not yet in a release checklist |

## Integrations

| Feature | Status | Evidence / what's missing |
|---|---|---|
| Gmail: search, read, triage, extract dates and action items | ✅ Live | Read-only checks against a real inbox (counts only were printed) |
| Gmail: drafts and sending (approved) | ⏳ Needs you | Mocked Google API tests incl. attachment fingerprint and daily cap; a first real draft and send need your approval |
| Google sign-in (OAuth): connect, scopes, refresh | ✅ Live | Connected with your own Desktop client; expiry and revocation are only mocked so far |
| Google Calendar: list events | ✅ Live | Read-only check against your calendar |
| Google Calendar: add, move, edit, delete (approved) | ⏳ Needs you | Mocked API tests; a first real change needs your approval |
| iCal calendar feed (read-only) | ✅ Live | `scripts/smoke_integrations` |
| Keel's browser: open, read, click, fill, attach, submit, download | 🧪 Tested | Real Chromium against local fixture pages (`tests/test_browser_rules.py`, `tests/e2e`), including a red-team page; not yet on a real website |
| Application portals (scholarships, internships) | ⏳ Needs you | Fill plans are tested on fixture forms; real portals vary, need your login, and every submit needs your approval |
| Email reminders and check-ins (SMTP) | ✅ Live | A reminder set in chat arrived on time by Gmail SMTP |
| GitHub (incl. private repos) | ✅ Live | `scripts/smoke_integrations` |
| Reading web pages (SSRF-guarded fetcher) | ✅ Live | Real pages in eval and demo; SSRF and redirect rules unit-tested |

## Data and security

| Feature | Status | Evidence / what's missing |
|---|---|---|
| Permission tiers, taint, URL gate, fencing | ✅ Live | Red-team suite (14 channels × 7 payloads); `scripts/security_demo` (12 attacks) |
| Hostile attachments, invitations, hidden page text, OAuth expiry and revocation | 🧪 Tested | `tests/test_attachment_attacks.py`, `tests/test_invite_and_oauth_attacks.py`, `tests/e2e/test_browser_attacks.py` |
| Concurrent approvals, restarts mid-workflow, two schedulers | 🧪 Tested | `tests/test_pending_concurrency.py`, `tests/test_interruptions.py` (real threads on a file database) |
| Encrypted backups, verify, restore | ✅ Live | A copy of the real database backed up, verified and restored; CI does the same inside Docker |
| Retention rules | 🧪 Tested | `tests/test_switches_retention.py`; the migration was run on a copy of real data |
| Per-integration switches | 🧪 Tested | Same tests; refusals of already-queued approvals included |
| Docker deployment (non-root, read-only, API key) | ✅ Live | CI builds and runs it, including backup and restore |
| Database encryption at rest | Not built | By design: use full-disk encryption (see SECURITY.md) |
| Multiple users | Not built | Single-user by design: one optional API key, no accounts |

## Dashboard

| Feature | Status | Evidence / what's missing |
|---|---|---|
| Dashboard: 15 pages in 7 sections (Home, Assistant, My Life, Memory, Automations, Insights, Settings) | ✅ Live | 38 jsdom tests, 13 real-Chromium e2e tests, checked at desktop and phone width |
| Phone layout | 🔬 Experimental | Checked at phone width in a desktop browser; not yet on a real phone |
| Installable app (PWA) and push notifications | 🧪 Tested | `tests/test_push.py` (real Web Push encryption, decrypted in the test), dashboard test; manifest and service worker checked in Chromium. ⏳ Needs you: install on your phone through Tailscale and send a test |
