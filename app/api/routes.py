from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Iterator
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import schemas
from app.container import Container
from app.db.models import (
    ApplicationDraft,
    AuditLog,
    Checkin,
    Commitment,
    Conversation,
    Document,
    Goal,
    Memory,
    Message,
    Notification,
    Opportunity,
    PendingAction,
    PlanItem,
    Project,
    Reminder,
    Task,
    TaskEvent,
    WorkSession,
)
from app.errors import ConflictError, NotFoundError
from app.services import accountability as accountability_svc
from app.services import analytics as analytics_svc
from app.services.briefing import briefing_text, build_briefing, calendar_events, calendar_warnings
from app.services.ics_export import plan_to_ics
from app.services.notifications import parse_hhmm
from app.tools.base import ToolContext
from app.tools.memory import needs_review as memory_needs_review
from app.tools.planning import get_plan, plan_dict


def get_container(request: Request) -> Container:
    return request.app.state.container


_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")
_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(value: str) -> str:
    """'localhost:8000' -> 'localhost', '[::1]:8000' -> '::1', 'http://x:5173' -> 'x'. Lower-cased."""
    value = value.strip().lower().split("://", 1)[-1].split("/", 1)[0]
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _browser_problem(request: Request) -> str | None:
    """Why a keyless request may have been sent by a website open in your browser, or None if it looks local.

    - DNS rebinding: a hostile domain re-pointed at 127.0.0.1 makes the browser treat this API as that domain's own
      site, able to read every response. The Host header still carries the hostile name, so only local names pass.
    - CSRF: any website may send a "simple" POST to 127.0.0.1 without asking (no-cors). Browsers label it with
      Sec-Fetch-Site and Origin, so state-changing requests from another site are refused. Tools like curl and the CLI
      send neither header and are unaffected.
    """
    if _hostname(request.headers.get("host", "")) not in _LOCAL_NAMES:
        return "unexpected Host header"
    if request.method not in _SAFE_METHODS:
        if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}:
            return "cross-site request"
        origin = request.headers.get("origin")
        if origin is not None and _hostname(origin) not in _LOCAL_NAMES:
            return "cross-site request"
    return None


def _direct_local(request: Request) -> bool:
    """True for a request from a loopback address that did not come through a proxy (proxies connect from localhost)."""
    host = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
    return loopback and not any(h in request.headers for h in _PROXY_HEADERS)


def require_api_key(
    request: Request, c: Container = Depends(get_container), x_api_key: str | None = Header(default=None, alias="X-API-Key")
) -> None:
    """Guards every /api/* route.

    With API_KEY set, every request must carry it (a website can't add that header: it doesn't know the key). Without
    one, the API answers only requests made directly from this machine, to a local host name, and not sent cross-site
    by a website open in your browser: a server started with --host 0.0.0.0, put behind a reverse proxy, or reached
    by DNS rebinding or CSRF refuses them until a key is set, instead of quietly exposing your data.
    """
    configured = c.settings.api_key
    if configured is None:
        if not _direct_local(request):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="No API_KEY is set, so the API only accepts requests from this machine. Set API_KEY to allow remote access.")
        problem = _browser_problem(request)
        if problem:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Refused ({problem}): without API_KEY, only the dashboard at http://127.0.0.1 or "
                                       "http://localhost and local tools may use the API. Set API_KEY to use another host name.")
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key, configured):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid X-API-Key header.")


router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


def rate_limit(request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Dependency for endpoints that spend LLM tokens. Keyed by client address (+ API key, if any)."""
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is None:
        return
    client = request.client.host if request.client else "unknown"
    wait = limiter.check(f"{client}|{x_api_key or ''}")
    if wait is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests; slow down.",
            headers={"Retry-After": str(int(wait) + 1)},
        )


def get_db(container: Container = Depends(get_container)) -> Iterator[Session]:
    with container.session_factory() as db:
        yield db


@router.post("/chat", response_model=schemas.ChatResponse, dependencies=[Depends(rate_limit)])
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
    from app.services.project_report import open_blockers, task_progress

    stmt = select(Project).order_by(Project.deadline.is_(None), Project.deadline, Project.id)
    if status != "all":
        stmt = stmt.where(Project.status == status)
    blocked: dict[int, int] = {}
    for e in open_blockers(db):
        blocked[e.project_id] = blocked.get(e.project_id, 0) + 1
    out = []
    for p in db.scalars(stmt):
        row = schemas.ProjectOut.model_validate(p)
        row.progress, row.open_blockers = task_progress(db, p.id), blocked.get(p.id, 0)
        out.append(row)
    return out


def _project_or_404(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise NotFoundError(f"No project with id {project_id}.")
    return p


@router.get("/projects/{project_id}/report")
def project_report(project_id: int, days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.services.project_report import build_report

    tz = ZoneInfo(c.settings.agent_timezone)
    return build_report(db, _project_or_404(db, project_id), datetime.now(tz).date(), tz, days)


@router.post("/projects/{project_id}/entries", status_code=status.HTTP_201_CREATED)
def add_project_entry(project_id: int, body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    _project_or_404(db, project_id)
    return _run_user_tool(c, db, "log_project_entry", {**body, "project_id": project_id})


@router.post("/project-entries/{entry_id}/resolve")
def resolve_project_blocker(entry_id: int, body: dict | None = None, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    return _run_user_tool(c, db, "resolve_blocker", {**(body or {}), "entry_id": entry_id})


@router.post("/projects/{project_id}/links", status_code=status.HTTP_201_CREATED)
def add_project_link(project_id: int, body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    _project_or_404(db, project_id)
    return _run_user_tool(c, db, "link_project", {**body, "project_id": project_id})


@router.delete("/project-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_link(link_id: int, db: Session = Depends(get_db)):
    from app.db.models import ProjectLink

    link = db.get(ProjectLink, link_id)
    if link is None:
        raise NotFoundError(f"No link with id {link_id}.")
    db.delete(link)
    db.commit()


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


def _memory_out(m: Memory) -> schemas.MemoryOut:
    out = schemas.MemoryOut.model_validate(m)
    out.needs_review = memory_needs_review(m)
    return out


@router.get("/memories", response_model=list[schemas.MemoryOut])
def memories(
    kind: Literal["preference", "context", "fact", "decision", "all"] = "all",
    category: Literal["personal", "academic", "professional", "project", "general", "all"] = "all",
    db: Session = Depends(get_db),
):
    stmt = select(Memory).order_by(Memory.updated_at.desc())
    if kind != "all":
        stmt = stmt.where(Memory.kind == kind)
    if category != "all":
        stmt = stmt.where(Memory.category == category)
    return [_memory_out(m) for m in db.scalars(stmt)]


@router.get("/memories/review", response_model=schemas.MemoryReview)
def memories_to_review(db: Session = Depends(get_db)):
    """Memories not confirmed for a long time, and profile topics recorded more than once."""
    every = list(db.scalars(select(Memory).order_by(Memory.id)))
    by_topic: dict[str, list[Memory]] = {}
    for m in every:
        if m.topic:
            by_topic.setdefault(m.topic.lower(), []).append(m)
    return schemas.MemoryReview(
        stale=[_memory_out(m) for m in every if memory_needs_review(m)],
        conflicts=[[_memory_out(m) for m in ms] for ms in by_topic.values() if len(ms) > 1],
    )


def _run_user_tool(c: Container, db: Session, name: str, payload: dict) -> dict:
    outcome = c.executor.run_as_user(_user_ctx(c, db), name, payload)
    if outcome.status != "ok":
        raise HTTPException(status_code=422, detail=outcome.result.get("error", "Failed."))
    return outcome.result


@router.post("/memories", response_model=schemas.MemoryOut, status_code=status.HTTP_201_CREATED)
def create_memory(body: schemas.MemoryCreate, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    """Add a memory yourself (source "user"). A topic that already has a value is refused: edit that memory instead."""
    result = _run_user_tool(c, db, "save_memory", body.model_dump())
    if result.get("conflict"):
        raise HTTPException(status_code=409, detail=result["message"])
    return _memory_out(db.get(Memory, result["memory"]["id"]))


@router.patch("/memories/{memory_id}", response_model=schemas.MemoryOut)
def update_memory(memory_id: int, body: schemas.MemoryUpdate, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(Memory, memory_id) is None:
        raise NotFoundError(f"No memory with id {memory_id}.")
    changes = body.model_dump(exclude_unset=True)
    _run_user_tool(c, db, "update_memory", {"memory_id": memory_id, **changes})
    db.expire_all()
    return _memory_out(db.get(Memory, memory_id))


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


@router.get("/plan.ics")
def plan_ics(plan_date: date | None = Query(None, alias="date"), db: Session = Depends(get_db), c: Container = Depends(get_container)):
    """The day's time blocks as a calendar file, to import into Google Calendar yourself (Keel never writes to it)."""
    tz = ZoneInfo(c.settings.agent_timezone)
    day = plan_date or datetime.now(tz).date()
    return Response(
        plan_to_ics(get_plan(db, day), day, tz), media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="keel-plan-{day.isoformat()}.ics"'},
    )


@router.get("/briefing")
def briefing(db: Session = Depends(get_db), c: Container = Depends(get_container)):
    """Today at a glance, plus today's calendar (titles cleaned, links removed). The dashboard shows it as plain text."""
    tz = ZoneInfo(c.settings.agent_timezone)
    today = datetime.now(tz).date()
    facts = build_briefing(db, today)
    workday = (parse_hhmm(c.settings.workday_start), parse_hhmm(c.settings.workday_end))
    facts["calendar_warnings"] = calendar_warnings(db, c.calendar_source, today, tz, workday)
    events = calendar_events(c.calendar_source, today, tz)
    return {**facts, "calendar": events, "text": briefing_text(facts, events)}


# ---------- applications (Keel prepares; the user submits)
@router.get("/opportunities")
def opportunities(status_filter: Literal["active", "all"] = Query("active", alias="status"), db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.tools.applications import opportunity_dict

    today = datetime.now(ZoneInfo(c.settings.agent_timezone)).date()
    stmt = select(Opportunity)
    if status_filter == "active":
        stmt = stmt.where(Opportunity.status.notin_(("accepted", "rejected", "withdrawn")))
    rows = sorted(db.scalars(stmt), key=lambda o: (o.deadline is None, o.deadline or date.max, o.id))
    drafts: dict[int, list[dict]] = {}
    for d in db.scalars(select(ApplicationDraft).order_by(ApplicationDraft.updated_at.desc())):
        drafts.setdefault(d.opportunity_id or 0, []).append({"id": d.id, "kind": d.kind, "title": d.title, "content": d.content})
    return [{**opportunity_dict(o, today), "eligibility": o.eligibility, "notes": o.notes, "drafts": drafts.get(o.id, [])} for o in rows]


@router.post("/opportunities", status_code=status.HTTP_201_CREATED)
def add_opportunity(body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    return _run_user_tool(c, db, "add_opportunity", body)


@router.patch("/opportunities/{opportunity_id}")
def update_opportunity(opportunity_id: int, body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(Opportunity, opportunity_id) is None:
        raise NotFoundError(f"No opportunity with id {opportunity_id}.")
    return _run_user_tool(c, db, "update_opportunity", {**body, "opportunity_id": opportunity_id})


@router.delete("/opportunities/{opportunity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_opportunity(opportunity_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(Opportunity, opportunity_id) is None:
        raise NotFoundError(f"No opportunity with id {opportunity_id}.")
    _run_user_tool(c, db, "delete_opportunity", {"opportunity_id": opportunity_id})


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(draft_id: int, db: Session = Depends(get_db)):
    d = db.get(ApplicationDraft, draft_id)
    if d is None:
        raise NotFoundError(f"No draft with id {draft_id}.")
    db.delete(d)
    db.commit()


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


@router.post("/scheduler/tick", dependencies=[Depends(rate_limit)])
def scheduler_tick(c: Container = Depends(get_container)):
    """Run one scheduler pass now (what the background job does every minute). Handy for testing delivery."""
    return c.scheduler.tick()


@router.get("/documents", response_model=list[schemas.DocumentOut])
def documents(db: Session = Depends(get_db)):
    return db.scalars(select(Document).order_by(Document.path)).all()


@router.post("/documents/index")
def index_documents(c: Container = Depends(get_container)):
    """Re-scan NOTES_DIR (incremental). Run after adding or editing notes."""
    from app.services.documents import NotesUnavailable

    try:
        return c.index_notes()
    except NotesUnavailable as exc:
        raise ConflictError(str(exc)) from None


@router.post("/documents/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(request: Request, name: str = Query(min_length=1, max_length=255), c: Container = Depends(get_container)):
    """Upload a PDF, Word (.docx) or text file (raw body, file name in ?name=). It is saved in NOTES_DIR/uploads and indexed."""
    from pathlib import Path

    from app.services.documents import MAX_FILE_BYTES, NotesUnavailable
    from app.services.uploads import UploadRejected, save_upload

    if not c.settings.notes_dir:
        raise ConflictError("Set NOTES_DIR to choose where uploaded documents are kept.")
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_FILE_BYTES // 1_000_000} MB.")
    from starlette.concurrency import run_in_threadpool

    def store() -> tuple[str, dict]:  # disk and database work off the event loop
        rel = save_upload(Path(c.settings.notes_dir), name, bytes(data))
        return rel, c.index_notes()

    try:
        rel, report = await run_in_threadpool(store)
    except UploadRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except NotesUnavailable as exc:
        raise ConflictError(str(exc)) from None
    with c.session_factory() as db:
        doc = db.scalar(select(Document).where(Document.path == rel))
        if doc is None:  # saved but the indexer couldn't read it
            from app.services.uploads import delete_upload

            delete_upload(Path(c.settings.notes_dir), rel)
            raise HTTPException(status_code=422, detail="The file could not be read: " + "; ".join(report.get("skipped", [])[:3]))
        return schemas.DocumentOut.model_validate(doc)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, c: Container = Depends(get_container)):
    """Remove a document you uploaded from the dashboard (files you put in NOTES_DIR yourself stay yours to manage)."""
    from pathlib import Path

    from app.services.uploads import UploadRejected, delete_upload

    with c.session_factory() as db:
        doc = db.get(Document, document_id)
        if doc is None:
            raise NotFoundError(f"No document with id {document_id}.")
        path = doc.path
    if not c.settings.notes_dir:
        raise ConflictError("NOTES_DIR is not set.")
    try:
        delete_upload(Path(c.settings.notes_dir), path)
    except UploadRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    c.index_notes()


# ---------- workflows ("when X, do Y"); approving a paused run is only possible here, never from the model
def _wf_now(c: Container) -> datetime:
    return datetime.now(ZoneInfo(c.settings.agent_timezone))


@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.db.models import Workflow, WorkflowRun
    from app.services.workflows import run_dict, workflow_dict

    tz = ZoneInfo(c.settings.agent_timezone)
    runs = db.scalars(select(WorkflowRun).order_by(WorkflowRun.id.desc()).limit(100)).all()
    out = []
    for wf in db.scalars(select(Workflow).order_by(Workflow.id)):
        out.append({**workflow_dict(wf), "runs": [run_dict(r, tz) for r in runs if r.workflow_id == wf.id][:10]})
    return out


@router.get("/workflow-templates")
def workflow_templates():
    from app.services.workflows import TEMPLATES, describe

    return [{**t, "description": describe(t["trigger"], t["steps"])} for t in TEMPLATES]


@router.post("/workflows", status_code=status.HTTP_201_CREATED)
def create_workflow(body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    """The user creating a workflow from the dashboard (same validation as the chat tool; no approval needed: it's them)."""
    return _run_user_tool(c, db, "create_workflow", body)


@router.patch("/workflows/{workflow_id}")
def update_workflow(workflow_id: int, body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.db.models import Workflow

    if db.get(Workflow, workflow_id) is None:
        raise NotFoundError(f"No workflow with id {workflow_id}.")
    return _run_user_tool(c, db, "set_workflow_enabled", {"workflow_id": workflow_id, "enabled": bool(body.get("enabled"))})


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(workflow_id: int, db: Session = Depends(get_db)):
    from app.db.models import Workflow

    wf = db.get(Workflow, workflow_id)
    if wf is None:
        raise NotFoundError(f"No workflow with id {workflow_id}.")
    db.delete(wf)
    db.commit()


@router.post("/workflows/{workflow_id}/run")
def run_workflow_now(workflow_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.db.models import Workflow

    if db.get(Workflow, workflow_id) is None:
        raise NotFoundError(f"No workflow with id {workflow_id}.")
    return _run_user_tool(c, db, "run_workflow", {"workflow_id": workflow_id})


@router.post("/workflow-runs/{run_id}/{decision}")
def decide_workflow_run(run_id: int, decision: Literal["approve", "cancel", "retry"], db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.db.models import WorkflowRun
    from app.services.workflows import run_dict

    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise NotFoundError(f"No workflow run with id {run_id}.")
    eng, now = c.workflows, _wf_now(c)
    try:
        if decision == "approve":
            eng.approve(db, run, now)
        elif decision == "cancel":
            eng.cancel(db, run)
        else:
            eng.retry(db, run, now)
    except ValueError as exc:
        raise ConflictError(str(exc)) from None
    return run_dict(run, ZoneInfo(c.settings.agent_timezone))


@router.get("/analytics")
def analytics(weeks: int = Query(8, ge=2, le=26), db: Session = Depends(get_db), c: Container = Depends(get_container)):
    tz = ZoneInfo(c.settings.agent_timezone)
    return analytics_svc.analytics(db, datetime.now(tz).date(), tz, weeks=weeks)
