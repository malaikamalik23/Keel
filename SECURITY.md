# Security

Keel is an AI agent that can read the web, your notes and your calendar, and can change your tasks, memory and
reminders. That combination is exactly where **prompt injection** lives, so security is a design constraint here, not
a footnote. This document explains the threat model, what is enforced, what is *not* defended, and how to report a bug.

## Reporting a vulnerability

Please **don't open a public issue** for security bugs. Use GitHub's *Report a vulnerability* button on the
repository's **Security** tab (private advisory). Include what you did, what you expected, and what happened. I aim to
reply within a few days. This is a personal open-source project, so there is no bug bounty, but I'm grateful and will
credit you if you want.

## Scope and intended use

Keel is built for **one user on their own machine or server**. It is not multi-tenant: there is a single shared
`API_KEY`, no per-user accounts and no per-user data isolation. Don't expose it to the internet without a TLS reverse
proxy in front (the app itself speaks plain HTTP) and a strong `API_KEY`. Without `API_KEY` the API answers only
requests made directly from the same machine: a server started with `--host 0.0.0.0`, or reached through a reverse
proxy (detected by its `X-Forwarded-For` / `X-Real-IP` / `Forwarded` header), refuses every `/api` request until a key is set.
The same keyless mode also refuses requests that a **website open in your browser** could send: any `Host` other than
`localhost` / `127.0.0.1` / `[::1]` (DNS rebinding, where a hostile domain is re-pointed at your machine so the browser
lets it read the API), and state-changing requests marked cross-site by `Sec-Fetch-Site` or `Origin` (CSRF, e.g. a page
silently approving a pending delete). With `API_KEY` set, the key is the protection: a website cannot send it.

## Threat model

The attacker **cannot talk to Keel directly**. The realistic attack is *indirect*: they get text in front of the model
through something Keel reads, hoping the model treats it as instructions:

| Channel | How attacker text arrives |
|---|---|
| Web pages / search results | A poisoned page, or an SEO-poisoned search snippet |
| GitHub | A malicious README, issue title or commit message in a repo you ask about |
| Calendar | An invite from a stranger (titles are attacker-controlled) |
| Notes / PDFs | A downloaded paper or pasted web text in your notes folder |

The attacker's goals: make Keel **change or delete your data**, **leak your data** (memories, tasks, plans) to a server
they control, or **reach into your private network** (your own API, cloud metadata) through the agent's fetch tool.

**Design assumption: the model is untrustworthy once it has read external text.** Nothing below relies on the model
choosing to behave. The red-team suite simulates a model that obeys every injected instruction.

## What is enforced (in code)

1. **Permission tiers.** Every tool is READ, WRITE or HIGH_IMPACT. Deletes are HIGH_IMPACT: the model can only *queue*
   them; only you (dashboard, CLI or REST) can approve. There is no tool that approves, rejects or edits the queue. A queued action is re-checked at approval time: if its target changed in the meantime (for example SQLite reused the id of a deleted task, or you edited the memory it would overwrite), it is refused instead of acting on the wrong item. Approval is an atomic compare-and-set, so two approvals arriving at once (dashboard and CLI, a double click) run the action **exactly once**, and an approve that loses to a reject does nothing. Unanswered requests expire after `PENDING_ACTION_TTL_HOURS` (24 by default).
2. **Taint tracking.** When any tool returns external text, the session is *tainted*. While it is, every WRITE tool also
   needs your approval. Taint lasts **for the rest of that conversation**, not just while the content is in the model's
   replayed history window: after the page leaves the window, the running summary or the agent's own replies ("the
   page says you've authorised…") can still repeat it in words that carry no fence. A summary of a tainted conversation
   is also shown to the model fenced as untrusted data. Start a new chat for a clean session.
3. **Untrusted fencing.** External text is wrapped in `<untrusted_content>` tags. The closing tag is neutralised
   (case/whitespace-insensitive) so content cannot break out, and the system prompt says the contents are data.
4. **Invisible-text stripping.** Unicode format/private-use/control characters (including the "tag" block used for
   ASCII smuggling, zero-width characters and bidi overrides) are removed from all external text. Text hidden from humans in
   HTML (`display:none`, `hidden`, `aria-hidden`, `<script>`, comments…) never reaches the model.
5. **Exfiltration gate.** In a tainted conversation `web_fetch` runs without approval only for URLs the *user typed*
   and the result URLs of a `web_search`. Everything else is queued for approval: a URL the model assembled itself (the
   way data gets smuggled out in a query string), and a link found *inside* external text (a fetched page, a search
   snippet, a README, an issue, a note, a calendar event), because choosing among an attacker's links is a way to
   signal to them. URLs the model wrote, and the text of queued/error results, never count, so a retry can't slip through.
6. **SSRF protection.** `web_fetch` allows only http(s) on ports 80/443, no credentials in URLs, and only hosts that
   resolve to **public** addresses (loopback, private, link-local, cloud-metadata, multicast, reserved and IPv4-mapped
   IPv6 are refused; decimal/octal IP tricks are caught because the resolved address is checked). Redirects are followed manually,
   max 3, and **each hop is re-validated**. Responses are capped at 1 MB and limited to text types.
   `WEB_ALLOWED_DOMAINS` can restrict it to a short list. **DNS pinning:** the host is resolved and checked inside the
   connect step and the socket goes to that exact address, so there is no second lookup a hostile DNS server could
   answer with `127.0.0.1` (DNS rebinding). TLS still verifies the certificate against the hostname. Proxy settings
   from the environment are ignored, since a proxy would do its own lookup.
7. **Notes confinement.** Only files physically inside `NOTES_DIR` are indexed (symlinks that point outside are skipped).
   `read_note` serves text from the index, never from disk, so there is no path to traverse.
8. **Secrets stay out of messages.** Errors from Telegram, Brave and GitHub clients never include URLs or headers
   (the Telegram bot token lives in the URL, API keys in headers). Tokens are never logged or returned to the model.
9. **Plain-text output.** The dashboard renders model output as text (no HTML/Markdown), and Telegram messages are sent
   without `parse_mode`, so a Markdown image/link in a reply is not an exfiltration channel.
10. **API hardening.** `X-API-Key` (constant-time compare) or, without one, local-only access with Host and
    cross-site checks (see Scope), a per-client rate limit on LLM-calling endpoints,
    security headers (CSP `default-src 'self'`, `frame-ancestors 'none'`, `nosniff`), `Cache-Control: no-store` on API
    responses. Docker: non-root, read-only root filesystem, all capabilities dropped, published on `127.0.0.1` only.
11. **Memory hygiene.** Memories are pasted into every later system prompt, where the session is no longer tainted, so
    they are guarded where they are written and where they are shown: invisible characters are stripped before storing,
    the approval request shows exactly the text that will be stored (and, for an edit, the text it replaces), and the
    context pack shows each stored item as one flattened, clipped line inside a section labelled as user data, so it
    can't forge a heading of the system prompt or close a fence.
12. **Audit log.** Every tool call (allowed, queued, rejected, failed, expired) is recorded with actor and parameters.
13. **Telegram.** Only messages you send in your private chat with the bot are answered (the chat id must match, the chat
    must be private and the sender must be you); groups and everyone else are ignored silently. Approving
    actions is deliberately **not** possible from Telegram.

Tool *advertising* is not part of this model: in the default lean profile each request lists only some tools, but the executor applies every rule above to any tool the model tries to call, advertised or not (there is a test for that).

## How this is tested

`tests/injection/` runs on every push. It builds a world where **web pages, search results, GitHub READMEs and issues,
notes and calendar events all contain attack text** (7 payload styles: plain override, fence breakout, fake tool JSON,
fake user consent, Urdu/Roman Urdu, invisible Unicode, Markdown-image exfiltration), then drives a *fully compromised*
model that tries 17 harmful calls: deletes, writes, memory poisoning, exfiltration URLs, requests to `127.0.0.1` and
cloud metadata. It asserts that nothing executed, the database is byte-for-byte unchanged, no forbidden request was
made, and the audit log shows only queued or refused attempts.

Further suites cover the cases that are about *time* rather than one turn:

- `tests/injection/test_memory_poisoning.py`: every external channel tries to plant or edit a memory; a fresh, clean
  session the next day must not see it. Also: hidden characters never reach storage, what you approve is what's stored,
  an approved edit is refused if the memory changed meanwhile, a stored memory can't fake a system-prompt section, and
  a model that obeys a poisoned memory still can't delete anything.
- `tests/injection/test_summary_taint.py`: a poisoned page leaves the history window and is folded into the
  summary (with a summarizer that obeys it, the worst case); the next write is still held for approval and the summary
  reaches the model fenced. The same holds when the agent's own reply repeats the page and summaries are off or the
  summarizer call fails. A summary of only your own words taints nothing, and a new chat is clean. Before these fixes,
  each of those writes ran unapproved.
- `tests/test_api_auth.py`: without a key, LAN/public/IPv6 peers, proxied requests, hostile `Host` names (DNS
  rebinding) and cross-site POSTs (CSRF on the approve/reject endpoints) get 403 and nothing changes; the dashboard,
  the Vite dev proxy, curl and the CLI still work. The CSRF case was also run from a real Chromium page on another
  origin: before the fix it deleted the task, after it the API answers 403.
- `tests/injection/test_link_choice.py`: links listed by a fetched page, or found in a search snippet, README, issue,
  note or calendar event, are held for approval in the same turn and in later turns, and no request reaches the
  attacker's host; search results and URLs you type still open freely.
- `tests/test_pending_concurrency.py`: two approvals at once, approve vs reject in both orders, and 8 real threads
  approving the same action against a file database. Exactly one run, every time.
- `tests/test_dns_pinning.py`: a resolver that answers public first and `127.0.0.1` / `10.x` / `169.254.169.254` /
  `::1` second is refused before any socket opens; the connection goes to the checked IP; HTTPS keeps the hostname for
  certificate checks; and a guard fails if an httpx upgrade silently removes the pinning.

`python -m scripts.security_demo` runs twelve of these attacks in a readable form; `tests/test_security_demo.py` checks that it
reports a failure when the permission gate, the fetch gate, the browser checks or the local-only check is switched off.
CI also runs **Bandit** and **Semgrep** (python, security-audit and secrets rule sets) on every push.

The suite was **mutation-checked**: each defence (taint escalation, HIGH_IMPACT tier, exfiltration gate, fence
neutralisation, invisible-character stripping, hidden-HTML removal, SSRF check, untrusted flag) was deliberately
broken, and the suite failed every time. The newer suites were checked the same way: with the old approval code, the
old memory handling or the pinning switched off, they fail. A test that can't fail proves nothing; these can.

## Known limitations (read these)

- **Taint is coarse and sticky.** Once a conversation has read untrusted content, *all* writes need approval, even
  ones you asked for in plain words, and it lasts for the rest of that conversation. Correct but sometimes annoying:
  start a new chat after research.
- **The fetcher is not a firewall.** DNS pinning closes rebinding for `web_fetch`, which is the only component that fetches
  URLs the model chooses. Other integrations (GitHub, Brave, Telegram, your calendar URL) go to fixed hosts you configured.
  For defence in depth on a server, still block private ranges with an egress firewall or network policy.
- **Following a link inside a page needs your approval** once a conversation is tainted. That is the price of closing
  the link-choice leak (a page listing ten links could otherwise learn a digit from *which* one an injected model
  opens). Search results and URLs you type are unaffected. What remains: an injected model can still choose *which
  search result* to open and what to search for. An attacker would need several of their own pages ranked for a query
  to read anything from that, and the query goes to your search provider, not to them. `WEB_ALLOWED_DOMAINS` closes it.
- **Approved ≠ safe.** The gate puts a human in the loop; it can't judge the action for you. Read what you approve,
  especially approval requests that appear right after reading external content.
- **Memory poisoning needs your approval, but a tricked user is still a risk.** In a tainted session `save_memory` is
  queued; if you approve a malicious memory, it persists and is shown to the model in future context. There it can only
  mislead the model: deletes still need approval, but ordinary writes (editing or completing a task) would run, because
  a clean session is not tainted. Read memory approvals carefully; you can see and delete every memory in the dashboard.
- **Visible-text injection still reaches the model** (that is the point of reading a page). The defences limit what it
  can *do* with that text; they don't make the model immune to being misled in its *answers*. Verify important claims.
- **Information disclosure through the model's reply.** The model can still quote what it has read (including your
  memories) in its answer to you. That goes to you, not to the attacker, unless a channel like a rendered image exists (see 9).
- **Single shared API key, no TLS, no per-user auth.** See scope.
- **Prompts and tool descriptions are sent to your LLM provider**, along with tool results and your data that the agent
  reads. Choose a provider you trust with that data.
- **The database is not encrypted at rest.** See "Your data" below for why and what to do instead.
- **Not independently audited.** Treat this as careful hobby-grade engineering with a strong test suite, not a certified product.

## Your data: retention, backups, encryption

**What is stored, and for how long.** Everything lives in one SQLite file (`data/agent.db` by default) on your machine,
and Keel keeps it **until you delete it**. That is deliberate: the accountability features ("what have I been
avoiding?", postponement reasons, promises kept) are only honest if the history is complete, and an agent that quietly
deletes your data is worse than one that keeps it. Nothing is sent anywhere except to the LLM provider you chose (the
messages and tool results of each request) and the services you configured.

| Data | Kept | How to remove it |
|---|---|---|
| Tasks, goals, projects, commitments, plans, work sessions, reminders | until you delete them | dashboard / chat (deletes need approval) |
| Memories | until you delete them | dashboard Memory tab |
| Conversations | until you delete the file; only the recent window is sent to the model, older turns are summarised | delete the database |
| Audit log, task events (postponement history) | until you delete the file | these are the evidence trail; delete the database to reset |
| Notes index | rebuilt from `NOTES_DIR` | remove the file from the folder and re-index |
| Pending approvals | expire after `PENDING_ACTION_TTL_HOURS` (default 24) | approve or reject |

To erase everything, stop Keel and delete the database file (and its backups). Secrets live only in `.env`.

**Backups.** `python -m scripts.backup` writes a consistent copy to `data/backups/` (SQLite's online backup API, safe
while Keel is running; the copy is integrity-checked) and keeps the newest 14 (`--keep`, `--dir`). Backup files are created readable by their owner only (mode 600). Run it from a daily
scheduled task. Backups contain everything above, so store them as carefully as the database. To restore, stop Keel and
copy a backup over the database file.

**Encryption at rest: not built in, on purpose.** Encrypting the SQLite file (e.g. SQLCipher) means a native dependency
that breaks `pip install` on many machines, and a key that has to live somewhere: in `.env` next to the database it
protects, which adds little, or typed in on every start, which breaks the scheduler and Docker. Full-disk encryption
already protects the file against the realistic threat (a lost or stolen laptop) with none of those costs, so use it:
BitLocker (Windows), FileVault (macOS) or LUKS (Linux), and keep backups on an encrypted drive too.
