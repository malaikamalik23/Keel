from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Goal, Milestone, Task
from app.services.task_events import record_task_event
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

OPEN_TASK_STATUSES = ("todo", "in_progress")


def _milestone_dict(m: Milestone) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "position": m.position,
        "status": m.status,
        "target_date": m.target_date.isoformat() if m.target_date else None,
    }


def _goal_dict(g: Goal) -> dict:
    return {
        "id": g.id,
        "title": g.title,
        "description": g.description,
        "horizon": g.horizon,
        "category": g.category,
        "status": g.status,
        "priority": g.priority,
        "target_date": g.target_date.isoformat() if g.target_date else None,
        "milestones": [_milestone_dict(m) for m in g.milestones],
    }


def _get_goal(ctx: ToolContext, goal_id: int) -> Goal:
    goal = ctx.db.get(Goal, goal_id)
    if goal is None:
        raise ToolError(f"No goal with id {goal_id}.")
    return goal


# ---------- create_goal ----------
class CreateGoalInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    horizon: Literal["short", "long"] = Field("long", description="short = weeks/months, long = a year or more.")
    category: Literal["career", "education", "research", "skills", "personal", "other"] = "other"
    priority: Literal["low", "medium", "high"] = "medium"
    target_date: date | None = None


def create_goal(ctx: ToolContext, p: CreateGoalInput) -> dict:
    goal = Goal(
        title=p.title.strip(), description=p.description, horizon=p.horizon,
        category=p.category, priority=p.priority, target_date=p.target_date,
    )
    ctx.db.add(goal)
    ctx.db.flush()
    return {"goal": _goal_dict(goal)}


# ---------- list_goals ----------
class ListGoalsInput(BaseModel):
    status: Literal["active", "paused", "achieved", "dropped", "all"] = "active"


def list_goals(ctx: ToolContext, p: ListGoalsInput) -> dict:
    stmt = select(Goal)
    if p.status != "all":
        stmt = stmt.where(Goal.status == p.status)
    goals = list(ctx.db.scalars(stmt.order_by(Goal.id)))
    return {"total": len(goals), "goals": [_goal_dict(g) for g in goals]}


# ---------- update_goal ----------
class UpdateGoalInput(BaseModel):
    goal_id: int
    title: str | None = None
    description: str | None = None
    status: Literal["active", "paused", "achieved", "dropped"] | None = None
    priority: Literal["low", "medium", "high"] | None = None
    target_date: date | None = None


def update_goal(ctx: ToolContext, p: UpdateGoalInput) -> dict:
    goal = _get_goal(ctx, p.goal_id)
    changes = p.model_dump(exclude_unset=True, exclude={"goal_id"})
    if not changes:
        raise ToolError("No fields to update were provided.")
    for required in ("title", "status", "priority"):
        if required in changes and changes[required] is None:
            raise ToolError(f"'{required}' cannot be null.")
    for field, value in changes.items():
        setattr(goal, field, value)
    ctx.db.flush()
    return {"goal": _goal_dict(goal)}


# ---------- add_milestones ----------
class AddMilestonesInput(BaseModel):
    goal_id: int
    titles: list[str] = Field(
        min_length=1, max_length=20, description="Ordered milestone titles, e.g. ['HTTP fundamentals', 'Authentication', ...]."
    )


def add_milestones(ctx: ToolContext, p: AddMilestonesInput) -> dict:
    goal = _get_goal(ctx, p.goal_id)
    start = max((m.position for m in goal.milestones), default=-1) + 1
    created = []
    for i, title in enumerate(p.titles):
        title = title.strip()
        if not title:
            continue
        m = Milestone(goal_id=goal.id, title=title, position=start + i)
        ctx.db.add(m)
        created.append(m)
    if not created:
        raise ToolError("No non-empty milestone titles were provided.")
    ctx.db.flush()
    return {"goal_id": goal.id, "created": [_milestone_dict(m) for m in created]}


# ---------- milestone_to_tasks ----------
class MilestoneToTasksInput(BaseModel):
    milestone_id: int
    titles: list[str] = Field(min_length=1, max_length=20, description="Concrete, actionable task titles for this milestone.")
    due_date: date | None = Field(None, description="Optional shared deadline applied to every created task.")


def milestone_to_tasks(ctx: ToolContext, p: MilestoneToTasksInput) -> dict:
    milestone = ctx.db.get(Milestone, p.milestone_id)
    if milestone is None:
        raise ToolError(f"No milestone with id {p.milestone_id}.")
    created = []
    for title in p.titles:
        title = title.strip()
        if not title:
            continue
        task = Task(
            title=title, due_date=p.due_date, goal_id=milestone.goal_id, milestone_id=milestone.id,
        )
        ctx.db.add(task)
        created.append(task)
    if not created:
        raise ToolError("No non-empty task titles were provided.")
    ctx.db.flush()
    for t in created:
        record_task_event(ctx.db, t, "created")
    return {
        "milestone_id": milestone.id,
        "created_tasks": [{"id": t.id, "title": t.title, "due_date": t.due_date.isoformat() if t.due_date else None} for t in created],
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="create_goal",
            description=(
                "Create a goal (career, education, research, skills or personal). Use when the user states an "
                "ambition, not a one-off task, e.g. 'I want to become good at penetration testing.'"
            ),
            permission=Permission.WRITE,
            input_model=CreateGoalInput,
            handler=create_goal,
        )
    )
    registry.register(
        Tool(
            name="list_goals",
            description="List the user's goals with their milestones. Use to answer 'what am I working towards' or before decomposing a new ambition.",
            permission=Permission.READ,
            input_model=ListGoalsInput,
            handler=list_goals,
        )
    )
    registry.register(
        Tool(
            name="update_goal",
            description="Change a goal's title, description, status, priority or target date.",
            permission=Permission.WRITE,
            input_model=UpdateGoalInput,
            handler=update_goal,
        )
    )
    registry.register(
        Tool(
            name="add_milestones",
            description=(
                "Break a goal into an ordered list of milestones (e.g. HTTP fundamentals -> Authentication -> "
                "SQL injection -> ...). Call this right after create_goal for any goal big enough to need steps. "
                "Follow up with milestone_to_tasks to make the first milestone actionable."
            ),
            permission=Permission.WRITE,
            input_model=AddMilestonesInput,
            handler=add_milestones,
        )
    )
    registry.register(
        Tool(
            name="milestone_to_tasks",
            description="Turn one milestone into concrete tasks the user can actually start on.",
            permission=Permission.WRITE,
            input_model=MilestoneToTasksInput,
            handler=milestone_to_tasks,
        )
    )
