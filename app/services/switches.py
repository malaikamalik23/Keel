"""Per-integration switches: turn an integration off from the dashboard without editing .env or restarting.

An integration that is off is off everywhere:
- its tools are not offered to the model (so it doesn't plan around them),
- the executor refuses them for the agent, for a workflow step and for an approval waiting in the queue (an email
  queued before you switched Gmail off is not sent when approved later),
- multi-source tools skip that source (find_opportunities won't search Gmail while Gmail is off),
- "messages" off stops reminders, check-ins and nudges from being sent. Check-ins and nudges missed meanwhile are not
  sent later; a reminder that falls due is sent if you switch back on within a day, else dropped as stale.

Switches only ever take capabilities away: an integration that isn't configured in .env can't be switched on here.
They are stored in the database (`user_settings`), so they survive restarts and are part of backups.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserSetting, utcnow

# id -> label. The ids match the Services tab rows (app/services/connections.py), plus "messages".
SWITCHES: dict[str, str] = {
    "gmail": "Gmail",
    "google_calendar": "Google Calendar",
    "browser": "Keel's browser",
    "web": "Reading web pages",
    "github": "GitHub",
    "notes": "Notes folder",
    "messages": "Reminders and check-ins by email",
}
PREFIX = "switch:"


def integration_of(tool_name: str) -> str | None:
    """Which switch governs a tool (None = always on: Keel's own data)."""
    from app.tools.groups import group_of

    for prefix, switch in (("gmail_", "gmail"), ("gcal_", "google_calendar"), ("browser_", "browser"), ("github_", "github")):
        if tool_name.startswith(prefix):
            return switch
    if tool_name == "web_fetch":
        return "web"
    if tool_name == "application_fill_plan":
        return "browser"
    if group_of(tool_name) == "notes":
        return "notes"
    return None


def off_switches(db: Session) -> set[str]:
    rows = db.scalars(select(UserSetting).where(UserSetting.key.like(f"{PREFIX}%"))).all()
    return {r.key.removeprefix(PREFIX) for r in rows if r.value is False}


def is_on(db: Session, switch: str) -> bool:
    return switch not in off_switches(db)


def set_switch(db: Session, switch: str, on: bool) -> None:
    if switch not in SWITCHES:
        raise ValueError(f"Unknown switch '{switch}'.")
    row = db.get(UserSetting, PREFIX + switch)
    if row is None:
        db.add(UserSetting(key=PREFIX + switch, value=on, updated_at=utcnow()))
    else:
        row.value, row.updated_at = on, utcnow()
    db.commit()


def hidden_tools(db: Session, names) -> set[str]:
    off = off_switches(db)
    return {n for n in names if integration_of(n) in off} if off else set()


def refusal(tool_name: str) -> str:
    return f"{SWITCHES[integration_of(tool_name)]} is switched off on the Services tab, so '{tool_name}' can't run. Switch it back on there to use it."
