"""Phase 3 tools: daily planning, plan items, work sessions, accountability queries."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Commitment, Plan, PlanItem, Project, Task, WorkSession, utcnow
from app.services import accountability
from app.services.planner import MAX_MAJOR, build_plan, rank_tasks, usable_capacity
from app.services.scheduling import busy_intervals, free_slots, overlapping_events, place
from app.services.task_events import record_task_event
from app.services.task_state import actionable, open_blocker
from app.services.timeutil import window_utc
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

OPEN = ("todo", "in_progress")
DEFAULT_CAPACITY_MINUTES = 240
DEFAULT_WORKDAY = (time(9, 0), time(21, 0))


def _item_dict(i: PlanItem) -> dict:
    d = {"item_id": i.id, "task_id": i.task_id, "title": i.title, "minutes": i.planned_minutes, "major": i.is_major, "status": i.status}
    if i.start_time:
        d["start"], d["end"] = i.start_time, i.end_time
    return d


def plan_dict(plan: Plan | None, plan_date: date) -> dict:
    if plan is None:
        return {"date": plan_date.isoformat(), "exists": False}
    items = list(plan.items)
    return {
        "date": plan.plan_date.isoformat(), "exists": True,
        "capacity_minutes": plan.capacity_minutes, "usable_minutes": usable_capacity(plan.capacity_minutes),
        "planned_minutes": sum(i.planned_minutes for i in items if i.status != "skipped"),
        "items": [_item_dict(i) for i in items],
    }


def get_plan(db, plan_date: date) -> Plan | None:
    return db.scalars(select(Plan).where(Plan.plan_date == plan_date)).first()


# ---------- create_daily_plan ----------
class CreateDailyPlanInput(BaseModel):
    plan_date: date | None = Field(None, description="Day to plan (YYYY-MM-DD). Defaults to today.")
    capacity_minutes: int = Field(
        DEFAULT_CAPACITY_MINUTES, ge=15, le=1440,
        description="Minutes the user can realistically work that day. Ask if you don't know; 15% is kept free as a buffer.",
    )
    task_ids: list[int] | None = Field(
        None, max_length=30, description="Specific tasks, in priority order, if the user chose them. Omit to auto-select."
    )


def make_create_daily_plan(calendar_source: Callable[[], str] | None = None, workday: tuple[time, time] = DEFAULT_WORKDAY):
    def create_daily_plan(ctx: ToolContext, p: CreateDailyPlanInput) -> dict:
        return _create_daily_plan(ctx, p, calendar_source, workday)

    return create_daily_plan


def _create_daily_plan(ctx: ToolContext, p: CreateDailyPlanInput, calendar_source, workday, exclude: frozenset[int] = frozenset()) -> dict:
    day = p.plan_date or ctx.today()
    # Free time: working hours minus calendar events (times only, never titles), from now on if the day is today.
    busy = busy_intervals(calendar_source, day, ctx.tz)
    slots = free_slots(day, ctx.tz, workday, busy or [], now=ctx.now())
    free_minutes = sum(s.minutes for s in slots)
    capacity = p.capacity_minutes
    time_warnings: list[str] = []
    if busy is not None and capacity > free_minutes:
        time_warnings.append(
            f"You said {capacity} min, but your calendar and working hours leave {free_minutes} min free"
            f"{' from now' if day == ctx.today() else ''}, so the plan uses {free_minutes} min."
        )
        capacity = free_minutes
    plan = get_plan(ctx.db, day)
    if capacity < 15:
        raise ToolError(f"There is no free time left {'today' if day == ctx.today() else 'that day'} "
                        f"between your calendar events and working hours ({free_minutes} min).")
    if plan is None:
        plan = Plan(plan_date=day, capacity_minutes=capacity)
        ctx.db.add(plan)
        ctx.db.flush()
    else:
        plan.capacity_minutes = capacity

    # Re-planning keeps what is already done/skipped; only the still-"planned" items are rebuilt.
    kept = [i for i in plan.items if i.status != "planned"]
    for i in [i for i in plan.items if i.status == "planned"]:
        plan.items.remove(i)
    ctx.db.flush()
    kept_task_ids = {i.task_id for i in kept if i.task_id}
    used = sum(i.planned_minutes for i in kept if i.status == "done")
    majors = sum(1 for i in kept if i.is_major and i.status == "done")

    if p.task_ids is not None:
        found = {t.id: t for t in ctx.db.scalars(select(Task).where(Task.id.in_(p.task_ids)))}
        missing = [i for i in p.task_ids if i not in found]
        if missing:
            raise ToolError(f"No task with id {missing[0]}.")
        closed = [t.title for t in found.values() if t.status not in OPEN]
        if closed:
            raise ToolError(f"Task '{closed[0]}' is not open, so it can't be planned.")
        ordered = [found[i] for i in p.task_ids if i not in kept_task_ids]
        not_ready = [t for t in ordered if not actionable(ctx.db, t)]  # the user chose them: plan them, but say so
    else:
        # Only what can be worked on now: not waiting on someone, not waiting for an unfinished task.
        open_tasks = [t for t in ctx.db.scalars(select(Task).where(Task.status.in_(OPEN))) if t.id not in kept_task_ids and t.id not in exclude]
        not_ready = []
        ordered = rank_tasks([t for t in open_tasks if actionable(ctx.db, t)], day)

    result = build_plan(ordered, capacity, already_used_minutes=used, already_major=majors)
    # Time-block what was chosen, in order, into the free slots (whole blocks, a short break after each).
    def _end_by(t: Task):
        """A task that must be ready for a meeting that day has to finish before the meeting starts."""
        if not t.event_start:
            return None
        from app.services.event_links import from_utc_naive

        start = from_utc_naive(t.event_start, ctx.tz)
        return start if start.date() == day else None

    placed, unplaced = place([(c.task.id, c.minutes, _end_by(c.task)) for c in result.selected], slots)
    pos = len(kept)
    for c in result.selected:
        block = placed.get(c.task.id)
        plan.items.append(PlanItem(
            task_id=c.task.id, title=c.task.title, planned_minutes=c.minutes, position=pos, is_major=c.is_major,
            start_time=block.start.strftime("%H:%M") if block else None, end_time=block.end.strftime("%H:%M") if block else None,
        ))
        pos += 1
    ctx.db.flush()
    out = plan_dict(plan, day)
    out["deferred"] = result.deferred[:10]
    out["limits"] = {"max_major_priorities": MAX_MAJOR}
    out["free_minutes"] = free_minutes
    if busy is not None:
        out["busy"] = [{"start": b.start.strftime("%H:%M"), "end": b.end.strftime("%H:%M")} for b in busy]
    if unplaced:
        names = [c.task.title for c in result.selected if c.task.id in unplaced]
        time_warnings.append("No single free stretch is long enough for: " + ", ".join(names) + ". They are planned without a time.")
    warnings = time_warnings + [
        f"'{t.title}' is waiting on {t.waiting_on}." if t.waiting_on else f"'{t.title}' can't start until '{open_blocker(ctx.db, t).title}' is done."
        for t in not_ready
    ]
    if warnings:
        out["warnings"] = warnings
    return out


create_daily_plan = make_create_daily_plan()  # no calendar, default working hours (tests and direct use)


# ---------- get_daily_plan ----------
class GetDailyPlanInput(BaseModel):
    plan_date: date | None = Field(None, description="Defaults to today.")


def get_daily_plan(ctx: ToolContext, p: GetDailyPlanInput) -> dict:
    day = p.plan_date or ctx.today()
    return plan_dict(get_plan(ctx.db, day), day)


# ---------- update_plan_item ----------
class UpdatePlanItemInput(BaseModel):
    item_id: int
    status: Literal["planned", "done", "skipped"]
    reason: str | None = Field(None, max_length=500, description="Why it was skipped, in the user's words.")


def update_plan_item(ctx: ToolContext, p: UpdatePlanItemInput) -> dict:
    item = ctx.db.get(PlanItem, p.item_id)
    if item is None:
        raise ToolError(f"No plan item with id {p.item_id}.")
    old = item.status
    item.status = p.status
    if p.status == "skipped" and old != "skipped" and item.task_id:
        task = ctx.db.get(Task, item.task_id)
        if task is not None:
            record_task_event(ctx.db, task, "skipped", old="planned", new="skipped", reason=p.reason)
    ctx.db.flush()
    return {"item": _item_dict(item)}


# ---------- log_work_session ----------
class LogWorkSessionInput(BaseModel):
    minutes: int = Field(ge=1, le=720)
    task_id: int | None = None
    project_id: int | None = None
    note: str | None = Field(None, max_length=500)


def log_work_session(ctx: ToolContext, p: LogWorkSessionInput) -> dict:
    project_id = p.project_id
    task = None
    if p.task_id is not None:
        task = ctx.db.get(Task, p.task_id)
        if task is None:
            raise ToolError(f"No task with id {p.task_id}.")
        project_id = project_id or task.project_id
    if project_id is not None and ctx.db.get(Project, project_id) is None:
        raise ToolError(f"No project with id {project_id}.")
    ended = utcnow()
    s = WorkSession(
        task_id=p.task_id, project_id=project_id, minutes=p.minutes, note=p.note,
        ended_at=ended, started_at=ended - timedelta(minutes=p.minutes),
    )
    ctx.db.add(s)
    if task is not None and task.status == "todo":
        task.status = "in_progress"
    ctx.db.flush()
    return {"session": {"id": s.id, "task_id": s.task_id, "project_id": s.project_id, "minutes": s.minutes}}


# ---------- list_work_sessions ----------
class ListWorkSessionsInput(BaseModel):
    days: int = Field(7, ge=1, le=90)


def list_work_sessions(ctx: ToolContext, p: ListWorkSessionsInput) -> dict:
    start = ctx.today() - timedelta(days=p.days - 1)
    lo, hi = window_utc(start, ctx.today(), ctx.tz)
    rows = list(ctx.db.scalars(select(WorkSession).where(WorkSession.started_at >= lo, WorkSession.started_at < hi).order_by(WorkSession.started_at)))
    return {
        "total_minutes": sum(r.minutes for r in rows),
        "sessions": [{"id": r.id, "task_id": r.task_id, "project_id": r.project_id, "minutes": r.minutes, "note": r.note} for r in rows],
    }


# ---------- accountability_report ----------
class AccountabilityInput(BaseModel):
    window_days: int = Field(7, ge=1, le=60)


def accountability_report(ctx: ToolContext, p: AccountabilityInput) -> dict:
    return accountability.accountability_report(ctx.db, ctx.today(), ctx.tz, window_days=p.window_days)


# ---------- task_history ----------
class TaskHistoryInput(BaseModel):
    task_id: int


def task_history(ctx: ToolContext, p: TaskHistoryInput) -> dict:
    task = ctx.db.get(Task, p.task_id)
    if task is None:
        raise ToolError(f"No task with id {p.task_id}.")
    return {"task_id": task.id, "title": task.title, "postponement_count": task.postponement_count, "events": accountability.task_history(ctx.db, task.id)}


# ---------- plan_week ----------
class PlanWeekInput(BaseModel):
    start_date: date | None = Field(None, description="First day to plan (YYYY-MM-DD). Defaults to today.")
    days: int = Field(5, ge=1, le=7, description="How many days to plan.")
    capacity_minutes_per_day: int = Field(
        DEFAULT_CAPACITY_MINUTES, ge=15, le=1440, description="Minutes the user can work on a normal day. Ask if you don't know."
    )
    skip_weekends: bool = Field(True, description="Leave Saturday and Sunday unplanned.")


def make_plan_week(calendar_source: Callable[[], str] | None = None, workday: tuple[time, time] = DEFAULT_WORKDAY):
    def plan_week(ctx: ToolContext, p: PlanWeekInput) -> dict:
        """Day by day, the same planner as create_daily_plan: each day around that day's meetings, each task once,
        the most urgent first. What doesn't fit anywhere is reported, not dropped."""
        start = p.start_date or ctx.today()
        scheduled: set[int] = set()
        days, skipped = [], []
        day, planned_days = start, 0
        while planned_days < p.days:
            if p.skip_weekends and day.weekday() >= 5:
                day += timedelta(days=1)
                continue
            try:
                out = _create_daily_plan(
                    ctx, CreateDailyPlanInput(plan_date=day, capacity_minutes=p.capacity_minutes_per_day),
                    calendar_source, workday, exclude=frozenset(scheduled),
                )
            except ToolError as exc:
                skipped.append({"date": day.isoformat(), "reason": str(exc)})
            else:
                ids = {i["task_id"] for i in out["items"] if i["task_id"] and i["status"] == "planned"}
                scheduled |= ids
                days.append({
                    "date": day.isoformat(), "weekday": day.strftime("%A"), "planned_minutes": out["planned_minutes"],
                    "free_minutes": out["free_minutes"],
                    "items": [{k: i[k] for k in ("title", "minutes", "start", "end") if k in i} for i in out["items"] if i["status"] == "planned"],
                    **({"warnings": out["warnings"]} if out.get("warnings") else {}),
                })
            planned_days += 1
            day += timedelta(days=1)
        left = [t for t in ctx.db.scalars(select(Task).where(Task.status.in_(OPEN)))
                if t.id not in scheduled and actionable(ctx.db, t)]
        result: dict = {"days": days}
        if skipped:
            result["days_without_time"] = skipped
        if left:
            result["not_planned"] = [{"title": t.title, "due": t.due_date.isoformat() if t.due_date else None} for t in left][:10]
            late = [t.title for t in left if t.due_date and t.due_date <= start + timedelta(days=p.days)]
            if late:
                result["warning"] = "Due this week but no room for: " + ", ".join(late[:5]) + ". Something has to give."
        return result

    return plan_week


# ---------- get_week_overview ----------
class WeekOverviewInput(BaseModel):
    start_date: date | None = Field(None, description="First day (YYYY-MM-DD). Defaults to today.")
    days: int = Field(7, ge=1, le=14)


def make_week_overview(calendar_source: Callable[[], str] | None = None, workday: tuple[time, time] = DEFAULT_WORKDAY):
    def get_week_overview(ctx: ToolContext, p: WeekOverviewInput) -> dict:
        """Per day: calendar time taken, free working time, and what is due. Uses event times only, never titles."""
        start = p.start_date or ctx.today()
        open_tasks = list(ctx.db.scalars(select(Task).where(Task.status.in_(OPEN))))
        promises = [c for c in ctx.db.scalars(select(Commitment).where(Commitment.status == "open")) if c.deadline]
        out = []
        for n in range(p.days):
            day = start + timedelta(days=n)
            busy = busy_intervals(calendar_source, day, ctx.tz)
            free = sum(s.minutes for s in free_slots(day, ctx.tz, workday, busy or [], now=ctx.now()))
            due = [t for t in open_tasks if t.due_date == day]
            need = sum(t.estimated_minutes or 30 for t in due if actionable(ctx.db, t))
            entry = {
                "date": day.isoformat(), "weekday": day.strftime("%A"),
                "busy_minutes": sum(b.minutes for b in busy) if busy else 0, "free_minutes": free,
                "tasks_due": [t.title for t in due], "promises_due": [c.text for c in promises if c.deadline == day],
            }
            notes = []
            if due and need > free:
                notes.append(f"packed: about {need} min of work due but only {free} min free")
            overlaps = overlapping_events(calendar_source, day, ctx.tz)
            if overlaps:
                notes.append(f"double-booked: {overlaps} pair(s) of events overlap")
            if notes:
                entry["warnings"] = notes
            out.append(entry)
        return {"calendar": calendar_source is not None, "days": out}

    return get_week_overview


# ---------- get_briefing ----------
class GetBriefingInput(BaseModel):
    pass


def get_briefing(ctx: ToolContext, p: GetBriefingInput) -> dict:
    """The day at a glance from the user's own data. Calendar events are not included (their titles come from other
    people): use read_calendar for those."""
    from app.services.briefing import build_briefing

    return build_briefing(ctx.db, ctx.today())


# ---------- what_next ----------
class WhatNextInput(BaseModel):
    energy: Literal["low", "ok", "high"] | None = Field(None, description="How the user says they feel: 'tired' = low, 'fresh/motivated' = high.")
    minutes: int | None = Field(None, ge=5, le=600, description="Time the user has, if they said ('I have 30 minutes'). Leave empty to use the time until their next calendar event.")


def _what_next(calendar_source: Callable[[], str] | None, workday: tuple[time, time]):
    def what_next(ctx: ToolContext, p: WhatNextInput) -> dict:
        from app.services.recommend import free_window, planned_task_ids, recommend

        now = ctx.now()
        window = free_window(calendar_source, now, ctx.tz, workday[1]) if p.minutes is None else {"minutes": p.minutes}
        out = recommend(ctx.db, now, energy=p.energy, minutes=window.get("minutes"), planned_task_ids=planned_task_ids(ctx.db, now.date()))
        return {**out, "free_until": window.get("until"), "in_a_meeting_now": window.get("busy_now", False)}
    return what_next


def register(registry: ToolRegistry, calendar_source: Callable[[], str] | None = None, workday: tuple[time, time] = DEFAULT_WORKDAY) -> None:
    for tool in (
        Tool(
            name="what_next",
            description=(
                "Suggest what the user should do right now, best first, with the reasons: weighs deadlines, priority, "
                "today's plan, the time until their next calendar event (or the minutes they give), their energy, the "
                "time of day, follow-ups and promises falling due, and people they wanted to keep in touch with. Use for "
                "'what should I do now', 'I have 30 minutes', 'I'm tired, what can I do'."
            ),
            permission=Permission.READ, input_model=WhatNextInput, handler=_what_next(calendar_source, workday),
        ),
        Tool(
            name="plan_week",
            description=(
                "Plan several days at once (default the next 5 weekdays): each day gets time blocks around that day's "
                "calendar, each task is planned once, most urgent first, and anything due this week that doesn't fit is "
                "flagged. Use for 'plan my week'. Ask how many hours a day they have if you don't know."
            ),
            permission=Permission.WRITE, input_model=PlanWeekInput, handler=make_plan_week(calendar_source, workday),
        ),
        Tool(
            name="get_week_overview",
            description=(
                "The week ahead, day by day: how much of the working day the calendar takes, how much is free, and "
                "which tasks and promises are due, with warnings for packed or double-booked days. Use for 'how does "
                "my week look', 'when do I have time', 'which day is free for X'."
            ),
            permission=Permission.READ, input_model=WeekOverviewInput, handler=make_week_overview(calendar_source, workday),
        ),
        Tool(
            name="get_briefing",
            description=(
                "The user's day at a glance: the best next task, what is due today or overdue, promises due soon, what "
                "is coming up, follow-ups due, and how many tasks are waiting or blocked. Use for 'brief me', 'what does "
                "my day look like', 'what's important today'. Does not include calendar events (use read_calendar)."
            ),
            permission=Permission.READ, input_model=GetBriefingInput, handler=get_briefing,
        ),
        Tool(
            name="create_daily_plan",
            description=(
                "Build the plan for a day. Code picks and orders tasks by urgency/priority and guarantees the plan "
                f"fits the available time (with a buffer) and has at most {MAX_MAJOR} major priorities; anything left "
                "out comes back in 'deferred' with a reason. Each item gets a time block around the user's calendar "
                "events and working hours. Use for 'plan my day'; call it directly, it reads the open tasks and the "
                "calendar itself, so don't list tasks first. If you don't know how much time the user has, ask once "
                "first. Calling it again re-plans from now without losing finished items."
            ),
            permission=Permission.WRITE, input_model=CreateDailyPlanInput, handler=make_create_daily_plan(calendar_source, workday),
        ),
        Tool(
            name="get_daily_plan",
            description="Show the plan for a day with each item's status. Use for 'what's on my plate today' or evening reviews.",
            permission=Permission.READ, input_model=GetDailyPlanInput, handler=get_daily_plan,
        ),
        Tool(
            name="update_plan_item",
            description="Mark a plan item done, skipped or back to planned. When skipping, pass the user's reason. (complete_task already checks items off.)",
            permission=Permission.WRITE, input_model=UpdatePlanItemInput, handler=update_plan_item,
        ),
        Tool(
            name="log_work_session",
            description="Record time the user actually spent working ('I did 45 minutes on the FYP intro'). Link the task and/or project when known.",
            permission=Permission.WRITE, input_model=LogWorkSessionInput, handler=log_work_session,
        ),
        Tool(
            name="list_work_sessions",
            description="List logged work sessions and total minutes over the last N days.",
            permission=Permission.READ, input_model=ListWorkSessionsInput, handler=list_work_sessions,
        ),
        Tool(
            name="accountability_report",
            description=(
                "Facts computed from history: completion vs plan, most-postponed tasks (with the reasons the user gave), "
                "old tasks never worked on, overdue commitments, neglected goals, time spent per project. Use for "
                "'what have I been avoiding', weekly reviews, and check-ins. Never invent patterns beyond this data."
            ),
            permission=Permission.READ, input_model=AccountabilityInput, handler=accountability_report,
        ),
        Tool(
            name="task_history",
            description="The event history of one task (created, postponed with reasons, skipped, completed). Use to diagnose why a task keeps slipping.",
            permission=Permission.READ, input_model=TaskHistoryInput, handler=task_history,
        ),
    ):
        registry.register(tool)
