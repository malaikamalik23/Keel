from __future__ import annotations

import hmac
from datetime import date
from typing import Iterator, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.container import Container
from app.db.models import AuditLog, Commitment, Conversation, Goal, Memory, Message, PendingAction, Project, Task
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
