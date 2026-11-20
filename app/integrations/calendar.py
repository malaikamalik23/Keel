"""Read-only calendar access through the calendar's secret iCal (.ics) address -- no OAuth needed.

Event text is written by other people (anyone can send you an invite), so the tool that exposes it is
marked `returns_untrusted`: output is fenced and the session is tainted.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

MAX_ICS_BYTES = 5_000_000
MAX_EVENTS = 50


class CalendarError(Exception):
    pass


def fetch_ics(url: str, client: httpx.Client | None = None) -> str:
    if not url.lower().startswith("https://"):
        raise CalendarError("Calendar URL must be https.")
    try:
        resp = (client or httpx.Client(timeout=10, follow_redirects=False)).get(url)
    except httpx.HTTPError as exc:
        raise CalendarError(f"Could not fetch the calendar ({type(exc).__name__}).") from None
    if resp.status_code != 200:
        raise CalendarError(f"Calendar server returned HTTP {resp.status_code}.")
    if len(resp.content) > MAX_ICS_BYTES:
        raise CalendarError("Calendar file is too large.")
    return resp.text


def _as_local(value, tz: ZoneInfo, *, end_of_day: bool = False) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        return (value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)), False
    return datetime.combine(value, time.min, tzinfo=tz), True  # all-day event


def parse_events(ics_text: str, start: date, end: date, tz: ZoneInfo) -> list[dict]:
    """Events overlapping [start, end] (inclusive, local dates), recurring events expanded."""
    import icalendar
    import recurring_ical_events

    try:
        cal = icalendar.Calendar.from_ical(ics_text)
        occurrences = recurring_ical_events.of(cal).between(
            datetime.combine(start, time.min, tzinfo=tz), datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
        )
    except Exception as exc:  # noqa: BLE001 - malformed third-party input
        raise CalendarError(f"Could not parse the calendar ({type(exc).__name__}).") from None

    events = []
    for ev in occurrences:
        s, all_day = _as_local(ev.get("DTSTART").dt, tz)
        raw_end = ev.get("DTEND").dt if ev.get("DTEND") else None
        e = _as_local(raw_end, tz)[0] if raw_end is not None else s + (timedelta(days=1) if all_day else timedelta())
        events.append({
            "title": str(ev.get("SUMMARY", "(no title)"))[:120],
            "start": s.isoformat(timespec="minutes"), "end": e.isoformat(timespec="minutes"),
            "all_day": all_day, "minutes": 0 if all_day else int((e - s).total_seconds() // 60),
            "location": str(ev.get("LOCATION", ""))[:80] or None,
            # The calendar's own id for the event: how a task stays linked to it, even if it moves.
            "uid": " ".join(str(ev.get("UID", "")).split())[:255] or None,
        })
    events.sort(key=lambda x: x["start"])
    return events[:MAX_EVENTS]
