from __future__ import annotations

import hmac
from datetime import date, datetime
from typing import Iterator, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.container import Container
from app.db.models import (
    AuditLog, Checkin, Commitment, Conversation, Goal, Memory, Message, Notification, PendingAction, PlanItem, Project,
    Reminder, Task, TaskEvent, WorkSession,
)
from app.services import accountability as accountability_svc
from app.tools.planning import get_plan, plan_dict
from app.errors import NotFoundError
from app.tools.base import ToolContext


def get_container(request: Request) -> Container:
    return request.app.state.container


def require_api_key(
    c: Container = Depends(get_container), x_api_key: str | None = Header(default=None, alias="X-API-Key")
) -> None:
    """Guards every /api/* route. A no-op when `api_key` is unset (localhost-only default)."""
    configured = c.settings.api_key
    if configured is None:
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid X-API-Key header.")


router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


def get_db(container: Container = Depends(get_container)) -> Iterator[Session]:
    with container.session_factory() as db:
        yield db


@router.post("/chat", response_model=schemas.ChatResponse)
def chat(req: schemas.ChatRequest, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    result = c.agent.chat(db, req.message, req.conversation_id)
    return schemas.ChatResponse(
        conversation_id=result.conversation_id,
        reply=result.reply,
        tool_calls=[schemas.ToolCallOut.model_validate(t) for t in result.tool_calls],
        pending_action_ids=result.pending_action_ids,
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[schemas.MessageOut])
def conversation_messages(conversation_id: int, db: Session = Depends(get_db)):
    if db.get(Conversation, conversation_id) is None:
        raise NotFoundError(f"No conversation with id {conversation_id}.")
    return db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()


@router.get("/tasks", response_model=list[schemas.TaskOut])
def tasks(
    status: Literal["open", "todo", "in_progress", "done", "cancelled", "all"] = "open",
    db: Session = Depends(get_db),
):
    stmt = select(Task).order_by(Task.due_date.is_(None), Task.due_date, Task.id)
    if status == "open":
        stmt = stmt.where(Task.status.in_(("todo", "in_progress")))
    elif status != "all":
        stmt = stmt.where(Task.status == status)
    return db.scalars(stmt).all()


@router.get("/goals", response_model=list[schemas.GoalOut])
def goals(
    status: Literal["active", "paused", "achieved", "dropped", "all"] = "active",
    db: Session = Depends(get_db),
):
    stmt = select(Goal).order_by(Goal.id)
    if status != "all":
        stmt = stmt.where(Goal.status == status)
    return db.scalars(stmt).all()


@router.get("/projects", response_model=list[schemas.ProjectOut])
def projects(
    status: Literal["active", "paused", "completed", "archived", "all"] = "active",
    db: Session = Depends(get_db),
):
    stmt = select(Project).order_by(Project.deadline.is_(None), Project.deadline, Project.id)
    if status != "all":
        stmt = stmt.where(Project.status == status)
    return db.scalars(stmt).all()


@router.get("/commitments", response_model=list[schemas.CommitmentOut])
def commitments(
    status: Literal["open", "proposed", "done", "dropped", "all"] = "open",
    db: Session = Depends(get_db),
):
    stmt = select(Commitment)
    if status != "all":
        stmt = stmt.where(Commitment.status == status)
    rows = list(db.scalars(stmt))
    rows.sort(key=lambda c: (c.deadline is None, c.deadline or date.max, c.id))
    return rows


@router.get("/memories", response_model=list[schemas.MemoryOut])
def memories(
    kind: Literal["preference", "context", "fact", "all"] = "all",
    db: Session = Depends(get_db),
):
    stmt = select(Memory).order_by(Memory.updated_at.desc())
    if kind != "all":
        stmt = stmt.where(Memory.kind == kind)
    return db.scalars(stmt).all()


def _run_user_tool(c: Container, db: Session, name: str, payload: dict) -> dict:
    outcome = c.executor.run_as_user(_user_ctx(c, db), name, payload)
    if outcome.status != "ok":
        raise HTTPException(status_code=422, detail=outcome.result.get("error", "Failed."))
    return outcome.result


@router.patch("/memories/{memory_id}", response_model=schemas.MemoryOut)
def update_memory(memory_id: int, body: schemas.MemoryUpdate, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(Memory, memory_id) is None:
        raise NotFoundError(f"No memory with id {memory_id}.")
    changes = body.model_dump(exclude_unset=True)
    _run_user_tool(c, db, "update_memory", {"memory_id": memory_id, **changes})
    return db.get(Memory, memory_id)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    # The user is the one asking, so no approval queue -- but it is audited with actor="user".
    if db.get(Memory, memory_id) is None:
        raise NotFoundError(f"No memory with id {memory_id}.")
    _run_user_tool(c, db, "delete_memory", {"memory_id": memory_id})


@router.patch("/commitments/{commitment_id}", response_model=schemas.CommitmentOut)
def update_commitment(
    commitment_id: int, body: schemas.CommitmentUpdate, db: Session = Depends(get_db), c: Container = Depends(get_container)
):
    if db.get(Commitment, commitment_id) is None:
        raise NotFoundError(f"No commitment with id {commitment_id}.")
    changes = body.model_dump(exclude_unset=True)
    mark_done = changes.get("status") == "done"
    if mark_done:
        changes.pop("status")
    if changes:
        _run_user_tool(c, db, "update_commitment", {"commitment_id": commitment_id, **changes})
    if mark_done:
        _run_user_tool(c, db, "complete_commitment", {"commitment_id": commitment_id})
    if not changes and not mark_done:
        raise HTTPException(status_code=422, detail="No fields to update were provided.")
    db.expire_all()
    return db.get(Commitment, commitment_id)


@router.get("/pending-actions", response_model=list[schemas.PendingActionOut])
def pending_actions(db: Session = Depends(get_db), c: Container = Depends(get_container)):
    c.executor.expire_stale(db)  # sweep before listing so an expired action never shows as actionable
    return db.scalars(select(PendingAction).where(PendingAction.status == "pending").order_by(PendingAction.id)).all()


def _user_ctx(c: Container, db: Session) -> ToolContext:
    return ToolContext(db=db, tz=ZoneInfo(c.settings.agent_timezone))


@router.post("/pending-actions/{pending_id}/confirm", response_model=schemas.ConfirmResponse)
def confirm_action(pending_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    outcome = c.executor.confirm(_user_ctx(c, db), pending_id)
    return schemas.ConfirmResponse(status=outcome.status, result=outcome.result)


@router.post("/pending-actions/{pending_id}/reject", response_model=schemas.PendingActionOut)
def reject_action(pending_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    return c.executor.reject(_user_ctx(c, db), pending_id)


@router.get("/audit-log", response_model=list[schemas.AuditLogOut])
def audit_log(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    return db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()


@router.get("/tasks/{task_id}/events", response_model=list[schemas.TaskEventOut])
def task_events(task_id: int, db: Session = Depends(get_db)):
    if db.get(Task, task_id) is None:
        raise NotFoundError(f"No task with id {task_id}.")
    return db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.at, TaskEvent.id)).all()


@router.get("/plan", response_model=schemas.PlanOut)
def plan(plan_date: date | None = Query(None, alias="date"), db: Session = Depends(get_db), c: Container = Depends(get_container)):
    day = plan_date or datetime.now(ZoneInfo(c.settings.agent_timezone)).date()
    return plan_dict(get_plan(db, day), day)


@router.get("/accountability")
def accountability(window_days: int = Query(7, ge=1, le=60), db: Session = Depends(get_db), c: Container = Depends(get_container)):
    tz = ZoneInfo(c.settings.agent_timezone)
    return accountability_svc.accountability_report(db, datetime.now(tz).date(), tz, window_days=window_days)


@router.get("/work-sessions", response_model=list[schemas.WorkSessionOut])
def work_sessions(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    return db.scalars(select(WorkSession).order_by(WorkSession.started_at.desc()).limit(limit)).all()


@router.post("/tasks/{task_id}/complete", response_model=schemas.TaskOut)
def complete_task(task_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(Task, task_id) is None:
        raise NotFoundError(f"No task with id {task_id}.")
    _run_user_tool(c, db, "complete_task", {"task_id": task_id})
    db.expire_all()
    return db.get(Task, task_id)


@router.patch("/plan-items/{item_id}", response_model=schemas.PlanItemOut)
def update_plan_item(item_id: int, body: schemas.PlanItemUpdate, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(PlanItem, item_id) is None:
        raise NotFoundError(f"No plan item with id {item_id}.")
    _run_user_tool(c, db, "update_plan_item", {"item_id": item_id, **body.model_dump(exclude_unset=True)})
    db.expire_all()
    return db.get(PlanItem, item_id)


@router.get("/reminders", response_model=list[schemas.ReminderOut])
def reminders(status: Literal["pending", "sent", "cancelled", "failed", "all"] = "pending", db: Session = Depends(get_db)):
    stmt = select(Reminder).order_by(Reminder.fire_at)
    if status != "all":
        stmt = stmt.where(Reminder.status == status)
    return db.scalars(stmt.limit(200)).all()


@router.post("/reminders/{reminder_id}/cancel", response_model=schemas.ReminderOut)
def cancel_reminder(reminder_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(Reminder, reminder_id) is None:
        raise NotFoundError(f"No reminder with id {reminder_id}.")
    _run_user_tool(c, db, "cancel_reminder", {"reminder_id": reminder_id})
    db.expire_all()
    return db.get(Reminder, reminder_id)


@router.get("/checkins", response_model=list[schemas.CheckinOut])
def checkins(limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    return db.scalars(select(Checkin).order_by(Checkin.id.desc()).limit(limit)).all()


@router.post("/checkins/{checkin_id}/response", response_model=schemas.CheckinOut)
def checkin_response(checkin_id: int, body: schemas.CheckinResponseIn, db: Session = Depends(get_db)):
    ci = db.get(Checkin, checkin_id)
    if ci is None:
        raise NotFoundError(f"No check-in with id {checkin_id}.")
    ci.user_response = body.text
    db.commit()
    return ci


@router.get("/notifications", response_model=list[schemas.NotificationOut])
def notifications(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    return db.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)).all()


@router.post("/scheduler/tick")
def scheduler_tick(c: Container = Depends(get_container)):
    """Run one scheduler pass now (what the background job does every minute). Handy for testing delivery."""
    return c.scheduler.tick()
