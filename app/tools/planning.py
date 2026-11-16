"""Phase 3 tools: daily planning, plan items, work sessions, accountability queries."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Plan, PlanItem, Project, Task, WorkSession, utcnow
from app.services import accountability
from app.services.planner import MAX_MAJOR, build_plan, rank_tasks, usable_capacity
from app.services.task_events import record_task_event
from app.services.timeutil import window_utc
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

OPEN = ("todo", "in_progress")
DEFAULT_CAPACITY_MINUTES = 240


def _item_dict(i: PlanItem) -> dict:
    return {"item_id": i.id, "task_id": i.task_id, "title": i.title, "minutes": i.planned_minutes, "major": i.is_major, "status": i.status}


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


def create_daily_plan(ctx: ToolContext, p: CreateDailyPlanInput) -> dict:
    day = p.plan_date or ctx.today()
    plan = get_plan(ctx.db, day)
    if plan is None:
        plan = Plan(plan_date=day, capacity_minutes=p.capacity_minutes)
        ctx.db.add(plan)
        ctx.db.flush()
    else:
        plan.capacity_minutes = p.capacity_minutes

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
    else:
        open_tasks = [t for t in ctx.db.scalars(select(Task).where(Task.status.in_(OPEN))) if t.id not in kept_task_ids]
        ordered = rank_tasks(open_tasks, day)

    result = build_plan(ordered, p.capacity_minutes, already_used_minutes=used, already_major=majors)
    pos = len(kept)
    for c in result.selected:
        plan.items.append(PlanItem(task_id=c.task.id, title=c.task.title, planned_minutes=c.minutes, position=pos, is_major=c.is_major))
        pos += 1
    ctx.db.flush()
    out = plan_dict(plan, day)
    out["deferred"] = result.deferred[:10]
    out["limits"] = {"max_major_priorities": MAX_MAJOR}
    return out


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


def register(registry: ToolRegistry) -> None:
    for tool in (
        Tool(
            name="create_daily_plan",
            description=(
                "Build the plan for a day. Code picks and orders tasks by urgency/priority and guarantees the plan "
                f"fits the available time (with a buffer) and has at most {MAX_MAJOR} major priorities; anything left "
                "out comes back in 'deferred' with a reason. Use for 'plan my day'; call it directly, it reads the open tasks itself, so don't list tasks first. If you don't know how much time "
                "the user has, ask once first. Calling it again re-plans without losing finished items."
            ),
            permission=Permission.WRITE, input_model=CreateDailyPlanInput, handler=create_daily_plan,
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
