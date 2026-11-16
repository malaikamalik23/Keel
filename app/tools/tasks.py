from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import PlanItem, Task, utcnow
from app.services.task_events import record_task_event
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

OPEN_STATUSES = ("todo", "in_progress")
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _task_dict(t: Task, today: date | None = None) -> dict:
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
    return d


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


def create_task(ctx: ToolContext, p: CreateTaskInput) -> dict:
    task = Task(
        title=p.title.strip(),
        description=p.description,
        due_date=p.due_date,
        priority=p.priority,
        estimated_minutes=p.estimated_minutes,
    )
    ctx.db.add(task)
    ctx.db.flush()
    record_task_event(ctx.db, task, "created")
    out = {"task": _task_dict(task, ctx.today())}
    if p.due_date and p.due_date < ctx.today():
        out["warning"] = "The due date is in the past."
    return out


# ---------- list_tasks ----------
class ListTasksInput(BaseModel):
    status: Literal["open", "todo", "in_progress", "done", "cancelled", "all"] = Field(
        "open", description="'open' = todo + in_progress."
    )
    due_on_or_before: date | None = Field(None, description="Only tasks due on or before this date (YYYY-MM-DD).")
    limit: int = Field(25, ge=1, le=100)


def list_tasks(ctx: ToolContext, p: ListTasksInput) -> dict:
    stmt = select(Task)
    if p.status == "open":
        stmt = stmt.where(Task.status.in_(OPEN_STATUSES))
    elif p.status != "all":
        stmt = stmt.where(Task.status == p.status)
    if p.due_on_or_before:
        stmt = stmt.where(Task.due_date <= p.due_on_or_before)
    tasks = list(ctx.db.scalars(stmt))
    # Soonest deadline first (no deadline last), then priority.
    tasks.sort(key=lambda t: (t.due_date is None, t.due_date or date.max, PRIORITY_RANK[t.priority], t.id))
    today = ctx.today()
    return {"total": len(tasks), "tasks": [_task_dict(t, today) for t in tasks[: p.limit]]}


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
    out = {"task": _task_dict(task, ctx.today())}
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
    ctx.db.flush()
    return {"task": _task_dict(task), "was_overdue": was_overdue}


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


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="create_task",
            description=(
                "Create a task. Use when the user wants to add something to their to-do list or commits to doing "
                "something concrete. Prefer one clear next action per task."
            ),
            permission=Permission.WRITE,
            input_model=CreateTaskInput,
            handler=create_task,
        )
    )
    registry.register(
        Tool(
            name="list_tasks",
            description=(
                "List the user's tasks, soonest due date first. Use to answer 'what should I work on', "
                "or 'what's due'. Overdue tasks are flagged; postponement_count shows how often "
                "a task has slipped. For things the user promised or said they would do (commitments), use "
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
                "Change a task's title, description, status, priority, due date or estimate. Moving a due date "
                "later is recorded as a postponement. 'Move my <thing> deadline' or anything the user promised is a "
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
