"""Chat tools for workflows. Creating one is high impact (it is a standing rule that acts later on its own), so the
user always approves the exact trigger and steps. Approving a paused run is never a tool: only the user, in the
dashboard or API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Workflow, WorkflowRun
from app.services.workflows import Step, Trigger, describe, run_dict, validate_definition, workflow_dict
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


class CreateWorkflowInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: Trigger
    steps: list[Step] = Field(min_length=1, max_length=10, description=(
        "In order. Args may use {{today}}, {{today+7}}, {{event.title}}, {{event.id}}, {{event.deadline}}, "
        "or an earlier step's result like {{steps.1.task.id}}."))


class WorkflowRef(BaseModel):
    workflow_id: int


class SetEnabledInput(WorkflowRef):
    enabled: bool


class HistoryInput(BaseModel):
    workflow_id: int | None = None
    limit: int = Field(10, ge=1, le=50)


def register(registry: ToolRegistry, engine: dict[str, Any]) -> None:
    """`engine` is filled in by the container once notifications exist: {"engine": Engine}."""

    def _validated(p: CreateWorkflowInput):
        try:
            return validate_definition(registry, p.trigger.model_dump(exclude_none=True), [s.model_dump(exclude_none=True) for s in p.steps])
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    def create_workflow(ctx: ToolContext, p: CreateWorkflowInput) -> dict:
        trigger, steps = _validated(p)
        wf = Workflow(name=" ".join(p.name.split())[:120], trigger=trigger.model_dump(exclude_none=True),
                      steps=[s.model_dump(exclude_none=True, exclude_defaults=False) for s in steps])
        ctx.db.add(wf)
        ctx.db.flush()
        return {"workflow": workflow_dict(wf)}

    def summarize(ctx: ToolContext, p: CreateWorkflowInput) -> str:
        try:
            trigger, steps = _validated(p)
        except ToolError as exc:
            return f"Create workflow “{p.name}” (invalid: {exc})"
        return f"Create workflow “{p.name}”. " + describe(trigger.model_dump(exclude_none=True), [s.model_dump(exclude_none=True) for s in steps])

    def list_workflows(ctx: ToolContext, p: BaseModel) -> dict:
        return {"workflows": [workflow_dict(w) for w in ctx.db.scalars(select(Workflow).order_by(Workflow.id))]}

    def _get(ctx: ToolContext, wid: int) -> Workflow:
        wf = ctx.db.get(Workflow, wid)
        if wf is None:
            raise ToolError(f"No workflow with id {wid}.")
        return wf

    def run_workflow(ctx: ToolContext, p: WorkflowRef) -> dict:
        wf = _get(ctx, p.workflow_id)
        eng = engine.get("engine")
        if eng is None:
            raise ToolError("Workflows can't run in this setup.")
        now = datetime.now(ctx.tz)
        run = eng.start(ctx.db, wf, f"manual:{now.isoformat()}", {"type": "manual"})
        if run is None:
            raise ToolError("That workflow was just started.")
        eng.advance(ctx.db, run, now)
        return {"run": run_dict(run, ctx.tz)}

    def set_workflow_enabled(ctx: ToolContext, p: SetEnabledInput) -> dict:
        wf = _get(ctx, p.workflow_id)
        wf.enabled = p.enabled
        ctx.db.flush()
        return {"workflow": workflow_dict(wf)}

    def workflow_history(ctx: ToolContext, p: HistoryInput) -> dict:
        stmt = select(WorkflowRun).order_by(WorkflowRun.id.desc()).limit(p.limit)
        if p.workflow_id is not None:
            stmt = stmt.where(WorkflowRun.workflow_id == p.workflow_id)
        return {"runs": [run_dict(r, ctx.tz) for r in ctx.db.scalars(stmt)]}

    class Empty(BaseModel):
        pass

    registry.register(Tool(
        name="create_workflow", permission=Permission.HIGH_IMPACT, input_model=CreateWorkflowInput, handler=create_workflow, summarize=summarize,
        description="Call this for 'whenever / every time / when X happens, do Y' requests: it sets up a standing rule. "
                    "Triggers: a schedule, a completed task, a new application (job, internship or scholarship: "
                    "opportunity_added), an application's status, a project blocker, or on demand. Steps use Keel's own tools "
                    "or send the user a message. The user approves the rule before it exists, so propose it directly rather "
                    "than asking first. Steps can't read web pages or documents."))
    registry.register(Tool(name="list_workflows", permission=Permission.READ, input_model=Empty, handler=list_workflows,
                           description="List the user's workflows (standing 'when X, do Y' rules)."))
    registry.register(Tool(name="run_workflow", permission=Permission.WRITE, input_model=WorkflowRef, handler=run_workflow,
                           description="Start a workflow now."))
    registry.register(Tool(name="set_workflow_enabled", permission=Permission.WRITE, input_model=SetEnabledInput, handler=set_workflow_enabled,
                           description="Pause or resume a workflow."))
    registry.register(Tool(name="workflow_history", permission=Permission.READ, input_model=HistoryInput, handler=workflow_history,
                           description="Recent workflow runs: status, each step's result, errors, what waits for approval."))
