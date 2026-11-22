# Roadmap and known limitations

Keel is a **single-user, self-hosted** assistant. It is not a multi-user product and should not be deployed as one:
there are no accounts, one optional API key protects the whole API, and the scheduler runs inside the one process.
What is verified for real and what is only tested with mocks is in [STATUS.md](STATUS.md).

## Next

1. **Run the release checklist's guided steps for real** ([RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)): a Gmail
   draft and an approved send, a Google Calendar change, the browser on a real portal (stopping before submit), and
   Telegram over several days. The automated local and read-only parts already pass.
2. **An independent security review.** The adversarial suites (attachments, invitations, hidden page text, OAuth
   expiry and revocation, concurrent approvals, interrupted workflows) were written alongside the defences they test;
   someone who didn't build Keel should try to break it.
3. **A broader benchmark.** Beyond "right tool, right arguments": did the task actually happen, did it stay safe, did
   it retrieve the right memory, how long and how many tokens. New cases for follow-ups ("do that", "yes"), several
   actions in one message, considering vs decided vs promised vs done, and Urdu / Roman Urdu memory lookups.
4. **Easier setup.** A first-run setup page for the model key and timezone, guided Gmail and Calendar connection,
   clearer messages when a service disconnects.
5. **Phone.** An installable dashboard (PWA), tested on Android and iOS browsers, and a documented private way to
   reach it from your phone (a VPN such as Tailscale, or the server guide in [DEPLOY.md](DEPLOY.md)).
6. **v1.1 release** with release notes, issue templates, and a short demo video.

## Known limitations

| Area | Limitation |
|---|---|
| Users | Single user. One API key; no accounts or per-user data isolation. |
| Data at rest | The SQLite database is not encrypted by Keel; use full-disk encryption. Backups are encrypted when `BACKUP_PASSPHRASE` is set. |
| Process | One process: the scheduler and rate limiter are in-process. Run one Keel per database. |
| Model routing | Real-model routing is 94-98% on Groq and varies by a few cases between runs; one uninterrupted run of all cases is still to do. Small local models are slower and miss more. |
| Taint | Coarse and sticky: after a conversation reads external text, every change in it needs approval until you start a new chat. |
| Memory search | Word-based (no embeddings). Fine for hundreds of memories; a differently-worded question can miss. |
| Notes search | SQLite FTS5 only; Postgres would need `tsvector`. Scanned PDFs without a text layer can't be read. |
| Browser | Chromium only; it never solves CAPTCHAs or types passwords, payment or ID numbers, so many portals need you to log in or finish yourself. |
| Gmail | Your own Google Cloud project in Testing mode signs you out every 7 days unless you publish it (unverified, for your use only). |
| Calendars | The iCal feed is read-only; changes need Google Calendar. Outlook works through its iCal feed only. |
| Telegram | Not yet tried with a real bot. |
| Fetcher and browser | Not a firewall: the browser's address check can in principle be raced by DNS rebinding; on a server, also block private ranges with an egress firewall. |
| Hidden text | Detected by rules (styles, attributes, rendering); a page can still hide text in ways no rule recognises. |
| Phone | Usable in a phone browser; not installable yet and not tested on real phones. |
