from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Project
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
    }


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


def create_project(ctx: ToolContext, p: CreateProjectInput) -> dict:
    project = Project(
        name=p.name.strip(), description=p.description, purpose=p.purpose,
        priority=p.priority, deadline=p.deadline, goal_id=p.goal_id,
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


def update_project(ctx: ToolContext, p: UpdateProjectInput) -> dict:
    project = _get_project(ctx, p.project_id)
    changes = p.model_dump(exclude_unset=True, exclude={"project_id"})
    if not changes:
        raise ToolError("No fields to update were provided.")
    for required in ("name", "status", "priority"):
        if required in changes and changes[required] is None:
            raise ToolError(f"'{required}' cannot be null.")
    for field, value in changes.items():
        setattr(project, field, value)
    ctx.db.flush()
    return {"project": _project_dict(project)}


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
            description="Change a project's name, description, purpose, status, priority or deadline.",
            permission=Permission.WRITE,
            input_model=UpdateProjectInput,
            handler=update_project,
        )
    )
