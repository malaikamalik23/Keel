"""Export the day's time-blocked plan as an .ics file the user can import into Google Calendar (or any calendar).

Keel itself never writes to the user's calendar; importing the file is the user's own action. Titles come from the
user's own tasks, but are still escaped: a title must not be able to start a new line of the file (and so add fields
or events of its own).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.db.models import Plan
from app.security.untrusted import sanitize_text


def _escape(text: str) -> str:
    text = " ".join(sanitize_text(text).split())  # one line, no control or invisible characters
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")[:200]


def _utc(day: date, hhmm: str, tz: ZoneInfo) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def plan_to_ics(plan: Plan | None, day: date, tz: ZoneInfo, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Keel//Daily plan//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for item in (plan.items if plan else []):
        if not item.start_time or not item.end_time or item.status == "skipped":
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:keel-plan-{day.isoformat()}-{item.id}@keel.local",  # stable: importing again updates, not duplicates
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(day, item.start_time, tz)}",
            f"DTEND:{_utc(day, item.end_time, tz)}",
            f"SUMMARY:{_escape(item.title)}",
            "DESCRIPTION:Planned by Keel",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
