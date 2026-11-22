"""Connected services: what Keel is connected to, what each connection may do, and a live check of each.

Shared by the dashboard's Services tab and `scripts/smoke_integrations.py`. Nothing here ever returns a key, token,
password or the secret calendar address: only whether it is set. Every check is one read-only request; none sends a
message or writes anything. Failure messages are scrubbed of every configured secret before they leave this module.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.config import Settings, csv_list


class Skip(Exception):
    """The service isn't set up, so there is nothing to check."""


def _need(value: str | None, name: str) -> str:
    if not value or not str(value).strip():
        raise Skip(f"{name} not set")
    return str(value).strip()


def mask_email(addr: str | None) -> str | None:
    if not addr or "@" not in addr:
        return None
    user, _, domain = addr.strip().partition("@")
    return f"{user[:1]}•••@{domain}"


def secrets(s: Settings) -> list[str]:
    return [v for v in (s.anthropic_api_key, s.groq_api_key, s.brave_api_key, s.github_token, s.telegram_bot_token,
                        s.calendar_ics_url, s.telegram_chat_id, s.smtp_password, s.api_key, s.gmail_client_secret, s.gemini_api_key)
            if v and len(v) >= 4]


def scrub(message: str, s: Settings) -> str:
    for secret in secrets(s):
        message = message.replace(secret, "***")
    return message[:200]


# ---------- live checks: each returns a short detail, raises Skip when unconfigured, anything else is a failure

def check_brave(s: Settings) -> str:
    from app.integrations.websearch import BraveSearch

    results = BraveSearch(_need(s.brave_api_key, "BRAVE_API_KEY")).search("python programming language", count=1)
    return f"{len(results)} result(s)"


def check_github(s: Settings) -> str:
    from app.integrations.github import GitHubClient

    if not s.github_enabled:
        raise Skip("GITHUB_ENABLED=false")
    token = _need(s.github_token, "GITHUB_TOKEN")
    client = GitHubClient(token)
    if s.github_user:
        return f"{len(client.list_repos(s.github_user, limit=5))} repo(s) for GITHUB_USER"
    resp = client._get("/user")  # read-only: who does this token belong to
    return "token accepted (/user)" if resp.status_code == 200 else f"HTTP {resp.status_code}"


def check_notes(s: Settings) -> str:
    folder = Path(_need(s.notes_dir, "NOTES_DIR")).expanduser()
    if not folder.is_dir():
        raise RuntimeError("NOTES_DIR is set but is not a readable folder")
    files = [p for p in folder.rglob("*") if p.is_file()]
    return f"{len(files)} file(s) readable (not indexed)"


def check_calendar(s: Settings) -> str:
    from app.integrations.calendar import fetch_ics, parse_events

    tz = ZoneInfo(s.agent_timezone)
    text = fetch_ics(_need(s.calendar_ics_url, "CALENDAR_ICS_URL"))
    today = date.today()
    events = parse_events(text, today, today + timedelta(days=7), tz)
    return f"{len(events)} event(s) in the next 7 days"


def check_telegram(s: Settings) -> str:
    from app.integrations.notifier import TelegramNotifier

    notifier = TelegramNotifier(_need(s.telegram_bot_token, "TELEGRAM_BOT_TOKEN"), s.telegram_chat_id or "")
    bot = notifier.api("getMe").get("result", {})  # read-only; validates the token. Never sendMessage here.
    note = "" if s.telegram_chat_id else " (TELEGRAM_CHAT_ID not set, so notifications stay off)"
    return f"token valid, bot is_bot={bot.get('is_bot')}{note}"


def check_email(s: Settings, send: bool = False) -> str:
    from app.integrations.notifier import EmailNotifier, build_notifier

    _need(s.smtp_host, "SMTP_HOST")
    _need(s.email_to, "EMAIL_TO")
    notifier = build_notifier(s)
    if not isinstance(notifier, EmailNotifier):
        return "configured, but Telegram is set too and takes priority, so email is not used"
    to = mask_email(s.email_to)
    if send:
        notifier.send("Keel test email\nIf you can read this, reminders and check-ins will reach you by email.")
        return f"logged in and sent one test email to {to}"
    notifier.verify()
    return f"connected ({s.smtp_security}), logged in; nothing sent"


def check_gemini(s: Settings) -> str:
    from app.llm.gemini_client import list_models

    models = list_models(_need(s.gemini_api_key, "GEMINI_API_KEY"))
    if s.gemini_model not in models:
        near = [m for m in models if "flash" in m or "hydrogen" in m][:6]
        raise RuntimeError(f"key works, but there's no model {s.gemini_model}; try one of: {', '.join(near) or 'see AI Studio'}")
    return f"key works and {s.gemini_model} is available ({len(models)} models; nothing was sent)"


def check_model(s: Settings) -> str:
    """Free checks only: Ollama lists its models; Gemini lists the key's models. Never sends a prompt."""
    if "gemini" in (_provider(s), s.llm_fallback) and s.gemini_api_key:
        return check_gemini(s)
    if s.llm_provider != "ollama":
        raise Skip("only a local (Ollama) model or Gemini has a free check")
    import httpx

    from app.llm.ollama_client import check_base_url

    base = check_base_url(s.ollama_base_url)
    try:
        info = httpx.post(f"{base}/api/show", json={"model": s.ollama_model}, timeout=10)
    except httpx.HTTPError:
        raise RuntimeError(f"can't reach Ollama at {base}; start the Ollama app or run `ollama serve`") from None
    if info.status_code == 404:
        raise RuntimeError(f"{s.ollama_model} isn't on this computer; run `ollama pull {s.ollama_model}`")
    info.raise_for_status()
    caps = info.json().get("capabilities") or []
    if caps and "tools" not in caps:
        raise RuntimeError(f"{s.ollama_model} can't call tools, which Keel needs; pick a model that lists 'tools'")
    return f"{s.ollama_model} is installed and can call tools"


def gmail_connected(s: Settings) -> bool:
    from app.integrations.gmail import load_token

    return bool(s.gmail_client_id) and load_token(Path(s.gmail_token_path)) is not None


def check_gmail(s: Settings) -> str:
    from app.integrations.gmail import GmailClient

    _need(s.gmail_client_id, "GMAIL_CLIENT_ID")
    if not gmail_connected(s):
        raise Skip("not connected yet: run `python -m scripts.gmail_connect`")
    p = GmailClient(s.gmail_client_id, s.gmail_client_secret, Path(s.gmail_token_path)).profile()  # read-only
    return f"signed in as {mask_email(p.get('emailAddress'))}; {p.get('messagesTotal', '?')} messages"


def gcal_connected(s: Settings) -> bool:
    from app.integrations.google_auth import CALENDAR_SCOPE, GoogleAuth

    return gmail_connected(s) and GoogleAuth(s.gmail_client_id or "", None, Path(s.gmail_token_path)).has_scope(CALENDAR_SCOPE)


def check_google_calendar(s: Settings) -> str:
    from datetime import datetime, timedelta

    from app.integrations.gcal import GoogleCalendar
    from app.integrations.google_auth import GoogleAuth

    if not s.google_calendar_enabled:
        raise Skip("GOOGLE_CALENDAR_ENABLED is false")
    if not gcal_connected(s):
        raise Skip("calendar permission not granted yet: run `python -m scripts.gmail_connect` again")
    tz = ZoneInfo(s.agent_timezone)
    now = datetime.now(tz)
    cal = GoogleCalendar(GoogleAuth(s.gmail_client_id or "", s.gmail_client_secret, Path(s.gmail_token_path)), s.google_calendar_id)
    return f"{len(cal.events(now, now + timedelta(days=7)))} event(s) in the next 7 days (read only; nothing changed)"


def check_browser(s: Settings) -> str:
    """Starts Keel's browser and opens a blank page: no website is contacted."""
    if not s.browser_enabled:
        raise Skip("BROWSER_ENABLED is false")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright isn't installed: pip install playwright && python -m playwright install chromium") from None
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        version = b.version
        b.new_page().goto("about:blank")
        b.close()
    return f"Chromium {version} starts (no site was contacted)"


CHECKS: dict[str, Callable[[Settings], str]] = {
    "model": check_model,
    "browser": check_browser,
    "google_calendar": check_google_calendar,
    "gmail": check_gmail,
    "search": check_brave, "github": check_github, "notes": check_notes,
    "calendar": check_calendar, "telegram": check_telegram, "email": check_email,
}


def run_check(service: str, s: Settings, check: Callable[[Settings], str] | None = None) -> dict:
    """{"ok": True|False|None, "detail": str}. ok None = not set up. Never raises, never returns a secret."""
    fn = check or CHECKS[service]
    try:
        return {"ok": True, "detail": scrub(fn(s), s)}
    except Skip as why:
        return {"ok": None, "detail": str(why)}
    except Exception as exc:  # noqa: BLE001 - report the failure, scrubbed
        return {"ok": False, "detail": scrub(f"{type(exc).__name__}: {exc}", s)}


# ---------- what is connected

def _provider(s: Settings) -> str | None:
    from app.llm.factory import resolve_provider

    p = resolve_provider(s)
    if p in (None, "ollama"):
        return p
    return p if getattr(s, f"{p}_api_key") else None


def _model_label(s: Settings, provider: str) -> str:
    return {"ollama": f"Ollama on {_host(s.ollama_base_url) or 'this computer'} · {s.ollama_model}",
            "gemini": f"Google Gemini · {s.gemini_model}", "anthropic": f"Anthropic · {s.agent_model}",
            "groq": f"Groq · {s.agent_model}"}[provider]


GEMINI_DATA_NOTE = "Gemini free tier: Google says what you send is used to improve its products (not on the paid tier)"


def _host(url: str | None) -> str | None:
    try:
        return urlparse(url).hostname if url else None
    except ValueError:
        return None


def describe(s: Settings, indexed_documents: int | None = None) -> list[dict]:
    """One row per service: status ("connected" | "partial" | "not_set_up" | "off"), what it can do, and setup hints."""
    provider = _provider(s)
    telegram = bool(s.telegram_bot_token and s.telegram_chat_id)
    email = bool(s.smtp_host and s.email_to)
    channel = "telegram" if telegram else "email" if email else None
    rows = [
        {"id": "model", "name": "AI model", "status": "connected" if provider else "not_set_up",
         "details": ([_model_label(s, provider)] if provider else [])
                    + (["Local: chats stay on this computer"] if provider == "ollama" and _host(s.ollama_base_url) in ("127.0.0.1", "localhost", "::1") else [])
                    + ([f"When busy, falls back to {_model_label(s, s.llm_fallback)}"] if provider and s.llm_fallback not in ("none", provider) else [])
                    + ([GEMINI_DATA_NOTE] if "gemini" in (provider, s.llm_fallback) and s.gemini_api_key else []),
         "access": "Receives each chat message, the facts Keel looked up to answer it, and the tool list.",
         "setup": "ANTHROPIC_API_KEY, GROQ_API_KEY or GEMINI_API_KEY, or LLM_PROVIDER=ollama for a model on this computer; LLM_FALLBACK for a second one"},
        {"id": "telegram", "name": "Telegram", "status": "connected" if telegram else "partial" if s.telegram_bot_token else "not_set_up",
         "details": ([("Delivers your reminders and check-ins" if channel == "telegram" else "")]
                     + (["You can chat with Keel through the bot (only from your chat)"] if telegram and s.telegram_inbound else [])
                     + (["TELEGRAM_CHAT_ID is missing, so nothing is sent"] if s.telegram_bot_token and not s.telegram_chat_id else [])),
         "access": "Sends messages to your one chat; reads only messages from that chat.",
         "setup": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"},
        {"id": "email", "name": "Email", "status": "connected" if email else "partial" if s.smtp_host or s.email_to else "not_set_up",
         "details": ([f"{s.smtp_host} ({s.smtp_security})", f"Sends to {mask_email(s.email_to)}"] if email else [])
                    + (["Not used: Telegram is set up and takes priority"] if email and telegram else [])
                    + (["Delivers your reminders and check-ins"] if channel == "email" else []),
         "access": "Sends plain-text mail to your own address only. Keel never reads your inbox.",
         "setup": "SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD and EMAIL_TO"},
        {"id": "gmail", "name": "Gmail", "status": "not_set_up" if not s.gmail_client_id else "connected" if gmail_connected(s) else "partial",
         "details": ([] if not s.gmail_client_id else
                     (["Reads and searches your mail; saves drafts",
                       f"Sends only after you approve each email (at most {s.gmail_max_sends_per_day} a day)" if s.gmail_send_enabled
                       else "Sending is off (GMAIL_SEND_ENABLED=false)"]
                      if gmail_connected(s) else ["Not signed in yet: run python -m scripts.gmail_connect"])),
         "access": "Read your mail; create drafts; send only what you approve. Can't delete mail or change labels or filters. "
                   "Emails are treated as untrusted text.",
         "setup": "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET, then python -m scripts.gmail_connect"},
        {"id": "google_calendar", "name": "Google Calendar",
         "status": "off" if not s.google_calendar_enabled else "connected" if gcal_connected(s) else "partial",
         "details": ([] if not s.google_calendar_enabled else
                     ["Reads events; adds, moves and deletes them only after you approve each change", "Never emails guests"]
                     if gcal_connected(s) else
                     ["Not allowed yet: run python -m scripts.gmail_connect again and tick the calendar permission"]),
         "access": "Events in one calendar (GOOGLE_CALENDAR_ID). Not calendar settings or sharing. Event text is treated as untrusted.",
         "setup": "GOOGLE_CALENDAR_ENABLED=true (with Gmail's Google client), then python -m scripts.gmail_connect"},
        {"id": "calendar", "name": "Calendar (iCal, read-only)", "status": "connected" if s.calendar_ics_url else "not_set_up",
         "details": [f"iCal feed from {_host(s.calendar_ics_url) or 'an unknown host'}"] if s.calendar_ics_url else [],
         "access": "Read-only. Keel never changes your calendar; plans download as a file you import yourself.",
         "setup": "CALENDAR_ICS_URL (your calendar's secret iCal address)"},
        {"id": "github", "name": "GitHub", "status": "off" if not s.github_enabled else "connected",
         "details": [] if not s.github_enabled else
                    [("Token set: private repos it can read" if s.github_token else "No token: public repos only, lower rate limit")]
                    + ([f"Default account: {s.github_user}"] if s.github_user else []),
         "access": "Read-only: repos, issues, pull requests and commits.",
         "setup": "GITHUB_TOKEN (read-only scope) and GITHUB_USER; GITHUB_ENABLED=false turns it off"},
        {"id": "search", "name": "Web search (Brave)", "status": "connected" if s.brave_api_key else "not_set_up",
         "details": [], "access": "Sends your search query to Brave. Results are treated as untrusted.",
         "setup": "BRAVE_API_KEY"},
        {"id": "web", "name": "Reading web pages", "status": "connected" if s.web_fetch_enabled else "off",
         "details": ([f"Only {len(csv_list(s.web_allowed_domains))} allowed site(s)"] if csv_list(s.web_allowed_domains) else ["Any public site"])
                    + ([f"{len(csv_list(s.web_blocked_domains))} blocked site(s)"] if csv_list(s.web_blocked_domains) else [])
                    if s.web_fetch_enabled else [],
         "access": "Fetches a page you or a search result named. Never private network addresses. Pages are untrusted.",
         "setup": "WEB_FETCH_ENABLED, WEB_ALLOWED_DOMAINS, WEB_BLOCKED_DOMAINS"},
        {"id": "browser", "name": "Keel's browser", "status": "connected" if s.browser_enabled else "off",
         "details": (["Its own Chromium with a fresh private profile each start; never your browser",
                      "Window visible (you can log in to sites in it)" if not s.browser_headless else "Runs hidden (BROWSER_HEADLESS=true)"]
                     if s.browser_enabled else []),
         "access": "Opens sites, clicks links, fills forms and downloads documents. Typing into a page and submitting wait for "
                   "your approval; it never types passwords, payment or ID numbers, or solves CAPTCHAs. No private network addresses.",
         "setup": "BROWSER_ENABLED=true, then: pip install playwright && python -m playwright install chromium"},
        {"id": "notes", "name": "Notes folder", "status": "connected" if s.notes_dir else "not_set_up",
         "details": ([f"Folder “{Path(s.notes_dir).expanduser().name}”"] if s.notes_dir else [])
                    + ([f"{indexed_documents} document(s) indexed"] if s.notes_dir and indexed_documents is not None else []),
         "access": "Reads the files in that folder; dashboard uploads go into its uploads/ subfolder only.",
         "setup": "NOTES_DIR"},
        {"id": "scheduler", "name": "Background scheduler", "status": "connected" if s.scheduler_enabled else "off",
         "details": [f"Checks every {s.scheduler_tick_seconds} s; quiet hours {s.quiet_hours_start}-{s.quiet_hours_end}; "
                     f"at most {s.notification_max_per_day} messages a day"] if s.scheduler_enabled else
                    ["Reminders, check-ins and workflows don't run on their own"],
         "access": "Runs inside Keel. Sends only through the channel above, within the daily budget and quiet hours.",
         "setup": "SCHEDULER_ENABLED=true"},
    ]
    for r in rows:
        r["details"] = [d for d in r["details"] if d]
        r["testable"] = r["id"] in CHECKS and r["status"] in ("connected", "partial")
        if r["id"] == "github":
            r["testable"] = r["testable"] and bool(s.github_token)  # the check needs a token; public access needs none
        if r["id"] == "model":
            r["testable"] = provider == "ollama" or ("gemini" in (provider, s.llm_fallback) and bool(s.gemini_api_key))  # others would spend tokens
    return rows
