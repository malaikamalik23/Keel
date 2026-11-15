"""Smoke-test the live integrations with YOUR keys: one read-only call each. Never sends anything, never writes.

    python -m scripts.smoke_integrations

For each integration: if its setting is missing -> SKIPPED(no key); otherwise one read-only request -> PASS / FAIL.
Telegram is only checked with getMe (validates the bot token); no message is ever sent. Notes are only listed on disk
(nothing is indexed into the database). Key values are never printed: every message is also scrubbed of them.
Exit code is 1 if anything FAILED, else 0.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings

Check = Callable[[Settings], str]  # returns a short detail on success; raises Skip if unconfigured, anything else = FAIL


class Skip(Exception):
    pass


def _need(value: str | None, name: str) -> str:
    if not value or not str(value).strip():
        raise Skip(f"{name} not set")
    return str(value).strip()


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


CHECKS: list[tuple[str, Check]] = [
    ("Brave search", check_brave),
    ("GitHub", check_github),
    ("Notes folder", check_notes),
    ("Calendar", check_calendar),
    ("Telegram", check_telegram),
]


def _scrub(message: str, s: Settings) -> str:
    for secret in (s.brave_api_key, s.github_token, s.telegram_bot_token, s.calendar_ics_url, s.telegram_chat_id):
        if secret and len(secret) >= 4:
            message = message.replace(secret, "***")
    return message[:200]


def run(settings: Settings | None = None, checks: list[tuple[str, Check]] | None = None) -> list[tuple[str, str, str]]:
    s = settings or Settings()
    rows = []
    for name, check in checks or CHECKS:
        try:
            rows.append((name, "PASS", _scrub(check(s), s)))
        except Skip as why:
            rows.append((name, "SKIPPED(no key)", str(why)))
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            rows.append((name, "FAIL", _scrub(f"{type(exc).__name__}: {exc}", s)))
    return rows


def main() -> int:
    rows = run()
    width = max(len(n) for n, _, _ in rows)
    for name, status, detail in rows:
        print(f"{name:<{width}}  {status:<16} {detail}")
    return 1 if any(status == "FAIL" for _, status, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
