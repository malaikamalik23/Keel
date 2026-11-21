from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Document, Project, ProjectEntry, ProjectLink, utcnow
from app.integrations.github import GitHubError, validate_repo
from app.security.untrusted import TAG, sanitize_text
from app.services.project_report import build_report, entry_dict, link_dict
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


def _project_dict(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "purpose": p.purpose,
        "status": p.status,
        "priority": p.priority,
        "deadline": p.deadline.isoformat() if p.deadline else None,
        "goal_id": p.goal_id,
        "repo": p.repo,
    }


def _text(text: str | None, n: int) -> str | None:
    """The log is user data that later reaches prompts and the dashboard: plain text, bounded."""
    if text is None:
        return None
    text = re.sub(rf"<\s*(/?)\s*{TAG}", rf"[\1{TAG}", sanitize_text(text), flags=re.IGNORECASE)  # can't fake a fence
    return text.strip()[:n] or None


def _repo(repo: str | None) -> str | None:
    if not repo:
        return None
    repo = repo.strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    try:
        validate_repo(repo)
    except GitHubError as exc:
        raise ToolError(str(exc)) from None
    return repo


def _find_project(ctx: ToolContext, project_id: int | None, name: str | None) -> Project:
    if project_id is not None:
        return _get_project(ctx, project_id)
    if name:
        q = name.casefold().strip()
        rows = list(ctx.db.scalars(select(Project).where(Project.status != "archived")))
        hits = [r for r in rows if r.name.casefold() == q] or [r for r in rows if q in r.name.casefold() or r.name.casefold() in q]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise ToolError("Several projects match: " + "; ".join(f"{r.id}: {r.name}" for r in hits) + ". Pass project_id.")
    raise ToolError("Which project? Pass project_id or its name (list_projects shows them).")


def _get_project(ctx: ToolContext, project_id: int) -> Project:
    project = ctx.db.get(Project, project_id)
    if project is None:
        raise ToolError(f"No project with id {project_id}.")
    return project


# ---------- create_project ----------
class CreateProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    purpose: str | None = Field(None, description="Why this project matters / what it's for.")
    priority: Literal["low", "medium", "high"] = "medium"
    deadline: date | None = None
    goal_id: int | None = Field(None, description="Link to a goal this project serves, if any.")
    repo: str | None = Field(None, max_length=200, description="GitHub 'owner/name' for its code, if any.")


def create_project(ctx: ToolContext, p: CreateProjectInput) -> dict:
    project = Project(
        name=p.name.strip(), description=p.description, purpose=p.purpose,
        priority=p.priority, deadline=p.deadline, goal_id=p.goal_id, repo=_repo(p.repo),
    )
    ctx.db.add(project)
    ctx.db.flush()
    return {"project": _project_dict(project)}


# ---------- list_projects ----------
class ListProjectsInput(BaseModel):
    status: Literal["active", "paused", "completed", "archived", "all"] = "active"


def list_projects(ctx: ToolContext, p: ListProjectsInput) -> dict:
    stmt = select(Project)
    if p.status != "all":
        stmt = stmt.where(Project.status == p.status)
    projects = list(ctx.db.scalars(stmt.order_by(Project.deadline.is_(None), Project.deadline, Project.id)))
    return {"total": len(projects), "projects": [_project_dict(pr) for pr in projects]}


# ---------- update_project ----------
class UpdateProjectInput(BaseModel):
    project_id: int
    name: str | None = None
    description: str | None = None
    purpose: str | None = None
    status: Literal["active", "paused", "completed", "archived"] | None = None
    priority: Literal["low", "medium", "high"] | None = None
    deadline: date | None = None
    repo: str | None = Field(None, max_length=200, description="GitHub 'owner/name'.")


def update_project(ctx: ToolContext, p: UpdateProjectInput) -> dict:
    project = _get_project(ctx, p.project_id)
    changes = p.model_dump(exclude_unset=True, exclude={"project_id"})
    if not changes:
        raise ToolError("No fields to update were provided.")
    for required in ("name", "status", "priority"):
        if required in changes and changes[required] is None:
            raise ToolError(f"'{required}' cannot be null.")
    if "repo" in changes:
        changes["repo"] = _repo(changes["repo"])
    for field, value in changes.items():
        setattr(project, field, value)
    ctx.db.flush()
    return {"project": _project_dict(project)}


class ProjectRef(BaseModel):
    project_id: int | None = None
    project: str | None = Field(None, max_length=200, description="The project's name, if you don't have its id.")


# ---------- log_project_entry ----------
class LogProjectEntryInput(ProjectRef):
    kind: Literal["update", "blocker", "decision"]
    text: str = Field(min_length=1, max_length=2000)
    why: str | None = Field(None, max_length=2000, description="For a decision: the reason, and options rejected.")


def log_project_entry(ctx: ToolContext, p: LogProjectEntryInput) -> dict:
    project = _find_project(ctx, p.project_id, p.project)
    text = _text(p.text, 2000)
    if not text:
        raise ToolError("The entry is empty.")
    e = ProjectEntry(project_id=project.id, kind=p.kind, text=text, rationale=_text(p.why, 2000))
    ctx.db.add(e)
    ctx.db.flush()
    return {"project": project.name, "entry": entry_dict(e, ctx.tz)}


# ---------- resolve_blocker ----------
class ResolveBlockerInput(BaseModel):
    entry_id: int = Field(description="The blocker's id from project_report.")
    resolution: str | None = Field(None, max_length=1000, description="How it was unblocked.")


def resolve_blocker(ctx: ToolContext, p: ResolveBlockerInput) -> dict:
    e = ctx.db.get(ProjectEntry, p.entry_id)
    if e is None or e.kind != "blocker":
        raise ToolError(f"No blocker with id {p.entry_id}.")
    if e.resolved_at is None:
        e.resolved_at = utcnow()
    if p.resolution:
        e.resolution = _text(p.resolution, 1000)
    ctx.db.flush()
    return {"entry": entry_dict(e, ctx.tz)}


# ---------- link_project ----------
class LinkProjectInput(ProjectRef):
    note_path: str | None = Field(None, max_length=500, description="An indexed note's path (from notes_search).")
    url: str | None = Field(None, max_length=500)
    title: str | None = Field(None, max_length=200)


def link_project(ctx: ToolContext, p: LinkProjectInput) -> dict:
    project = _find_project(ctx, p.project_id, p.project)
    if bool(p.note_path) == bool(p.url):
        raise ToolError("Pass exactly one of note_path or url (set a GitHub repo with update_project).")
    if p.note_path:
        doc = ctx.db.scalar(select(Document).where(Document.path == p.note_path))
        if doc is None:
            raise ToolError("No indexed note with that path. Use notes_search to find it.")
        kind, target = "note", doc.path
    else:
        url = (p.url or "").strip()
        if not url.lower().startswith(("https://", "http://")) or any(c.isspace() for c in url):
            raise ToolError("The link must be a plain http(s) address.")
        kind, target = "url", url
    existing = ctx.db.scalar(select(ProjectLink).where(ProjectLink.project_id == project.id, ProjectLink.target == target))
    if existing is not None:
        return {"project": project.name, "link": link_dict(existing), "note": "Already attached."}
    title = " ".join((_text(p.title, 200) or "").split()) or None
    link = ProjectLink(project_id=project.id, kind=kind, target=target, title=title)
    ctx.db.add(link)
    ctx.db.flush()
    return {"project": project.name, "link": link_dict(link)}


# ---------- project_report ----------
class ProjectReportInput(ProjectRef):
    days: int = Field(7, ge=1, le=90, description="How far back to look.")


def project_report(ctx: ToolContext, p: ProjectReportInput) -> dict:
    project = _find_project(ctx, p.project_id, p.project)
    return build_report(ctx.db, project, ctx.today(), ctx.tz, p.days)


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="create_project",
            description="Create a project (a body of work with its own tasks), e.g. 'FYP' or a research paper. Optionally link it to a goal.",
            permission=Permission.WRITE,
            input_model=CreateProjectInput,
            handler=create_project,
        )
    )
    registry.register(
        Tool(
            name="list_projects",
            description="List the user's projects. Use before planning or when the user asks about a specific piece of work.",
            permission=Permission.READ,
            input_model=ListProjectsInput,
            handler=list_projects,
        )
    )
    registry.register(
        Tool(
            name="update_project",
            description="Change a project's name, description, purpose, status, priority, deadline or GitHub repo.",
            permission=Permission.WRITE,
            input_model=UpdateProjectInput,
            handler=update_project,
        )
    )
    registry.register(Tool(
        name="project_report", permission=Permission.READ, input_model=ProjectReportInput, handler=project_report,
        description="A project's progress report or status: tasks done/open/overdue/stuck, time worked, open blockers, "
                    "decisions, updates and attached documents. Report only what it returns."))
    registry.register(Tool(
        name="log_project_entry", permission=Permission.WRITE, input_model=LogProjectEntryInput, handler=log_project_entry,
        description="Add to a project's log: an update (progress made), a blocker (what is stopping it) or a decision "
                    "(what was decided and why)."))
    registry.register(Tool(
        name="resolve_blocker", permission=Permission.WRITE, input_model=ResolveBlockerInput, handler=resolve_blocker,
        description="Mark a project blocker as resolved."))
    registry.register(Tool(
        name="link_project", permission=Permission.WRITE, input_model=LinkProjectInput, handler=link_project,
        description="Attach a document to a project: one of the user's indexed notes or a web link."))
