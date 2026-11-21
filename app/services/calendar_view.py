"""The dashboard calendar: your calendar's events and Keel's own dates side by side, a day at a time.

Read-only. Keel never writes to your calendar (plans download as a file you import yourself). Event titles come from
whoever sent the invite, so they are cleaned (invisible characters and links removed) and only ever shown in the
dashboard as plain text; nothing here goes to the model.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Commitment, ImportantDate, Opportunity, Plan, Project, Reminder, Task
from app.services.briefing import _clean_title
from app.services.scheduling import Interval, free_slots, merge
from app.services.task_state import OPEN, actionable
from app.services.timeutil import local_to_naive_utc, to_local_date

logger = logging.getLogger(__name__)
MAX_DAYS = 42
MAX_EVENTS = 1000  # a busy calendar over six weeks; anything past this is dropped, and the response says so
CLOSED_APPLICATIONS = ("accepted", "rejected", "withdrawn")


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _read_calendar(source: Callable[[], str] | None, start: date, end: date, tz: ZoneInfo) -> tuple[str, str | None, list[dict]]:
    if source is None:
        return "off", None, []
    from app.integrations.calendar import CalendarError, parse_events

    try:
        return "ok", None, parse_events(source(), start, end, tz, limit=MAX_EVENTS)
    except CalendarError as exc:
        return "error", str(exc)[:160], []
    except Exception:  # noqa: BLE001 - a broken calendar must not break the page; Keel's own dates still show
        logger.warning("Calendar could not be read for the calendar view")
        return "error", "The calendar could not be read.", []


def build_calendar(db: Session, source: Callable[[], str] | None, start: date, days: int, tz: ZoneInfo,
                   workday: tuple[time, time], now: datetime) -> dict:
    days = max(1, min(days, MAX_DAYS))
    end = start + timedelta(days=days - 1)
    today = now.astimezone(tz).date()
    dates = [start + timedelta(days=n) for n in range(days)]
    items: dict[date, list[dict]] = {d: [] for d in dates}
    busy: dict[date, list[Interval]] = {d: [] for d in dates}
    timed: dict[date, list[tuple[datetime, datetime]]] = {d: [] for d in dates}

    state, error, events = _read_calendar(source, start, end, tz)
    for e in events:
        s, en = datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"])
        base = {"kind": "event", "title": _clean_title(e["title"]),
                "location": _clean_title(e["location"]) if e.get("location") else None}
        if e["all_day"]:
            last = (en - timedelta(seconds=1)).date() if en > s else s.date()
            d = s.date()
            while d <= last:
                if d in items:
                    items[d].append({**base, "all_day": True})
                d += timedelta(days=1)
            continue
        for d in dates:  # a timed event counts as busy on every day it touches
            lo, hi = datetime.combine(d, time.min, tzinfo=tz), datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz)
            if s < hi and en > lo:
                busy[d].append(Interval(max(s, lo), min(en, hi)))
                timed[d].append((s, en))
        shown = s.date() if s.date() in items else start
        items[shown].append({**base, "all_day": False, "start": _hhmm(s), "end": _hhmm(en), "minutes": e["minutes"],
                             "ends_other_day": (en - timedelta(microseconds=1)).date() > s.date()})  # ending at 00:00 isn't "next day"

    for plan in db.scalars(select(Plan).where(Plan.plan_date >= start, Plan.plan_date <= end)):
        for it in plan.items:
            items[plan.plan_date].append({"kind": "block", "title": it.title, "task_id": it.task_id, "status": it.status,
                                          "start": it.start_time, "end": it.end_time, "minutes": it.planned_minutes})

    due_tasks = list(db.scalars(select(Task).where(Task.due_date >= start, Task.due_date <= end, Task.status != "cancelled")))
    for t in due_tasks:
        items[t.due_date].append({"kind": "task", "id": t.id, "title": t.title, "status": t.status, "priority": t.priority})
    for c in db.scalars(select(Commitment).where(Commitment.status == "open", Commitment.deadline >= start, Commitment.deadline <= end)):
        items[c.deadline].append({"kind": "promise", "id": c.id, "title": c.text})
    for o in db.scalars(select(Opportunity).where(Opportunity.deadline >= start, Opportunity.deadline <= end,
                                                  Opportunity.status.notin_(CLOSED_APPLICATIONS))):
        items[o.deadline].append({"kind": "application", "id": o.id, "title": o.title, "status": o.status, "type": o.kind})
    for p in db.scalars(select(Project).where(Project.deadline >= start, Project.deadline <= end,
                                              Project.status.in_(("active", "paused")))):
        items[p.deadline].append({"kind": "project", "id": p.id, "title": p.name})
    lo_utc = local_to_naive_utc(datetime.combine(start, time.min), tz)
    hi_utc = local_to_naive_utc(datetime.combine(end + timedelta(days=1), time.min), tz)
    for r in db.scalars(select(Reminder).where(Reminder.status == "pending", Reminder.fire_at >= lo_utc, Reminder.fire_at < hi_utc)):
        local = r.fire_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        items[to_local_date(r.fire_at, tz)].append({"kind": "reminder", "id": r.id, "title": r.text, "start": _hhmm(local)})
    from app.tools.dates import next_occurrence

    for imp in db.scalars(select(ImportantDate)):
        when = next_occurrence(imp, start)
        if when is not None and when <= end:
            items[when].append({"kind": "date", "id": imp.id, "title": imp.title, "category": imp.category})

    out_days = []
    for d in dates:
        merged = merge(busy[d])
        spans = sorted(timed[d])
        overlaps = sum(1 for i, a in enumerate(spans) for b in spans[i + 1:] if b[0] < a[1])
        free = None if d < today else sum(s.minutes for s in free_slots(d, tz, workday, merged, now=now))
        need = sum(t.estimated_minutes or 30 for t in due_tasks if t.due_date == d and t.status in OPEN and actionable(db, t))
        warnings = []
        if free is not None and need > free:
            warnings.append(f"Packed: about {need} min of work is due but only {free} min is free.")
        if overlaps:
            warnings.append(f"Double-booked: {overlaps} pair(s) of events overlap.")
        day_items = sorted(items[d], key=lambda x: (not x.get("all_day", False) and x.get("start") is not None,
                                                    x.get("start") or "", x["kind"]))
        out_days.append({"date": d.isoformat(), "weekday": d.strftime("%a"), "is_today": d == today,
                         "busy_minutes": sum(i.minutes for i in merged), "free_minutes": free, "work_due_minutes": need,
                         "warnings": warnings, "items": day_items})

    overdue = []
    if start <= today <= end:
        overdue = [{"id": t.id, "title": t.title, "due": t.due_date.isoformat()} for t in db.scalars(
            select(Task).where(Task.status.in_(OPEN), Task.due_date < today).order_by(Task.due_date).limit(20))]
    return {"start": start.isoformat(), "end": end.isoformat(), "today": today.isoformat(), "calendar": state,
            "calendar_error": error, "events_truncated": len(events) >= MAX_EVENTS, "overdue": overdue, "days": out_days,
            "workday": [workday[0].strftime("%H:%M"), workday[1].strftime("%H:%M")]}
