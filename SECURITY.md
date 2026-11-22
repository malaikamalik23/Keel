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
| Notes / PDFs / Word | A downloaded paper, a .docx someone sent you, or pasted web text in your notes folder |

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
   HTML never reaches the model: `<script>`, comments, the `hidden` and `aria-hidden` attributes, and inline styles that
   hide it (`display:none`, visibility, opacity 0, zero or 1px fonts, transparent colour, off-page positions, `clip`,
   zero-size boxes, Outlook's `mso-hide`). That covers web pages, Gmail and Google Calendar invitations, whose
   description is HTML written by whoever sent the invite. Keel's browser renders the page and leaves out anything a
   person couldn't see, including text hidden by stylesheets.
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
   without `parse_mode`, so a Markdown image/link in a reply is not an exfiltration channel. Calendar event titles are written by whoever sent
   the invite, so the morning briefing never hands them to the model: code appends them after the model's text, as plain
   lines with links removed, and the `get_briefing` tool leaves them out.
   The planner, the week overview, task-to-meeting links and the automatic re-planning use only the *times* and
   ids of calendar events, never their titles; the messages about a moved or cancelled meeting name the user's own task. The plan export
   (`/api/plan.ics`) escapes task titles so a title can't add lines or events to the file.
   Applications: Keel never submits anything. Fields copied from a posting are stored as plain text (no invisible
   characters; titles as one line), links must be plain http(s), and the dashboard only makes http(s) links clickable.
   Drafts are written only from the user's own profile; missing facts are left as [ADD: ...], never invented.
   Projects: log entries are stored as plain text with untrusted-content tags neutralised, so a stored entry can't fake a
   fence later. Attached notes must already be indexed (no arbitrary paths), links must be http(s), and a repo must be a
   valid `owner/name`; GitHub stays read-only (pull requests are listed, never changed).
   Documents: Word files are parsed with `defusedxml`, refused if they declare a DOCTYPE or unzip to more than 20 MB,
   and text Word marks as hidden is left out (and counted) because hidden text is a way to show the model what the
   reader can't see. Every tool that returns document text (read, outline, compare, deadlines) is fenced as untrusted,
   and the deadlines and action items it finds are suggestions: making them tasks is a write after reading external
   content, so it waits for the user's approval.
   Workflows (standing "when X, do Y" rules) act later without the user present, so: creating one is high impact
   (the user approves the exact trigger and steps); steps may only use Keel's own tools, never one that reads
   external content, so nothing a run reads can steer it; high-impact steps always pause for the user's approval of
   that run, and the model has no tool that approves; runs use a clean, untainted context and are audited with
   actor `workflow`; templates are plain lookups ({{event.title}}), not an expression language; and a step that may
   have been cut off by a restart is not repeated without the user's OK. The red-team suite tries to create and run
   workflows from injected text.
   The Services tab says whether each key is set, never its value: no key, token, password, Telegram chat id or the
   calendar's secret address is ever returned (only the calendar's host name, and your email as `m•••@domain`), and a
   failed check's message is scrubbed of every configured secret. Each check is one read-only request: Telegram uses
   `getMe`, email logs in and hangs up, and nothing is sent. The Calendar tab shows invite titles as plain text with
   links removed, and none of it is passed to the model.
   Gmail is the biggest injection channel, because anyone can email you. So: every tool that reads mail is
   `returns_untrusted` (fenced, sanitised, taints the turn, and HTML is reduced to visible text); a draft is a write, so
   once mail has been read in a turn it waits for approval too; sending is high impact, and the approval shows the exact
   recipients, subject, text and attachments (with a content fingerprint), warns about any address you have never
   written to or heard from, and is re-checked at approval time, so an email changed after you approved it is not
   sent. At most `GMAIL_MAX_SENDS_PER_DAY` sends a day; workflows can't send mail; addresses and subjects can't carry
   extra headers. Keel asks Google only for read and compose access (no delete, labels or filters). The refresh token
   stays in a local file under `data/` (never committed); access tokens live in memory only and no error message
   contains a token. The red-team suite reads a poisoned inbox and tries to send and draft mail to the attacker.
   Google Calendar (off by default) uses the same sign-in with only the `calendar.events` permission. Listing events is
   untrusted (invites carry other people's text). Every change (add, move, edit, delete, adding a plan) is high impact:
   the approval shows the exact change, warns about overlaps (by time only, so another invite's title can't speak in
   the approval), guests and repeating events, and includes the event's last-changed stamp, so if it was edited before
   you approve, nothing happens; the write itself is conditional on that version (If-Match). Guests are never emailed
   (sendUpdates=none). A day's plan is never added twice. The red-team suite reads a poisoned invite and tries to
   create, move and delete events.
   Keel's browser (off by default) is a separate Chromium with a fresh profile per start. Every request a page makes
   (scripts, images, forms, WebSockets) goes through a guard that refuses non-public addresses, so a page can't reach
   Keel's own API, the router or cloud metadata; service workers are blocked, because their requests would bypass the
   guard. Page text is untrusted and hidden text is left out. Clicks that only navigate
   are free; a click that would post a form or reads like a consequential action is refused, and the only way to do it
   is `browser_submit`, which is high impact: the approval shows the site, the button and every value the form holds,
   and it is recomputed at approval time, so a form changed in between isn't sent. Typing into a page and attaching a
   file are high impact too (page scripts can read typed text, so it is disclosure). Password, payment, one-time-code
   and ID-number fields are never filled, CAPTCHAs are never solved, and workflows can't use the browser. A real-
   Chromium red-team test has a compromised model follow an attacker's page: nothing is posted, typed, attached or
   fetched.
   Application help only reads. Eligibility is computed from the user's saved profile against the stated requirements
   and reports unknowns as unknown (a requirement it can't parse is "check yourself"), so a model can't simply declare
   the user eligible. Discovery results from the web and email are untrusted like any other external text. The form
   plan proposes values only from saved facts and drafts, leaves password, payment and ID fields to the user, and
   fills nothing itself: browser_fill does that, after the user approves.
   File management stays inside NOTES_DIR (paths resolved, hidden folders and symlink escapes refused) and never
   overwrites. Moving a file is high impact (other apps may point at it). Editing is limited to text notes, must match
   exactly one passage, keeps the old version in `.keel-backups/` (not indexed), and waits for approval once untrusted
   text has been read; workflows can't move or edit files. Links are stored by path and survive re-indexing.
   Model providers see what Keel sends them. With `LLM_FALLBACK=gemini` on Google AI Studio's free tier, Google states
   that content is used to improve its products; the Services tab shows that note whenever Gemini is configured, and
   the install guide recommends the paid tier or a local fallback for sensitive data. The fallback only switches on a
   busy or unreachable provider (never hides a real error), and both models run under the same tool permissions,
   taint rules and approvals.
   With `LLM_PROVIDER=ollama` the model runs on the user's own computer, so no chat, memory or document text goes to a
   model provider. Keel refuses a plain-`http` Ollama address on another machine (only `https` there), so chats are
   never sent unencrypted over a network. A local model gets the same tool permissions, taint rules and approvals.
   Uploads from the dashboard go only to `NOTES_DIR/uploads/`: the name is reduced to a plain file name with an
   allowed extension (no folders, hidden files or Word lock files), the content must match it (PDF header, readable
   .docx, UTF-8 text), the 2 MB limit is enforced while the body streams in, nothing is overwritten, and the dashboard
   can delete only files in `uploads/`, never the user's own notes.
10. **API hardening.** `X-API-Key` (constant-time compare) or, without one, local-only access with Host and
    cross-site checks (see Scope), a per-client rate limit on LLM-calling endpoints,
    security headers (CSP `default-src 'self'`, `frame-ancestors 'none'`, `nosniff`, and a Permissions-Policy that lets
    only Keel's own page use the microphone and turns off camera, location and payments), `Cache-Control: no-store` on API
    responses. Voice: dictation only fills the message box (the user still presses Send, so a misheard phrase can't
    act), and the dashboard says that Chrome and Edge send dictation audio to their own speech services. Keel's
    server never receives audio, only the text you send. Docker: non-root, read-only root filesystem, all capabilities dropped, published on `127.0.0.1` only.
11. **Memory hygiene.** Memories are pasted into every later system prompt, where the session is no longer tainted, so
    they are guarded where they are written and where they are shown: invisible characters are stripped before storing,
    the approval request shows exactly the text that will be stored (and, for an edit, the text it replaces), and the
    context pack shows each stored item as one flattened, clipped line inside a section labelled as user data, so it
    can't forge a heading of the system prompt or close a fence. Profile topics are reduced to plain lower-case words.
    A stored profile fact can't be silently replaced: a different value for the same topic is saved only after the user
    decides, and in a tainted conversation the edit is an approval request that shows the old and the new value.
12. **Audit log.** Every tool call (allowed, queued, rejected, failed, expired) is recorded with actor and parameters.
13. **Telegram.** Only messages you send in your private chat with the bot are answered (the chat id must match, the chat
    must be private and the sender must be you); groups and everyone else are ignored silently. Approving
    actions is deliberately **not** possible from Telegram.
14. **Email.** Reminders and check-ins go to the single address in `EMAIL_TO`, from configuration only: no tool can
    send email, and the model can't choose a recipient. Only encrypted connections (STARTTLS or TLS, certificate
    checked) are used, the message is plain text, its subject can't add headers, and errors never include the password
    or the server's reply.

Tool *advertising* is not part of this model: in the default lean profile each request lists only some tools, but the executor applies every rule above to any tool the model tries to call, advertised or not (there is a test for that).

## How this is tested

`tests/injection/` runs on every push. It builds a world where **web pages, search results, GitHub READMEs and issues,
notes, Word documents, iCal and Google Calendar events and Gmail messages and threads all contain attack text** (15
channels; 7 payload styles: plain override, fence breakout, fake tool JSON,
fake user consent, Urdu/Roman Urdu, invisible Unicode, Markdown-image exfiltration), then drives a *fully compromised*
model that tries 17 harmful calls: deletes, writes, memory poisoning, exfiltration URLs, requests to `127.0.0.1` and
cloud metadata. It asserts that nothing executed, the database is byte-for-byte unchanged, no forbidden request was
made, and the audit log shows only queued or refused attempts. Keel's browser has its own real-Chromium red-team test
(`tests/e2e/test_browser_tools.py`), because its pages are live documents rather than text.

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
- `tests/test_interruptions.py`: the same for workflow steps (6 threads approving one step; a stale second click or a
  late cancel is refused), a restart while an approval and a workflow step are waiting (both still answerable), and two
  schedulers ticking one database at the same moment (one lease: nothing is sent twice; the other takes over when the
  first stops or dies). Before these fixes, all six approvals ran the step and both schedulers sent the reminder.
- `tests/test_attachment_attacks.py`: email attachments named to escape the notes folder (`../`, absolute and Windows
  paths, device names such as `CON.md`), disguised (`invoice\u202efdp.exe`, `report.pdf.exe`), lying about their type,
  zip bombs, binary text and oversized files; a poisoned attachment saved after reading the email waits for approval and
  is fenced when read; broken PDFs are skipped, not fatal.
- `tests/test_invite_and_oauth_attacks.py`: a poisoned invitation (instructions in hidden HTML, the location and a
  guest's name) is read as visible text with no guest addresses, and a model obeying it changes nothing; inline-style
  hiding in HTML; an access token that expires or that Google stops accepting early (refreshed and retried once; an
  approved email is still sent exactly once); a sign-in revoked between queueing and approving (nothing happens, the
  approval fails with how to reconnect); Google unreachable or a corrupt token file.
- `tests/e2e/test_browser_attacks.py`: eleven ways a page hides instructions (opacity, tiny fonts, transparent text,
  clipping, off-page, zero-size boxes, aria-hidden...), invisible buttons and links, and a page that opens a WebSocket
  to this computer and registers a service worker that fetches it. Before the fixes, ten of the eleven hidden texts
  reached the model and the page reached a local port.
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
- **The fetcher is not a firewall.** DNS pinning closes rebinding for `web_fetch`. Keel's browser checks every request's
  address too, but Chromium then resolves the name again itself, so a DNS-rebinding site could in principle switch to a
  private address between the check and the connection. Other integrations (GitHub, Brave, Telegram, your calendar URL)
  go to fixed hosts you configured. For defence in depth, especially on a server, block private ranges with an egress
  firewall or network policy.
- **Hidden-text detection is heuristic.** Pages can hide text in ways a rule doesn't recognise (white text on a white
  image, text behind another element). The browser leaves out what its checks find; the rest is still fenced and still
  can't act without your approval.
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
and Keel keeps it **until you delete it**, unless you set retention rules. Keeping is the default on purpose: the
accountability features ("what have I been avoiding?", postponement reasons, promises kept) are only honest if the
history is complete, and an agent that quietly deletes your data is worse than one that keeps it. Nothing is sent anywhere except to the LLM provider you chose (the
messages and tool results of each request) and the services you configured.

| Data | Kept | How to remove it |
|---|---|---|
| Tasks, goals, projects, commitments, plans, work sessions, reminders | until you delete them | dashboard / chat (deletes need approval) |
| Memories | until you delete them | dashboard Memory tab |
| Conversations | forever, or your retention rule (7 days or more); only the recent window is sent to the model, older turns are summarised | a retention rule, or delete the database |
| Audit log, sent-messages log, check-ins, finished approvals and workflow runs | forever, or your retention rule (30 days or more; 7 for approvals) | a retention rule |
| Task events (postponement history) | forever, or your retention rule (90 days or more: shorter makes accountability less complete) | a retention rule |
| Old versions of edited notes (`NOTES_DIR/.keel-backups/`) | forever, or your retention rule (7 days or more) | a retention rule, or delete the folders |
| Notes index | rebuilt from `NOTES_DIR` | remove the file from the folder and re-index |
| Pending approvals | expire after `PENDING_ACTION_TTL_HOURS` (default 24) | approve or reject |

**Retention rules** (Services tab, "Data retention") apply to those logs and history only, never to what you made
yourself, and only you can set them: there is no tool for it, so the model can't shorten or lengthen what is kept. The
floors keep what Keel needs (recent sent messages stop duplicates; a request waiting for approval keeps its
conversation). "Delete expired now" shows how many rows each rule would remove before anything is deleted; with the
scheduler on, the rules run once a day. Deletion uses SQLite's `secure_delete`, so removed rows are overwritten in the
file; backups taken earlier keep them until they are rotated out (`BACKUP_KEEP`).

**Integration switches.** Each connected integration on the Services tab (Gmail, Google Calendar, Keel's browser, web
pages, web search, GitHub, the notes folder, and reminders/check-ins) has an on/off switch. Off takes effect at once:
its tools aren't offered to the model, and the executor refuses them for chat, for workflow steps and for approvals
that were already waiting (an email queued before you switched Gmail off is not sent when approved later; the refusal
is in the audit log). With messages off, nothing is sent; reminders that fall due are sent if you switch back on within
a day. A switch can only take a capability away: what `.env` hasn't set up can't be switched on, and only the dashboard
(not the model) can change a switch.

To erase everything, stop Keel and delete the database file (and its backups). Secrets live only in `.env`.

**Backups.** `python -m scripts.backup` writes a consistent copy to `data/backups/` (SQLite's online backup API, safe
while Keel is running; the copy is integrity-checked) and keeps the newest 14 (`BACKUP_KEEP`, `--keep`, `BACKUP_DIR`,
`--dir`). Backup files are created readable by their owner only (mode 600).

With `BACKUP_PASSPHRASE` set (12+ characters), a backup is **one encrypted file** (`agent-<stamp>.keelbak`) holding the
database and the notes folder (`BACKUP_INCLUDE_NOTES`; hidden files and folders and symlinks are skipped):

- **Encryption:** AES-256-GCM in 64 KiB chunks; the key comes from the passphrase through scrypt (N=2^17, r=8, p=1: about
  a second and 128 MB per guess). Each chunk is authenticated together with the header and its position, so a wrong
  passphrase, a changed byte, reordered or missing chunks and a cut-off file are all refused. Nothing unencrypted is
  written to the backup folder; a short-lived database copy sits in your temp folder while the file is made.
- **Never included:** `.env` and the Google token. Keep `.env` (and the passphrase) in your password manager: without
  the passphrase nobody, including you, can open a backup.
- **Checks:** `python -m scripts.backup verify FILE` decrypts it, checks every file against the SHA-256 in its manifest
  and runs SQLite's integrity check, without restoring anything.
- **Restore** (`restore FILE`, with Keel stopped): the whole backup is checked first, in a staging folder; only then is
  the current database renamed `agent.before-restore-<stamp>.db` (kept, not deleted) and replaced. Notes are copied back
  only where no file exists, never overwriting one. A crafted backup can't write anywhere else: only `agent.db`,
  `manifest.json` and `notes/<relative path>` are accepted (no `..`, absolute paths, hidden names, links or devices),
  and a backup from a newer Keel (unknown database version) is refused. Restoring is CLI-only; the dashboard can list
  backups and take one, never restore.
- **Daily:** `BACKUP_DAILY=true` with the scheduler on takes one every day at `BACKUP_TIME`.

Put `BACKUP_DIR` on another drive or a synced folder (OneDrive, Google Drive, Dropbox): the provider only ever sees the
encrypted file. Without a passphrase, backups are plain database copies (the old format) and need the same care as the
database. Moving Keel to another computer is a restore: install Keel, copy `.env`, `restore FILE`, start Keel, and
reconnect Google (`python -m scripts.gmail_connect`).

**Encryption at rest: not built in, on purpose.** Encrypting the SQLite file (e.g. SQLCipher) means a native dependency
that breaks `pip install` on many machines, and a key that has to live somewhere: in `.env` next to the database it
protects, which adds little, or typed in on every start, which breaks the scheduler and Docker. Full-disk encryption
already protects the file against the realistic threat (a lost or stolen laptop) with none of those costs, so use it:
BitLocker (Windows), FileVault (macOS) or LUKS (Linux). Backups are different: they leave the machine, so they are
encrypted with their own passphrase (above).
