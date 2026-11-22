from __future__ import annotations

import hmac
import ipaddress
import threading
from collections.abc import Iterator
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import func, select
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
    utcnow,
)
from app.errors import ConflictError, NotFoundError
from app.services import accountability as accountability_svc
from app.services import analytics as analytics_svc
from app.services import connections as connections_svc
from app.services.briefing import briefing_text, build_briefing, calendar_events, calendar_warnings
from app.services.calendar_view import MAX_DAYS, build_calendar
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


@router.get("/opportunities/{opportunity_id}/eligibility")
def opportunity_eligibility(opportunity_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.services.application_help import eligibility_report

    o = db.get(Opportunity, opportunity_id)
    if o is None:
        raise NotFoundError(f"No opportunity with id {opportunity_id}.")
    return eligibility_report(o, list(db.scalars(select(Memory))), datetime.now(ZoneInfo(c.settings.agent_timezone)).date())


@router.get("/opportunities/{opportunity_id}/readiness")
def opportunity_readiness(opportunity_id: int, db: Session = Depends(get_db)):
    from app.services.application_help import readiness

    o = db.get(Opportunity, opportunity_id)
    if o is None:
        raise NotFoundError(f"No opportunity with id {opportunity_id}.")
    from app.tools.files import documents_linked_to

    return readiness(o, list(db.scalars(select(Document))), list(db.scalars(select(ApplicationDraft))),
                     documents_linked_to(db, "opportunity", str(o.id)))


@router.delete("/opportunities/{opportunity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_opportunity(opportunity_id: int, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    if db.get(Opportunity, opportunity_id) is None:
        raise NotFoundError(f"No opportunity with id {opportunity_id}.")
    _run_user_tool(c, db, "delete_opportunity", {"opportunity_id": opportunity_id})


@router.get("/document-links")
def all_document_links(db: Session = Depends(get_db)):
    """Every document's connections, by path: applications, tasks, emails and projects."""
    from app.db.models import DocumentLink, Project, ProjectLink

    out: dict[str, list[dict]] = {}
    for link in db.scalars(select(DocumentLink).order_by(DocumentLink.id)):
        out.setdefault(link.path, []).append({"id": link.id, "to": link.target_type, "title": link.title, "note": link.note})
    for link in db.scalars(select(ProjectLink).where(ProjectLink.kind == "note").order_by(ProjectLink.id)):
        proj = db.get(Project, link.project_id)
        out.setdefault(link.target, []).append({"id": f"p{link.id}", "to": "project", "title": proj.name if proj else None, "note": link.title})
    return out


@router.delete("/document-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document_link(link_id: str, db: Session = Depends(get_db)):
    """Remove one connection (the file itself is untouched). Project links have ids like 'p3'."""
    from app.db.models import DocumentLink, ProjectLink

    row = db.get(ProjectLink, int(link_id[1:])) if link_id.startswith("p") and link_id[1:].isdigit() else \
        db.get(DocumentLink, int(link_id)) if link_id.isdigit() else None
    if row is None:
        raise NotFoundError(f"No link {link_id}.")
    db.delete(row)
    db.commit()


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


# ---------- calendar view (read-only: your calendar's events next to Keel's own dates)
@router.get("/calendar")
def calendar_view(start: date | None = None, days: int = Query(7, ge=1, le=MAX_DAYS),
                  db: Session = Depends(get_db), c: Container = Depends(get_container)):
    tz = ZoneInfo(c.settings.agent_timezone)
    now = datetime.now(tz)
    workday = (parse_hhmm(c.settings.workday_start), parse_hhmm(c.settings.workday_end))
    return build_calendar(db, c.calendar_source, start or now.date(), days, tz, workday, now)


# ---------- connected services (never returns a key, token, password or the calendar's secret address)
@router.get("/services")
def services(db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.services import switches as switches_svc

    indexed = db.scalar(select(func.count(Document.id))) if c.settings.notes_dir else None
    rows = connections_svc.describe(c.settings, indexed)
    off = switches_svc.off_switches(db)
    for r in rows:  # an on/off switch on each row that has one and is set up (a switch can't turn on what .env hasn't)
        sid = "messages" if r["id"] == "scheduler" else r["id"]
        if sid in switches_svc.SWITCHES and r["status"] != "not_set_up":
            r["switch"] = {"id": sid, "label": switches_svc.SWITCHES[sid], "on": sid not in off}
    return rows


@router.put("/switches/{switch}")
def set_switch(switch: str, body: dict, db: Session = Depends(get_db)):
    """Turn an integration off (or back on). Only the dashboard can do this: there is no tool for it."""
    from app.services import switches as switches_svc

    if switch not in switches_svc.SWITCHES:
        raise NotFoundError(f"No switch '{switch}'.")
    if not isinstance(body.get("on"), bool):
        raise HTTPException(422, 'Send {"on": true} or {"on": false}.')
    switches_svc.set_switch(db, switch, body["on"])
    return {"id": switch, "on": body["on"]}


@router.post("/services/{service}/test", dependencies=[Depends(rate_limit)])
def test_service(service: str, c: Container = Depends(get_container)):
    """One read-only request to the service. Never sends a message (email logs in and hangs up)."""
    if service not in connections_svc.CHECKS:
        raise NotFoundError(f"No live check for '{service}'.")
    return {"service": service, **connections_svc.run_check(service, c.settings), "checked_at": datetime.now(ZoneInfo(c.settings.agent_timezone)).isoformat(timespec="seconds")}


# ---------- encrypted backups (the passphrase never leaves .env; restoring is CLI-only, with Keel stopped)
_backup_lock = threading.Lock()


@router.get("/backups")
def list_backups(c: Container = Depends(get_container)):
    from app.services import backups as backups_svc

    s = c.settings
    try:
        folder = backups_svc.backup_dir(s)
    except backups_svc.BackupError:
        return {"enabled": False, "reason": "Backups need a file-based SQLite database.", "backups": []}
    return {"enabled": bool(s.backup_passphrase), "daily": bool(s.backup_daily and s.scheduler_enabled and s.backup_passphrase),
            "time": s.backup_time, "keep": s.backup_keep, "includes_notes": bool(s.backup_include_notes and s.notes_dir),
            "folder": str(folder), "backups": [b.__dict__ for b in backups_svc.list_backups(folder)]}


@router.post("/backups", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit)])
def create_backup(c: Container = Depends(get_container)):
    from app.services import backups as backups_svc

    if not c.settings.backup_passphrase:
        raise ConflictError("Set BACKUP_PASSPHRASE in .env to turn on encrypted backups.")
    if not _backup_lock.acquire(blocking=False):
        raise ConflictError("A backup is already running.")
    try:
        target = backups_svc.backup_now(c.settings)
    except backups_svc.BackupError as e:
        raise ConflictError(str(e)) from None
    finally:
        _backup_lock.release()
    return {"name": target.name, "size": target.stat().st_size}


# ---------- retention rules (logs and history only; what you created yourself is deleted only by you)
@router.get("/retention")
def get_retention(db: Session = Depends(get_db)):
    from app.services import retention

    saved = retention.rules(db)
    return {"categories": [{"id": c.id, "label": c.label, "min_days": c.min_days, "note": c.note, "days": saved[c.id]}
                           for c in retention.CATEGORIES.values()], "last_run": retention.last_run(db)}


def _rules_body(body: dict) -> dict[str, int | None]:
    from app.services import retention

    out = {}
    for cat, days in body.items():
        if cat not in retention.CATEGORIES:
            raise HTTPException(422, f"Unknown category '{cat}'.")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool)):
            raise HTTPException(422, f"{cat}: days must be a whole number or null (keep forever).")
        c = retention.CATEGORIES[cat]
        if days is not None and not (c.min_days <= days <= retention.MAX_DAYS):
            raise HTTPException(422, f"{c.label}: keep at least {c.min_days} days (or forever).")
        out[cat] = days
    return out


@router.put("/retention")
def put_retention(body: dict, db: Session = Depends(get_db)):
    from app.services import retention

    for cat, days in _rules_body(body).items():
        retention.set_rule(db, cat, days)
    return get_retention(db)


@router.post("/retention/preview")
def preview_retention(body: dict | None = None, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    """What the saved rules (or the rules in the body, not saved yet) would delete now. Deletes nothing."""
    from app.services import retention

    rules = {**retention.rules(db), **_rules_body(body or {})}
    return {"would_delete": retention.apply(db, notes_dir=c.settings.notes_dir, dry_run=True, only=rules)}


@router.post("/retention/apply")
def apply_retention(db: Session = Depends(get_db), c: Container = Depends(get_container)):
    """Run the saved rules now (the dashboard shows the preview first)."""
    from app.services import retention

    return {"deleted": retention.apply(db, notes_dir=c.settings.notes_dir)}


# ---------- personal onboarding and profile (only the dashboard can change these; there is no tool for it)
@router.get("/profile")
def get_profile(db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.services import profile as profile_svc

    return profile_svc.profile(db, c.settings)


@router.post("/profile")
def post_profile(body: dict, request: Request, db: Session = Depends(get_db)):
    """{"answers": {...}, "settings": {...}, "state": "done" | "skipped"}. Settings are checked before anything is saved."""
    from app.api.main import reload_container
    from app.services import profile as profile_svc

    settings = body.get("settings") or {}
    answers = body.get("answers") or {}
    state = body.get("state")
    if not isinstance(settings, dict) or not isinstance(answers, dict) or state not in (None, "done", "skipped"):
        raise HTTPException(422, "Send answers and settings as objects, and state as done or skipped.")
    try:
        clean = {name: profile_svc.PERSONAL_SETTINGS[name](v) for name, v in settings.items() if name in profile_svc.PERSONAL_SETTINGS}
        unknown = sorted(set(settings) - set(profile_svc.PERSONAL_SETTINGS))
        if unknown:
            raise ValueError(f"{', '.join(unknown)} can't be changed here.")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    saved = profile_svc.save_profile(db, answers)
    if clean:
        profile_svc.save_settings(db, clean)
    if state:
        profile_svc.set_state(db, state)
    if clean:
        reload_container(request.app)
    return {"saved": saved, "settings_changed": sorted(clean), **profile_svc.profile(db, request.app.state.container.settings)}


# ---------- capture inbox (rule-based suggestions; nothing is filed until the user approves it here)
def _inbox_dict(item) -> dict:
    return {"id": item.id, "text": item.text, "status": item.status, "suggestion": item.suggestion, "filed_as": item.filed_as,
            "created_at": item.created_at.isoformat(timespec="seconds")}


def _capture_context(db: Session, c: Container):
    from app.db.models import Project

    projects = [(p.id, p.name) for p in db.scalars(select(Project).where(Project.status.in_(("active", "paused"))).order_by(Project.id))]
    return projects, c.registry.get("create_note") is not None


@router.get("/inbox")
def list_inbox(db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.db.models import InboxItem

    new = db.scalars(select(InboxItem).where(InboxItem.status == "new").order_by(InboxItem.id.desc())).all()
    recent = db.scalars(select(InboxItem).where(InboxItem.status != "new").order_by(InboxItem.id.desc()).limit(10)).all()
    projects, notes = _capture_context(db, c)
    return {"new": [_inbox_dict(i) for i in new], "recent": [_inbox_dict(i) for i in recent],
            "projects": [{"id": pid, "name": name} for pid, name in projects], "notes": notes}


@router.post("/inbox", status_code=status.HTTP_201_CREATED)
def capture(body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    from app.db.models import InboxItem
    from app.services.capture import suggest

    text = " ".join(str(body.get("text") or "").split())
    if not text or len(text) > 2000:
        raise HTTPException(422, "Type something (up to 2,000 characters).")
    projects, notes = _capture_context(db, c)
    item = InboxItem(text=text, suggestion=suggest(text, datetime.now(ZoneInfo(c.settings.agent_timezone)), projects, notes=notes))
    db.add(item)
    db.commit()
    return _inbox_dict(item)


def _filing(kind: str, fields: dict, c: Container) -> tuple[str, dict]:
    """The tool and arguments that file an item. Only the fields each tool needs are passed on."""
    from app.services.capture import note_path

    def get(name, default=None):
        v = fields.get(name, default)
        return v.strip() if isinstance(v, str) else v

    if kind == "task":
        args = {"title": get("title"), "due_date": get("due_date") or None, "project_id": get("project_id") or None}
        return "create_task", {k: v for k, v in args.items() if v is not None}
    if kind == "reminder":
        return "create_reminder", {"text": get("text"), "remind_at": get("remind_at")}
    if kind == "memory":
        return "save_memory", {"content": get("content"), "kind": get("kind") or "fact"}
    if kind == "note":
        title = get("title") or "Note"
        now = datetime.now(ZoneInfo(c.settings.agent_timezone))
        return "create_note", {"path": note_path(title, now), "content": f"# {title}\n\n{get('content') or title}\n"}
    if kind == "project_update":
        return "log_project_entry", {"project_id": get("project_id"), "kind": get("kind") or "update", "text": get("text")}
    raise HTTPException(422, f"Unknown type '{kind}'.")


def _filed_label(kind: str, result: dict) -> str:
    for key in ("task", "reminder", "memory", "entry"):
        if isinstance(result.get(key), dict) and result[key].get("id") is not None:
            return f"{kind.replace('_', ' ')} #{result[key]['id']}"
    return f"note {result['created']}" if result.get("created") else kind


@router.post("/inbox/{item_id}/file")
def file_inbox_item(item_id: int, body: dict, db: Session = Depends(get_db), c: Container = Depends(get_container)):
    """File it as the type and fields the user approved (Keel's suggestion or their correction), through the same tool
    chat uses: validated, audited as the user, and refused if that integration is switched off."""
    from app.db.models import InboxItem

    item = db.get(InboxItem, item_id)
    if item is None:
        raise NotFoundError(f"No inbox item {item_id}.")
    if item.status != "new":
        raise ConflictError("This item was already filed or dismissed.")
    kind, fields = body.get("kind"), body.get("fields") or {}
    if not isinstance(fields, dict):
        raise HTTPException(422, "fields must be an object.")
    tool, args = _filing(str(kind), fields, c)
    if c.registry.get(tool) is None:
        raise ConflictError("Notes need a notes folder (NOTES_DIR)." if kind == "note" else f"'{tool}' isn't available.")
    out = c.executor.run_as_user(ToolContext(db=db, tz=ZoneInfo(c.settings.agent_timezone)), tool, args)
    if out.status != "ok":
        db.rollback()
        raise HTTPException(422, str(out.result.get("error") or out.result)[:300])
    item = db.get(InboxItem, item_id)
    item.status, item.filed_as, item.filed_at = "filed", _filed_label(kind, out.result), utcnow()
    db.commit()
    return _inbox_dict(item)


@router.post("/inbox/{item_id}/dismiss")
def dismiss_inbox_item(item_id: int, db: Session = Depends(get_db)):
    from app.db.models import InboxItem

    item = db.get(InboxItem, item_id)
    if item is None:
        raise NotFoundError(f"No inbox item {item_id}.")
    if item.status == "new":
        item.status = "dismissed"
        db.commit()
    return _inbox_dict(item)
