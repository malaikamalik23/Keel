"""The daily briefing: what matters today, computed by code from the user's own data.

Used by the morning check-in (which has the LLM word it), by the `get_briefing` tool ("brief me"), and by the
dashboard. Two parts, kept apart on purpose:

- `facts`: the user's own tasks, commitments and plan. Safe to hand to the model to word.
- `calendar`: events from the iCal feed. Their titles are written by other people (anyone can send an invite), so they
  are never given to the model here. Code formats them, strips links, and appends them after the model's text.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Commitment, Task
from app.security.untrusted import sanitize_text
from app.services.planner import rank_tasks
from app.services.task_state import OPEN, actionable, open_blocker

logger = logging.getLogger(__name__)
COMING_UP_DAYS = 3
_LINK = re.compile(r"(https?://|www\.)\S+|\b[\w.-]+\.(com|net|org|io|ly|me|co|pk|xyz|info|link|app)(/\S*)?\b", re.IGNORECASE)


def _task(t: Task) -> dict:
    d = {"id": t.id, "title": t.title, "priority": t.priority}
    if t.due_date:
        d["due"] = t.due_date.isoformat()
    if t.estimated_minutes:
        d["minutes"] = t.estimated_minutes
    return d


def build_briefing(db: Session, today: date) -> dict:
    """Facts about the user's day from their own data (no calendar: see `calendar_lines`)."""
    from app.tools.planning import get_plan, plan_dict

    open_tasks = list(db.scalars(select(Task).where(Task.status.in_(OPEN))))
    ready = [t for t in open_tasks if actionable(db, t)]
    soon = today + timedelta(days=COMING_UP_DAYS)
    commitments = [c for c in db.scalars(select(Commitment).where(Commitment.status == "open")) if c.deadline]
    ranked = rank_tasks(ready, today)
    plan = plan_dict(get_plan(db, today), today)
    # Time-blocked items in clock order (the next one to do is the earliest block), unscheduled ones after them.
    planned_left = sorted((i for i in plan.get("items", []) if i["status"] == "planned"), key=lambda i: i.get("start") or "99:99")

    return {
        "date": today.isoformat(),
        "weekday": today.strftime("%A"),
        "next_action": (planned_left[0]["title"] if planned_left else (_task(ranked[0]) if ranked else None)),
        "plan": {"exists": plan["exists"], "items_left": len(planned_left), "items_total": len(plan.get("items", []))},
        "due_today": [_task(t) for t in ready if t.due_date == today][:5],
        "overdue": [_task(t) for t in ready if t.due_date and t.due_date < today][:5],
        "coming_up": [_task(t) for t in sorted(ready, key=lambda t: (t.due_date or date.max, t.id)) if t.due_date and today < t.due_date <= soon][:5],
        "commitments_due_soon": [
            {"text": c.text, "deadline": c.deadline.isoformat(), "overdue": c.deadline < today}
            for c in sorted(commitments, key=lambda c: c.deadline) if c.deadline <= soon
        ][:5],
        "follow_ups_due": [
            {"title": t.title, "waiting_on": t.waiting_on, "since": t.follow_up_date.isoformat()}
            for t in open_tasks if t.waiting_on and t.follow_up_date and t.follow_up_date <= today
        ][:5],
        "waiting_count": sum(1 for t in open_tasks if t.waiting_on),
        "blocked_count": sum(1 for t in open_tasks if not t.waiting_on and open_blocker(db, t) is not None),
        "slipping": [{"title": t.title, "postponed": t.postponement_count} for t in open_tasks if t.postponement_count >= 2][:3],
        "important_dates": _upcoming_dates(db, today),
        "applications_due_soon": _applications_due(db, today),
    }


def _applications_due(db: Session, today: date) -> list[dict]:
    from app.db.models import Opportunity
    from app.tools.applications import missing_documents

    rows = [o for o in db.scalars(select(Opportunity).where(Opportunity.status.in_(("interested", "preparing")), Opportunity.deadline.isnot(None)))
            if 0 <= (o.deadline - today).days <= 7]
    rows.sort(key=lambda o: o.deadline)
    return [{"title": o.title, "deadline": o.deadline.isoformat(), "missing": len(missing_documents(o))} for o in rows][:5]


def _upcoming_dates(db: Session, today: date) -> list[dict]:
    from app.tools.dates import upcoming

    return [{"title": d["title"], "date": d["date"], "days_left": d["days_left"]} for d in upcoming(db, today, 7)][:5]


def calendar_warnings(db: Session, source: Callable[[], str] | None, today: date, tz: ZoneInfo, workday) -> list[str]:
    """Double-booked or packed days, today and tomorrow, from event *times* only (safe to show the model)."""
    from app.services.scheduling import busy_intervals, free_slots, overlapping_events

    if source is None:
        return []
    out = []
    for day, name in ((today, "Today"), (today + timedelta(days=1), "Tomorrow")):
        clashes = overlapping_events(source, day, tz)
        if clashes:
            out.append(f"{name}: {clashes} pair(s) of calendar events overlap. You can't be in both.")
        busy = busy_intervals(source, day, tz) or []
        free = sum(s.minutes for s in free_slots(day, tz, workday, busy))
        due = [t for t in db.scalars(select(Task).where(Task.status.in_(OPEN), Task.due_date == day)) if actionable(db, t)]
        need = sum(t.estimated_minutes or 30 for t in due)
        if due and need > free:
            out.append(f"{name}: about {need} min of work is due but meetings leave only {free} min free.")
    return out


def _clean_title(title: str) -> str:
    """One plain line with no links: an invite title must not be able to put a clickable link in a Keel message."""
    text = " ".join(sanitize_text(title).split())
    text = _LINK.sub("[link removed]", text)
    return text[:80] or "(no title)"


def calendar_events(source: Callable[[], str] | None, today: date, tz: ZoneInfo) -> list[dict] | None:
    """Today's events, cleaned. None if no calendar is configured or it could not be read (never fails the briefing)."""
    if source is None:
        return None
    from app.integrations.calendar import CalendarError, parse_events

    try:
        events = parse_events(source(), today, today, tz)
    except (CalendarError, Exception):  # noqa: BLE001 - a broken calendar must not stop the morning message
        logger.warning("Calendar could not be read for the briefing")
        return None
    out = []
    for e in events:
        start = datetime.fromisoformat(e["start"])
        end = datetime.fromisoformat(e["end"])
        out.append({
            "title": _clean_title(e["title"]),
            "time": "all day" if e["all_day"] else f"{start:%H:%M}-{end:%H:%M}",
            "minutes": e["minutes"],
        })
    return out


def calendar_lines(events: list[dict] | None) -> str:
    if not events:
        return ""
    busy = sum(e["minutes"] for e in events)
    head = f"Calendar today ({busy // 60}h {busy % 60:02d}m busy):" if busy else "Calendar today:"
    return head + "\n" + "\n".join(f"- {e['time']} {e['title']}" for e in events)


def briefing_text(b: dict, events: list[dict] | None = None) -> str:
    """The briefing in plain words, without an LLM (fallback for the morning message, and the dashboard's text)."""
    lines = [f"Good morning. Here is {b['weekday']}."]
    nxt = b["next_action"]
    if nxt:
        lines.append(f"Start with: {nxt if isinstance(nxt, str) else nxt['title']}.")
    if b["plan"]["exists"]:
        lines.append(f"Today's plan: {b['plan']['items_left']} of {b['plan']['items_total']} item(s) still to do.")
    else:
        lines.append("No plan for today yet: tell me how much time you have and I'll build one.")
    if b["overdue"]:
        lines.append("Overdue: " + ", ".join(t["title"] for t in b["overdue"][:3]) + ".")
    if b["due_today"]:
        lines.append("Due today: " + ", ".join(t["title"] for t in b["due_today"][:3]) + ".")
    if b["commitments_due_soon"]:
        lines.append("Promises due soon: " + ", ".join(f"{c['text']} ({c['deadline']})" for c in b["commitments_due_soon"][:3]) + ".")
    if b["coming_up"]:
        lines.append("Coming up: " + ", ".join(f"{t['title']} ({t['due']})" for t in b["coming_up"][:3]) + ".")
    if b["follow_ups_due"]:
        lines.append("Time to follow up: " + ", ".join(f"{f['waiting_on']} about {f['title']}" for f in b["follow_ups_due"][:3]) + ".")
    if b.get("applications_due_soon"):
        lines.append("Applications closing soon: " + ", ".join(
            f"{a['title']} ({a['deadline']}{', ' + str(a['missing']) + ' document(s) missing' if a['missing'] else ''})"
            for a in b["applications_due_soon"][:3]) + ".")
    if b.get("important_dates"):
        lines.append("Dates: " + ", ".join(f"{d['title']} ({'today' if d['days_left'] == 0 else d['date']})" for d in b["important_dates"][:3]) + ".")
    for warning in b.get("calendar_warnings", [])[:2]:
        lines.append(warning)
    text = " ".join(lines)
    cal = calendar_lines(events)
    return f"{text}\n\n{cal}" if cal else text
