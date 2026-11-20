from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, WithJsonSchema
from sqlalchemy import select

from app.db.models import PlanItem, Project, Task, utcnow
from app.security.untrusted import sanitize_text
from app.services.task_events import record_task_event
from app.services.task_state import next_due, open_blocker, would_cycle
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

OPEN_STATUSES = ("todo", "in_progress")
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
Recurrence = Literal["daily", "weekdays", "weekly", "monthly"]


def _task_dict(t: Task, today: date | None = None, db=None) -> dict:
    d = {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "estimated_minutes": t.estimated_minutes,
        "postponement_count": t.postponement_count,
        "completed_at": t.completed_at.isoformat(timespec="minutes") if t.completed_at else None,
    }
    if today and t.due_date and t.status in OPEN_STATUSES and t.due_date < today:
        d["overdue"] = True
    if t.project_id:
        d["project_id"] = t.project_id
    if t.recurrence:
        d["repeats"] = t.recurrence
    if t.waiting_on:
        d["waiting_on"] = t.waiting_on
        d["follow_up_date"] = t.follow_up_date.isoformat() if t.follow_up_date else None
    if t.event_start:
        d["before_event_at_utc"] = t.event_start.isoformat(timespec="minutes")
    if db is not None and t.status in OPEN_STATUSES:
        blocker = open_blocker(db, t)
        if blocker is not None:
            d["blocked_by"] = {"id": blocker.id, "title": blocker.title}
    return d


def _clean_waiting(text: str | None) -> str | None:
    """Who/what the task waits for: one short line of plain text (it is shown in the prompt and the dashboard)."""
    if text is None:
        return None
    return " ".join(sanitize_text(text).split())[:120] or None


def _check_blocker(ctx: ToolContext, task_id: int | None, blocker_id: int) -> Task:
    blocker = _get_task(ctx, blocker_id)
    if task_id is not None and (blocker_id == task_id or would_cycle(ctx.db, task_id, blocker_id)):
        raise ToolError(f"Task #{blocker_id} already depends on this task, directly or through others: that would be a loop.")
    return blocker


def _link_event(ctx: ToolContext, source, uid: str, start: datetime) -> tuple[str, datetime]:
    """Confirm the calendar really has event `uid` at `start` (local); return (uid, start as naive UTC)."""
    from app.services.event_links import find_occurrence, to_utc_naive

    if source is None:
        raise ToolError("No calendar is connected, so a task can't be linked to an event.")
    local = start if start.tzinfo else start.replace(tzinfo=ctx.tz)
    try:
        found = find_occurrence(source, ctx.tz, uid, local)
    except Exception:  # noqa: BLE001 - calendar errors become a plain message
        raise ToolError("The calendar could not be read just now.") from None
    if found is None or abs((found - local).total_seconds()) > 60:
        raise ToolError("That event isn't on the calendar at that time. Use the uid and start from read_calendar.")
    return uid, to_utc_naive(found)


def _check_project(ctx: ToolContext, project_id: int) -> None:
    if ctx.db.get(Project, project_id) is None:
        raise ToolError(f"No project with id {project_id}.")


def _get_task(ctx: ToolContext, task_id: int) -> Task:
    task = ctx.db.get(Task, task_id)
    if task is None:
        raise ToolError(f"No task with id {task_id}.")
    return task


# ---------- create_task ----------
class CreateTaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="Short, action-oriented title.")
    description: str | None = Field(None, description="Optional details or definition of done.")
    due_date: date | None = Field(None, description="Deadline as YYYY-MM-DD in the user's local timezone.")
    priority: Literal["low", "medium", "high"] = "medium"
    estimated_minutes: int | None = Field(None, ge=1, le=1440, description="Realistic time estimate.")
    repeats: Recurrence | None = None
    blocked_by_task_id: int | None = Field(None, description="Task that must be done first.")
    waiting_on: str | None = Field(None, max_length=120, description="Who/what the user waits for.")
    follow_up_date: date | None = Field(None, description="When to chase them.")
    project_id: int | None = None
    before_event_uid: str | None = Field(None, max_length=255, description="read_calendar uid of an event this must be ready for.")
    # Advertised as a plain string: Groq rejects "date-time" values without seconds (see test_reminder_time_schema...).
    before_event_start: Annotated[datetime | None, WithJsonSchema({"type": "string"})] = Field(
        None, description="That event's start, from read_calendar, e.g. 2026-11-20T14:00.")


def create_task(ctx: ToolContext, p: CreateTaskInput, calendar_source: Callable[[], str] | None = None) -> dict:
    if p.blocked_by_task_id is not None:
        _check_blocker(ctx, None, p.blocked_by_task_id)
    if p.project_id is not None:
        _check_project(ctx, p.project_id)
    if p.follow_up_date and not p.waiting_on:
        raise ToolError("follow_up_date needs waiting_on: say who or what the user is waiting for.")
    event_uid = event_start = None
    due = p.due_date
    if p.before_event_uid or p.before_event_start:
        if not (p.before_event_uid and p.before_event_start):
            raise ToolError("To link an event, give both before_event_uid and before_event_start.")
        event_uid, event_start = _link_event(ctx, calendar_source, p.before_event_uid, p.before_event_start)
        from app.services.event_links import from_utc_naive

        event_day = from_utc_naive(event_start, ctx.tz).date()
        due = min(due, event_day) if due else event_day  # ready by the event at the latest
    task = Task(
        title=p.title.strip(),
        description=p.description,
        due_date=due or (ctx.today() if p.repeats else None),  # a routine needs a first date
        priority=p.priority,
        estimated_minutes=p.estimated_minutes,
        recurrence=p.repeats,
        blocked_by_id=p.blocked_by_task_id,
        waiting_on=_clean_waiting(p.waiting_on),
        follow_up_date=p.follow_up_date,
        project_id=p.project_id,
        event_uid=event_uid,
        event_start=event_start,
    )
    ctx.db.add(task)
    ctx.db.flush()
    record_task_event(ctx.db, task, "created")
    out = {"task": _task_dict(task, ctx.today(), ctx.db)}
    if p.due_date and p.due_date < ctx.today():
        out["warning"] = "The due date is in the past."
    return out


# ---------- list_tasks ----------
class ListTasksInput(BaseModel):
    status: Literal["open", "todo", "in_progress", "done", "cancelled", "all"] = Field(
        "open", description="'open' = todo + in_progress."
    )
    due_on_or_before: date | None = Field(None, description="Only tasks due on or before this date (YYYY-MM-DD).")
    only: Literal["waiting", "blocked", "repeating"] | None = None
    limit: int = Field(25, ge=1, le=100)


def list_tasks(ctx: ToolContext, p: ListTasksInput) -> dict:
    stmt = select(Task)
    if p.status == "open":
        stmt = stmt.where(Task.status.in_(OPEN_STATUSES))
    elif p.status != "all":
        stmt = stmt.where(Task.status == p.status)
    if p.due_on_or_before:
        stmt = stmt.where(Task.due_date <= p.due_on_or_before)
    if p.only == "waiting":
        stmt = stmt.where(Task.waiting_on.isnot(None))
    elif p.only == "repeating":
        stmt = stmt.where(Task.recurrence.isnot(None))
    tasks = list(ctx.db.scalars(stmt))
    if p.only == "blocked":
        tasks = [t for t in tasks if open_blocker(ctx.db, t) is not None]
    # Soonest deadline first (no deadline last), then priority.
    tasks.sort(key=lambda t: (t.due_date is None, t.due_date or date.max, PRIORITY_RANK[t.priority], t.id))
    today = ctx.today()
    return {"total": len(tasks), "tasks": [_task_dict(t, today, ctx.db) for t in tasks[: p.limit]]}


# ---------- update_task ----------
class UpdateTaskInput(BaseModel):
    task_id: int
    title: str | None = None
    description: str | None = None
    status: Literal["todo", "in_progress", "cancelled"] | None = Field(
        None, description="To mark a task done, use complete_task instead."
    )
    priority: Literal["low", "medium", "high"] | None = None
    due_date: date | None = Field(None, description="New deadline (YYYY-MM-DD). null clears it.")
    estimated_minutes: int | None = Field(None, ge=1, le=1440)
    repeats: Recurrence | None = None
    blocked_by_task_id: int | None = None
    waiting_on: str | None = Field(None, max_length=120, description="null once the reply came.")
    follow_up_date: date | None = None
    project_id: int | None = None
    reason: str | None = Field(
        None,
        max_length=500,
        description=(
            "Why this change is happening, in the user's words (e.g. when pushing a deadline later: 'too big', "
            "'sick', 'other priority'). Always pass it when postponing -- it powers the accountability analysis."
        ),
    )


def update_task(ctx: ToolContext, p: UpdateTaskInput) -> dict:
    task = _get_task(ctx, p.task_id)
    changes = p.model_dump(exclude_unset=True, exclude={"task_id"})
    reason = changes.pop("reason", None)
    if not changes:
        raise ToolError("No fields to update were provided.")
    for required in ("title", "status", "priority"):
        if required in changes and changes[required] is None:
            raise ToolError(f"'{required}' cannot be null.")

    renames = {"repeats": "recurrence", "blocked_by_task_id": "blocked_by_id"}
    changes = {renames.get(k, k): v for k, v in changes.items()}
    if changes.get("blocked_by_id") is not None:
        _check_blocker(ctx, task.id, changes["blocked_by_id"])
    if changes.get("project_id") is not None:
        _check_project(ctx, changes["project_id"])
    if "waiting_on" in changes:
        changes["waiting_on"] = _clean_waiting(changes["waiting_on"])
        if changes["waiting_on"] is None:
            changes.setdefault("follow_up_date", None)  # no longer waiting: nothing to chase
    still_waiting = changes["waiting_on"] if "waiting_on" in changes else task.waiting_on
    if changes.get("follow_up_date") and not still_waiting:
        raise ToolError("follow_up_date needs waiting_on: say who or what the user is waiting for.")

    old_status = task.status
    postponed = False
    if "due_date" in changes:
        new_due = changes.pop("due_date")
        # Pushing a deadline later counts as a postponement (this feeds the accountability system).
        if new_due and task.due_date and new_due > task.due_date:
            task.postponement_count += 1
            postponed = True
            record_task_event(
                ctx.db, task, "postponed", old=task.due_date.isoformat(), new=new_due.isoformat(), reason=reason
            )
        task.due_date = new_due
    for field, value in changes.items():
        setattr(task, field, value)
    if task.status in OPEN_STATUSES:
        task.completed_at = None
    if task.status != old_status:
        if task.status == "cancelled":
            record_task_event(ctx.db, task, "cancelled", old=old_status, new="cancelled", reason=reason)
        elif old_status in ("done", "cancelled") and task.status in OPEN_STATUSES:
            record_task_event(ctx.db, task, "reopened", old=old_status, new=task.status, reason=reason)

    ctx.db.flush()
    out = {"task": _task_dict(task, ctx.today(), ctx.db)}
    if postponed:
        out["postponed"] = True
    return out


# ---------- complete_task ----------
class CompleteTaskInput(BaseModel):
    task_id: int


def complete_task(ctx: ToolContext, p: CompleteTaskInput) -> dict:
    task = _get_task(ctx, p.task_id)
    if task.status == "done":
        return {"task": _task_dict(task), "note": "Task was already done."}
    was_overdue = bool(task.due_date and task.due_date < ctx.today())
    old_status = task.status
    task.status = "done"
    task.completed_at = utcnow()
    record_task_event(ctx.db, task, "completed", old=old_status, new="done")
    # Keep today's plan honest: finishing a task checks it off wherever it was planned.
    for item in ctx.db.scalars(select(PlanItem).where(PlanItem.task_id == task.id, PlanItem.status == "planned")):
        item.status = "done"
    out: dict = {"task": _task_dict(task), "was_overdue": was_overdue}
    if task.recurrence:
        # A routine: the next one appears as soon as this one is done, on the next date after today in its rhythm.
        nxt = Task(
            title=task.title, description=task.description, priority=task.priority, estimated_minutes=task.estimated_minutes,
            due_date=next_due(task.recurrence, task.due_date or ctx.today(), ctx.today()), recurrence=task.recurrence,
            project_id=task.project_id, goal_id=task.goal_id, milestone_id=task.milestone_id,
        )
        ctx.db.add(nxt)
        ctx.db.flush()
        record_task_event(ctx.db, nxt, "created", reason=f"repeats {task.recurrence} (after #{task.id})")
        out["next_occurrence"] = _task_dict(nxt, ctx.today())
    unblocked = list(ctx.db.scalars(select(Task).where(Task.blocked_by_id == task.id, Task.status.in_(OPEN_STATUSES))))
    if unblocked:
        out["unblocked"] = [{"id": t.id, "title": t.title} for t in unblocked]
        for t in unblocked:
            t.blocked_by_id = None  # the dependency is satisfied
    ctx.db.flush()
    return out


# ---------- delete_task (HIGH IMPACT) ----------
class DeleteTaskInput(BaseModel):
    task_id: int


def delete_task(ctx: ToolContext, p: DeleteTaskInput) -> dict:
    task = _get_task(ctx, p.task_id)
    title = task.title
    # A deleted task must not linger in today's plan as a ghost entry (it would count against plan adherence and show
    # up in the evening review). Items already done/skipped stay: they are history of what actually happened.
    removed = 0
    for item in ctx.db.scalars(select(PlanItem).where(PlanItem.task_id == task.id, PlanItem.status == "planned")):
        ctx.db.delete(item)
        removed += 1
    ctx.db.delete(task)
    ctx.db.flush()
    out = {"deleted_task_id": p.task_id, "title": title}
    if removed:
        out["removed_from_plan"] = removed
    return out


def _summarize_delete(ctx: ToolContext, p: DeleteTaskInput) -> str:
    task = _get_task(ctx, p.task_id)
    return f"Permanently delete task #{task.id}: \"{task.title}\""


def register(registry: ToolRegistry, calendar_source: Callable[[], str] | None = None) -> None:
    registry.register(
        Tool(
            name="create_task",
            description=(
                "Create a task. Use when the user wants to add something to their to-do list or commits to doing "
                "something concrete. Prefer one clear next action per task. Routines: repeats. Waiting on someone: "
                "waiting_on (+ follow_up_date). Must be ready for a meeting: before_event_uid/start."
            ),
            permission=Permission.WRITE,
            input_model=CreateTaskInput,
            handler=lambda ctx, p: create_task(ctx, p, calendar_source),
        )
    )
    registry.register(
        Tool(
            name="list_tasks",
            description=(
                "List the user's tasks, soonest due date first. Use to answer 'what should I work on', "
                "or 'what's due'. Overdue tasks are flagged; postponement_count shows how often "
                "a task has slipped. only='waiting' answers 'what am I waiting on'. For things the user promised or said they would do (commitments), use "
                "list_commitments instead."
            ),
            permission=Permission.READ,
            input_model=ListTasksInput,
            handler=list_tasks,
        )
    )
    registry.register(
        Tool(
            name="update_task",
            description=(
                "Change a task's fields (title, status, priority, due date, estimate, repeats, waiting_on...). Moving a "
                "due date later is recorded as a postponement. 'Move my <thing> deadline' or anything the user promised is a "
                "commitment: use update_commitment (list_commitments first if you need its id)."
            ),
            permission=Permission.WRITE,
            input_model=UpdateTaskInput,
            handler=update_task,
        )
    )
    registry.register(
        Tool(
            name="complete_task",
            description=(
                "Mark a task as done. Only call when the user says they finished it. If they say they did something "
                "they promised or committed to ('I finished the poster I promised'), use complete_commitment instead."
            ),
            permission=Permission.WRITE,
            input_model=CompleteTaskInput,
            handler=complete_task,
        )
    )
    registry.register(
        Tool(
            name="delete_task",
            description=(
                "Permanently delete a task. This is high impact: it is queued for the user's approval and does not "
                "run immediately. Prefer update_task with status 'cancelled' unless the user clearly wants deletion."
            ),
            permission=Permission.HIGH_IMPACT,
            input_model=DeleteTaskInput,
            handler=delete_task,
            summarize=_summarize_delete,
        )
    )
