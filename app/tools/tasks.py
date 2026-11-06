from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Task, utcnow
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


def update_task(ctx: ToolContext, p: UpdateTaskInput) -> dict:
    task = _get_task(ctx, p.task_id)
    changes = p.model_dump(exclude_unset=True, exclude={"task_id"})
    if not changes:
        raise ToolError("No fields to update were provided.")
    for required in ("title", "status", "priority"):
        if required in changes and changes[required] is None:
            raise ToolError(f"'{required}' cannot be null.")

    postponed = False
    if "due_date" in changes:
        new_due = changes.pop("due_date")
        # Pushing a deadline later counts as a postponement (this feeds the accountability system).
        if new_due and task.due_date and new_due > task.due_date:
            task.postponement_count += 1
            postponed = True
        task.due_date = new_due
    for field, value in changes.items():
        setattr(task, field, value)
    if task.status in OPEN_STATUSES:
        task.completed_at = None

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
    task.status = "done"
    task.completed_at = utcnow()
    ctx.db.flush()
    return {"task": _task_dict(task), "was_overdue": was_overdue}


# ---------- delete_task (HIGH IMPACT) ----------
class DeleteTaskInput(BaseModel):
    task_id: int


def delete_task(ctx: ToolContext, p: DeleteTaskInput) -> dict:
    task = _get_task(ctx, p.task_id)
    title = task.title
    ctx.db.delete(task)
    ctx.db.flush()
    return {"deleted_task_id": p.task_id, "title": title}


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
                "List the user's tasks, soonest deadline first. Use to answer 'what should I work on', "
                "'what's due', or before planning. Overdue tasks are flagged; postponement_count shows how often "
                "a task has slipped."
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
                "Change a task's title, description, status, priority, deadline or estimate. Moving a deadline "
                "later is recorded as a postponement."
            ),
            permission=Permission.WRITE,
            input_model=UpdateTaskInput,
            handler=update_task,
        )
    )
    registry.register(
        Tool(
            name="complete_task",
            description="Mark a task as done. Only call when the user says they finished it.",
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
