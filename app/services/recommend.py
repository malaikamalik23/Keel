"""What to do right now: a short, ranked list of suggestions from the user's own data, with the reasons spelled out.

Code decides; nothing here calls a model. It weighs the same urgency and priority as the daily planner, then adjusts for
the moment: how much time there is before the next calendar event, how much energy the user says they have, the time
of day, today's plan, follow-ups that are due, promises about to fall due, and people the user wanted to keep in touch
with. Each suggestion carries its reasons, so the user can see why it came first and disagree.

Calendar event titles are written by other people, so they never appear here: only the time the next one starts.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Commitment, Person, Task
from app.services import people as people_svc
from app.services.planner import is_major, priority_score, task_minutes
from app.services.task_state import OPEN, actionable

logger = logging.getLogger(__name__)
ENERGY = ("low", "ok", "high")
SHORT = 30  # minutes: a task this short suits low energy or a gap between meetings
TIGHT = 120  # minutes: below this, whether something fits is worth a mention


def duration(minutes: int) -> str:
    """45 -> '45 min', 60 -> '1 h', 150 -> '2 h 30 min'."""
    h, m = divmod(minutes, 60)
    return f"{m} min" if not h else f"{h} h" if not m else f"{h} h {m} min"
MAX_SUGGESTIONS = 5


def _due_words(due: date | None, today: date) -> str | None:
    if due is None:
        return None
    days = (due - today).days
    if days < 0:
        return f"overdue by {-days} day{'s' if days != -1 else ''}"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    if days <= 6:
        return f"due {due.strftime('%A')}"
    return None


def part_of_day(now: datetime) -> str:
    return "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"


def free_window(source: Callable[[], str] | None, now: datetime, tz: ZoneInfo, workday_end: time | None = None) -> dict:
    """Minutes until the next calendar event (or the end of the working day), and whether one is on right now.
    Never fails: an unreadable calendar just means no limit is known."""
    out: dict = {"minutes": None, "until": None, "busy_now": False}
    limit: datetime | None = None
    if workday_end is not None:
        end = datetime.combine(now.date(), workday_end, tzinfo=tz)
        if end > now:
            limit = end
    if source is not None:
        from app.integrations.calendar import parse_events

        try:
            events = [e for e in parse_events(source(), now.date(), now.date(), tz) if not e["all_day"]]
        except Exception:  # noqa: BLE001 - a broken calendar must not break the suggestions
            logger.warning("Calendar could not be read for suggestions")
            events = []
        for e in events:
            start, end_ = datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"])
            if start.tzinfo is None:
                start, end_ = start.replace(tzinfo=tz), end_.replace(tzinfo=tz)
            if start <= now < end_:
                out["busy_now"] = True
                out["busy_until"] = end_.astimezone(tz).strftime("%H:%M")
            elif start > now and (limit is None or start < limit):
                limit = start
    if limit is not None:
        out["minutes"] = max(int((limit - now).total_seconds() // 60), 0)
        out["until"] = limit.astimezone(tz).strftime("%H:%M")
    return out


def planned_task_ids(db: Session, today: date) -> set[int]:
    """Tasks in today's plan that are not done or skipped yet."""
    from app.tools.planning import get_plan, plan_dict

    return {i["task_id"] for i in plan_dict(get_plan(db, today), today).get("items", []) if i.get("task_id") and i["status"] == "planned"}


def recommend(db: Session, now: datetime, *, energy: str | None = None, minutes: int | None = None,
              planned_task_ids: set[int] | None = None) -> dict:
    """Up to five things to do now, best first, each with its reasons. `minutes` is the time available (None: unknown)."""
    today = now.date()
    energy = energy if energy in ENERGY else None
    moment = part_of_day(now)
    planned = planned_task_ids or set()
    items: list[dict] = []

    for t in db.scalars(select(Task).where(Task.status.in_(OPEN))):
        if not actionable(db, t):
            continue
        mins, major = task_minutes(t), is_major(t)
        score = priority_score(t, today)
        reasons: list[str] = []
        due = _due_words(t.due_date, today)
        if due:
            reasons.append(due)
        if t.priority == "high":
            reasons.append("high priority")
        if t.id in planned:
            score += 25
            reasons.append("in today's plan")
        action = "complete"
        if minutes is not None and mins > minutes:
            if major and energy != "low" and minutes >= 20:
                score -= 10
                reasons.append(f"needs ~{duration(mins)}: start it and do the first {duration(minutes)}" if minutes <= TIGHT
                               else f"needs ~{duration(mins)}, more than today's free time: start it now")
                action = "start"
            else:
                score -= 60
                reasons.append(f"needs ~{duration(mins)}, more than you have")
        elif minutes is not None and minutes <= TIGHT:  # only worth saying when time is short
            score += 10
            reasons.append(f"fits in your {duration(minutes)}")
        if energy == "low":
            if mins <= SHORT and not major and (minutes is None or mins <= minutes):
                score += 20
                reasons.append("short and light, good for low energy")
            elif major:
                score -= 25
        elif energy == "high" and major:
            score += 20
            reasons.append("a big one, while you have the energy")
        elif energy is None and moment == "morning" and major:
            score += 8
            reasons.append("mornings suit deep work")
        elif energy is None and moment == "evening" and mins <= SHORT:
            score += 5
        if t.postponement_count >= 3:
            reasons.append(f"postponed {t.postponement_count}×: try just the first 10 minutes")
            action = "break_down"
        items.append({"kind": "task", "id": t.id, "title": t.title, "minutes": mins, "priority": t.priority,
                      "due": t.due_date.isoformat() if t.due_date else None, "score": score, "reasons": reasons, "action": action})

    for t in db.scalars(select(Task).where(Task.status.in_(OPEN), Task.waiting_on.is_not(None), Task.follow_up_date <= today)):
        late = (today - t.follow_up_date).days
        items.append({"kind": "follow_up", "id": t.id, "title": f"Follow up with {t.waiting_on}: {t.title}", "minutes": 10,
                      "score": 75 + min(late, 10) + (15 if energy == "low" else 0),
                      "reasons": ["follow-up date has come" if late == 0 else f"follow-up was due {late} day{'s' if late != 1 else ''} ago", "a quick message"],
                      "action": "follow_up"})

    for c in db.scalars(select(Commitment).where(Commitment.status == "open", Commitment.deadline <= today + timedelta(days=2))):
        due = _due_words(c.deadline, today) or "due soon"
        items.append({"kind": "promise", "id": c.id, "title": c.text, "minutes": None,
                      "score": (110 if c.deadline < today else 95 if c.deadline == today else 80) + (10 if c.importance == "high" else 0),
                      "reasons": [f"you promised this, {due}"], "action": "keep"})

    for p in db.scalars(select(Person).where(Person.keep_in_touch_days.is_not(None))):
        if people_svc.reconnect_due(p, today):
            since = people_svc.days_since(p, today)
            items.append({"kind": "person", "id": p.id, "title": f"Get in touch with {p.name}", "minutes": 10,
                          "score": 35 + (20 if energy == "low" or moment == "evening" else 0),
                          "reasons": [f"last talked {since} days ago" if since is not None else "no contact logged yet",
                                      f"you wanted to every {p.keep_in_touch_days} days"],
                          "action": "contacted"})

    items.sort(key=lambda i: (-i["score"], i["kind"] != "task", i["id"]))
    top = items[:MAX_SUGGESTIONS]
    note = None
    if not top:
        note = ("Nothing is waiting on you. Rest, or capture what's on your mind." if energy == "low" or moment == "evening"
                else "Nothing urgent. A good time to plan the week or work on a goal.")
    elif energy == "low":
        note = "Low energy: short wins first. Finishing one small thing usually makes the next easier."
    return {"now": now.strftime("%H:%M"), "part_of_day": moment, "energy": energy, "minutes": minutes,
            "suggestions": top, "note": note}
