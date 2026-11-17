# Install and troubleshooting

A complete first run on your own computer, then the problems people actually hit and how to fix them.
To run Keel on a server instead, see **[DEPLOY.md](DEPLOY.md)**.

## What you need

| | |
|---|---|
| Python | 3.11 or 3.12 |
| Node | 20 or newer, only to build the dashboard once |
| An LLM key | [Anthropic](https://console.anthropic.com/) **or** [Groq](https://console.groq.com/keys) (Groq has a free tier) |
| Disk | under 1 GB including the virtual environment |

Everything else (Telegram, web search, calendar, GitHub, a notes folder) is optional and can be added later.

## 1. Get the code and install

**macOS / Linux**

```bash
git clone https://github.com/malaikamalik23/Keel.git && cd Keel
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/malaikamalik23/Keel.git; cd Keel
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env
```

## 2. Configure

Open `.env` and set three things:

```ini
GROQ_API_KEY=...                 # or ANTHROPIC_API_KEY=...
AGENT_MODEL=openai/gpt-oss-120b  # must belong to the provider whose key you set (Anthropic: claude-sonnet-5)
AGENT_TIMEZONE=Asia/Karachi      # your IANA timezone
```

On Groq's free tier also set `HISTORY_MESSAGE_LIMIT=12`. Every other option is described in `.env.example`.
`.env` is in `.gitignore`: never commit it.

## 3. Check the install

```bash
pytest -q
```

About 465 tests should pass in a few minutes. They need no key and no network, so a failure here is an install problem,
not a provider problem.

## 4. Build the dashboard and start

```bash
cd frontend && npm ci && npm run build && cd ..
uvicorn app.api.main:create_app --factory
```

Open **http://127.0.0.1:8000**. The database is created at `data/agent.db` on first start, and upgraded automatically
after every update.

Prefer the terminal? `python -m app.cli` gives you the same agent without the dashboard (`/help` lists its commands).

## 5. First five minutes

```
I want to get good at web penetration testing
Plan my day, I have 4 hours
I'll finish the GRE quant set by Friday
delete the literature review task
```

The last one does **not** delete anything: it appears under **Approvals** for you to confirm. That is the point of
Keel. A longer tour is in [DEMO.md](DEMO.md).

## 6. Optional extras

| You want | Set in `.env` |
|---|---|
| Reminders, morning and evening check-ins | `SCHEDULER_ENABLED=true` (run only one Keel process) |
| Messages on your phone | `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (also lets you chat from your phone) |
| Reminders by email instead | `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` and `EMAIL_TO` (see below) |
| Web search | `BRAVE_API_KEY` |
| Search over your notes and papers | `NOTES_DIR=/path/to/folder` |
| Your calendar (read-only) | `CALENDAR_ICS_URL` |
| Your GitHub repos (read-only) | `GITHUB_USER`, and `GITHUB_TOKEN` for private repos |

Check the ones you configured with one read-only call each (it never prints keys):

```bash
python -m scripts.smoke_integrations
```

### Reminders and check-ins by email

If Telegram is not an option (it is blocked on some networks), Keel can email them to you instead. It only ever sends
to the one address in `EMAIL_TO`, over an encrypted connection, as plain text. With Gmail:

1. Turn on 2-Step Verification for your Google account, then create an **App Password** (Google Account > Security >
   2-Step Verification > App passwords). Use that, not your normal password.
2. In `.env`:

   ```ini
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=you@gmail.com
   SMTP_PASSWORD=the-16-character-app-password
   EMAIL_TO=you@gmail.com
   SCHEDULER_ENABLED=true
   ```

3. Check it: `python -m scripts.smoke_integrations` logs in without sending; add `--send-test-email` to receive one.

Email is one-way: you get reminders and check-ins, and reply in the dashboard or the CLI. If Telegram is also
configured, Telegram is used instead. The same daily budget, quiet hours and de-duplication apply to both.

## 7. Docker instead

```bash
cp .env.example .env     # set an LLM key, AGENT_MODEL, AGENT_TIMEZONE and API_KEY
docker compose up --build
```

Open http://127.0.0.1:8000 and enter the `API_KEY` when the dashboard asks for it.

## Updating

```bash
git pull
pip install -r requirements-dev.txt
cd frontend && npm ci && npm run build && cd ..
```

Restart Keel. With Docker: `git pull && docker compose up -d --build`. Take a backup first: `python -m scripts.backup`.

---

## Troubleshooting

### Starting up

**PowerShell says running scripts is disabled when activating the venv.**
Run `Set-ExecutionPolicy -Scope Process RemoteSigned` in that window and activate again, or skip activation and call
the venv directly: `.venv\Scripts\python -m pytest -q`.

**`python` is not found, or is Python 3.10 or older.**
Install 3.11 or 3.12 and create the venv with it: `py -3.12 -m venv .venv` on Windows, `python3.12 -m venv .venv` elsewhere.

**http://127.0.0.1:8000 shows `{"detail":"Not Found"}`.**
The dashboard has not been built. Run step 4's `npm ci && npm run build` and restart. The API itself is fine:
http://127.0.0.1:8000/health answers `{"status":"ok"}`.

**`address already in use`.**
Something else has port 8000. Start on another port: `uvicorn app.api.main:create_app --factory --port 8010`.

**`docker compose up` stops with "Set API_KEY in .env".**
Deliberate: the container refuses to run without a key. Put a long random value in `.env`:
`python -c "import secrets; print('API_KEY=' + secrets.token_urlsafe(32))"`.

### 403 and 401

**403 "No API_KEY is set, so the API only accepts requests from this machine".**
You opened Keel from another device, through a reverse proxy, or started it with `--host 0.0.0.0`. Without a key Keel
answers only the machine it runs on. Set `API_KEY` in `.env` and restart (see [DEPLOY.md](DEPLOY.md)).

**403 "unexpected Host header" or "cross-site request".**
Without a key, Keel answers only `http://127.0.0.1:<port>` and `http://localhost:<port>`, and only requests its own
dashboard or local tools send. Open it by one of those two addresses. A custom host name (`keel.local`, a LAN IP)
needs `API_KEY`. This check is what stops a website open in your browser from using your local Keel.

**The dashboard asks for an API key.**
`API_KEY` is set. Enter the same value; it is kept for that browser tab only.

### The model

**"Rate limit" or 429 errors on Groq.**
Two different limits on the free tier:
- *Tokens per minute.* Keep `TOOL_PROFILE=lean` and `HISTORY_MESSAGE_LIMIT=12`, and wait a minute.
- *Tokens per day.* It frees up gradually over the following hours; nothing in Keel can speed that up. Long sessions
  and the routing eval (`evals/`, 160 calls) are the usual cause.

**The provider says the model does not exist, or does not support tools.**
`AGENT_MODEL` must be a model of the provider whose key is set, and it must support tool calling. The default
`claude-sonnet-5` only works with an Anthropic key. On Groq use `openai/gpt-oss-120b`.

**Both keys are set and the wrong provider is used.**
Set `LLM_PROVIDER=anthropic` or `LLM_PROVIDER=groq` explicitly.

**"Today" or reminder times are hours off.**
`AGENT_TIMEZONE` is still `UTC`. Set your IANA zone (for example `Asia/Karachi`) and restart.

### Behaviour that looks like a bug but is not

**Everything suddenly needs approval.**
The conversation read something from outside (a web page, a note, a GitHub issue, your calendar). From then on every
change in *that conversation* waits for your approval, because Keel can no longer tell your wishes from text on the
page. Start a new chat (the **New** button in Chat, or `/new` in the CLI) for a clean one. See [SECURITY.md](../SECURITY.md).

**Opening a link from a page asks for approval.**
After Keel has read something external, it opens only URLs you typed and search results without asking. A link it
found inside a page waits under **Approvals**, or paste the URL into the chat yourself.

**A delete did nothing.**
Deletes always wait under **Approvals** (CLI: `/pending`, then `/confirm <id>`). Unanswered requests expire after
24 hours.

**An approval says "the target changed since this request was queued".**
The item was edited or replaced after the request was made, so Keel refused to act on the wrong thing. Ask again.

### Optional features

**Reminders and check-ins never arrive.**
`SCHEDULER_ENABLED=true` must be set and Keel must be running at that time. Without Telegram they are stored and
shown in the dashboard's Activity tab rather than pushed to you. Quiet hours and the daily budget also hold messages back.

**Every check-in arrives twice.**
Two schedulers are running on the same database (two server processes, or the server plus `python -m app.scheduler`).
Run one.

**Emails do not arrive.**
Run `python -m scripts.smoke_integrations`. "rejected the username or password" with Gmail means you need an App
Password (step above), not your account password. Look in Spam once and mark the first message as not spam. If
Telegram variables are also set, Telegram is used instead of email.

**Telegram stays silent.**
Send your bot `/start` once (a bot cannot message you first), and check that `TELEGRAM_CHAT_ID` is your *numeric* id
(ask `@userinfobot`). Use your private chat, not a group. Keel ignores every other chat without replying.

**`notes_search` finds nothing.**
`NOTES_DIR` must point at a folder that contains `.md`, `.txt`, `.rst`, `.tex`, `.org` or `.pdf` files. The folder is
scanned at startup; after adding files, restart or type `/index` in the CLI. Scanned PDFs without a text layer cannot be read.

**The agent says it cannot search the web.**
`web_search` only exists when `BRAVE_API_KEY` is set. Reading a URL you paste works without it.

**The end-to-end tests are skipped.**
They need a browser: `python -m playwright install chromium`, and the dashboard must be built.

### Still stuck

Run `pytest -q` and `python -m scripts.smoke_integrations`, then open an issue with the output (it contains no keys).
For anything that looks like a security problem, use the private route in [SECURITY.md](../SECURITY.md) instead.
