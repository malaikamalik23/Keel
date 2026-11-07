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
proxy in front (the app itself speaks plain HTTP) and a strong `API_KEY`.

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
   them; only you (dashboard, CLI or REST) can approve. There is no tool that approves, rejects or edits the queue.
2. **Taint tracking.** When any tool returns external text, the session is *tainted*. While it is, every WRITE tool also
   needs your approval. Taint lasts as long as that content stays in the model's replayed history window.
3. **Untrusted fencing.** External text is wrapped in `<untrusted_content>` tags. The closing tag is neutralised
   (case/whitespace-insensitive) so content cannot break out, and the system prompt says the contents are data.
4. **Invisible-text stripping.** Unicode format/private-use/control characters (including the "tag" block used for
   ASCII smuggling, zero-width characters and bidi overrides) are removed from all external text. Text hidden from humans in
   HTML (`display:none`, `hidden`, `aria-hidden`, `<script>`, comments…) never reaches the model.
5. **Exfiltration gate.** In a tainted session `web_fetch` may only fetch URLs the *user typed* or that appeared inside
   fenced results. A URL the model assembled itself (the way data gets smuggled out in a query string) is queued for
   approval. URLs the model wrote, and the text of queued/error results, never count as "seen", so a retry can't slip through.
6. **SSRF protection.** `web_fetch` allows only http(s) on ports 80/443, no credentials in URLs, and only hosts that
   resolve to **public** addresses (loopback, private, link-local, cloud-metadata, multicast, reserved and IPv4-mapped
   IPv6 are refused; decimal/octal IP tricks are caught because the resolved address is checked). Redirects are followed manually,
   max 3, and **each hop is re-validated**. Responses are capped at 1 MB and limited to text types.
   `WEB_ALLOWED_DOMAINS` can restrict it to a short list.
7. **Notes confinement.** Only files physically inside `NOTES_DIR` are indexed (symlinks that point outside are skipped).
   `read_note` serves text from the index, never from disk, so there is no path to traverse.
8. **Secrets stay out of messages.** Errors from Telegram, Brave and GitHub clients never include URLs or headers
   (the Telegram bot token lives in the URL, API keys in headers). Tokens are never logged or returned to the model.
9. **Plain-text output.** The dashboard renders model output as text (no HTML/Markdown), and Telegram messages are sent
   without `parse_mode`, so a Markdown image/link in a reply is not an exfiltration channel.
10. **API hardening.** Optional `X-API-Key` (constant-time compare), a per-client rate limit on LLM-calling endpoints,
    security headers (CSP `default-src 'self'`, `frame-ancestors 'none'`, `nosniff`), `Cache-Control: no-store` on API
    responses. Docker: non-root, read-only root filesystem, all capabilities dropped, published on `127.0.0.1` only.
11. **Audit log.** Every tool call (allowed, queued, rejected, failed, expired) is recorded with actor and parameters.
12. **Telegram.** Only the single configured chat id is ever answered; everything else is ignored silently. Approving
    actions is deliberately **not** possible from Telegram.

Tool *advertising* is not part of this model: in the default lean profile each request lists only some tools, but the executor applies every rule above to any tool the model tries to call, advertised or not (there is a test for that).

## How this is tested

`tests/injection/` runs on every push. It builds a world where **web pages, search results, GitHub READMEs and issues,
notes and calendar events all contain attack text** (7 payload styles: plain override, fence breakout, fake tool JSON,
fake user consent, Urdu/Roman Urdu, invisible Unicode, Markdown-image exfiltration), then drives a *fully compromised*
model that tries 17 harmful calls: deletes, writes, memory poisoning, exfiltration URLs, requests to `127.0.0.1` and
cloud metadata. It asserts that nothing executed, the database is byte-for-byte unchanged, no forbidden request was
made, and the audit log shows only queued or refused attempts.

The suite was **mutation-checked**: each defence (taint escalation, HIGH_IMPACT tier, exfiltration gate, fence
neutralisation, invisible-character stripping, hidden-HTML removal, SSRF check, untrusted flag) was deliberately
broken, and the suite failed every time. A test that can't fail proves nothing; these can.

## Known limitations (read these)

- **Taint is coarse and sticky.** Any untrusted content in the replay window makes *all* writes need approval, even
  ones you asked for in plain words. Correct but sometimes annoying; it clears when the content ages out of the window.
- **DNS rebinding.** The fetcher validates the resolved address, then httpx resolves again. A hostile DNS server could
  answer differently the second time. For a hard guarantee, run Keel behind an egress firewall/network policy that blocks
  private ranges (and consider `WEB_ALLOWED_DOMAINS`).
- **Approved ≠ safe.** The gate puts a human in the loop; it can't judge the action for you. Read what you approve,
  especially approval requests that appear right after reading external content.
- **Memory poisoning needs your approval, but a tricked user is still a risk.** In a tainted session `save_memory` is
  queued; if you approve a malicious memory, it persists and is shown to the model in future context.
- **Visible-text injection still reaches the model** (that is the point of reading a page). The defences limit what it
  can *do* with that text; they don't make the model immune to being misled in its *answers*. Verify important claims.
- **Information disclosure through the model's reply.** The model can still quote what it has read (including your
  memories) in its answer to you. That goes to you, not to the attacker, unless a channel like a rendered image exists (see 9).
- **Single shared API key, no TLS, no per-user auth.** See scope.
- **Prompts and tool descriptions are sent to your LLM provider**, along with tool results and your data that the agent
  reads. Choose a provider you trust with that data.
- **Not independently audited.** Treat this as careful hobby-grade engineering with a strong test suite, not a certified product.
