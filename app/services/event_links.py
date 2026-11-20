"""Tasks linked to a calendar event: find the event, and keep the task in step when the event moves or is cancelled.

An event is identified by its calendar UID and its start time (a repeating meeting has one UID for every occurrence;
the start picks the one). Titles are never used: they are written by whoever sent the invite.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task
from app.services.task_state import OPEN

logger = logging.getLogger(__name__)
SEARCH_BACK, SEARCH_AHEAD = timedelta(days=14), timedelta(days=90)


def to_utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def from_utc_naive(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=UTC).astimezone(tz)


def find_occurrence(source: Callable[[], str], tz: ZoneInfo, uid: str, near: datetime) -> datetime | None:
    """Start of the occurrence of event `uid` closest to `near` (local), or None if the calendar has no such event."""
    from app.integrations.calendar import parse_events

    events = parse_events(source(), (near - SEARCH_BACK).date(), (near + SEARCH_AHEAD).date(), tz)
    starts = [datetime.fromisoformat(e["start"]) for e in events if e.get("uid") == uid and not e["all_day"]]
    return min(starts, key=lambda s: abs(s - near)) if starts else None


def sync_links(db: Session, source: Callable[[], str] | None, tz: ZoneInfo) -> list[dict]:
    """Follow linked events: a moved event moves the task's due date; a vanished one is reported. Returns the changes.
    A calendar that can't be read changes nothing."""
    if source is None:
        return []
    changes = []
    linked = list(db.scalars(select(Task).where(Task.status.in_(OPEN), Task.event_uid.isnot(None), Task.event_start.isnot(None))))
    for t in linked:
        was = from_utc_naive(t.event_start, tz)
        try:
            now_at = find_occurrence(source, tz, t.event_uid, was)
        except Exception:  # noqa: BLE001 - an unreadable calendar is not a cancelled meeting
            logger.warning("Calendar could not be read while checking linked events")
            return changes
        if now_at is None:
            changes.append({"task_id": t.id, "title": t.title, "change": "cancelled", "was": was.isoformat(timespec="minutes")})
            t.event_uid, t.event_start = None, None
        elif now_at != was:
            # Not recorded as a postponement: the meeting moved, the user didn't put anything off.
            t.event_start, t.due_date = to_utc_naive(now_at), now_at.date()
            changes.append({"task_id": t.id, "title": t.title, "change": "moved", "was": was.isoformat(timespec="minutes"),
                            "now": now_at.isoformat(timespec="minutes")})
    if changes:
        db.flush()
    return changes
