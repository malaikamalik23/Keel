from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Commitment, utcnow
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


def _commitment_dict(c: Commitment, today: date | None = None) -> dict:
    d = {
        "id": c.id,
        "text": c.text,
        "deadline": c.deadline.isoformat() if c.deadline else None,
        "importance": c.importance,
        "status": c.status,
        "project_id": c.project_id,
        "task_id": c.task_id,
        "postponement_count": c.postponement_count,
        "completed_at": c.completed_at.isoformat(timespec="minutes") if c.completed_at else None,
    }
    if today and c.status in ("proposed", "open") and c.deadline and c.deadline < today:
        d["overdue"] = True
    return d


def _get_commitment(ctx: ToolContext, commitment_id: int) -> Commitment:
    c = ctx.db.get(Commitment, commitment_id)
    if c is None:
        raise ToolError(f"No commitment with id {commitment_id}.")
    return c


# ---------- propose_commitment ----------
class ProposeCommitmentInput(BaseModel):
    text: str = Field(min_length=1, max_length=300, description="The commitment in the user's own words, e.g. 'Finish the GRE quant set'.")
    deadline: date | None = Field(None, description="When they said they'd do it, as YYYY-MM-DD, if a deadline was implied.")
    importance: Literal["low", "medium", "high"] = "medium"
    project_id: int | None = None
    task_id: int | None = Field(None, description="Link to an existing task this commitment refers to, if any.")
    confirmed: bool = Field(
        False,
        description=(
            "True only if the user's phrasing was unambiguous and doesn't need a confirmation round-trip "
            "(e.g. they explicitly said 'remember that I promised X by Friday'). Otherwise leave False: the "
            "commitment is stored as 'proposed' and you must check with the user before it becomes active."
        ),
    )


def propose_commitment(ctx: ToolContext, p: ProposeCommitmentInput) -> dict:
    c = Commitment(
        text=p.text.strip(), deadline=p.deadline, importance=p.importance,
        project_id=p.project_id, task_id=p.task_id, conversation_id=ctx.conversation_id,
        status="open" if p.confirmed else "proposed",
    )
    ctx.db.add(c)
    ctx.db.flush()
    return {"commitment": _commitment_dict(c, ctx.today())}


# ---------- confirm_commitment ----------
class ConfirmCommitmentInput(BaseModel):
    commitment_id: int


def confirm_commitment(ctx: ToolContext, p: ConfirmCommitmentInput) -> dict:
    c = _get_commitment(ctx, p.commitment_id)
    if c.status != "proposed":
        raise ToolError(f"Commitment #{c.id} is '{c.status}', not 'proposed'.")
    c.status = "open"
    ctx.db.flush()
    return {"commitment": _commitment_dict(c, ctx.today())}


# ---------- list_commitments ----------
class ListCommitmentsInput(BaseModel):
    status: Literal["open", "proposed", "done", "dropped", "all"] = "open"
    due_on_or_before: date | None = None


def list_commitments(ctx: ToolContext, p: ListCommitmentsInput) -> dict:
    stmt = select(Commitment)
    if p.status != "all":
        stmt = stmt.where(Commitment.status == p.status)
    if p.due_on_or_before:
        stmt = stmt.where(Commitment.deadline <= p.due_on_or_before)
    rows = list(ctx.db.scalars(stmt))
    rows.sort(key=lambda c: (c.deadline is None, c.deadline or date.max, c.id))
    today = ctx.today()
    return {"total": len(rows), "commitments": [_commitment_dict(c, today) for c in rows]}


# ---------- update_commitment ----------
class UpdateCommitmentInput(BaseModel):
    commitment_id: int
    text: str | None = None
    deadline: date | None = Field(None, description="New deadline. Pushing it later counts as a postponement.")
    importance: Literal["low", "medium", "high"] | None = None
    status: Literal["open", "dropped"] | None = Field(
        None, description="To mark it done, use complete_commitment instead."
    )


def update_commitment(ctx: ToolContext, p: UpdateCommitmentInput) -> dict:
    c = _get_commitment(ctx, p.commitment_id)
    changes = p.model_dump(exclude_unset=True, exclude={"commitment_id"})
    if not changes:
        raise ToolError("No fields to update were provided.")
    if "text" in changes and not changes["text"]:
        raise ToolError("'text' cannot be empty.")

    postponed = False
    if "deadline" in changes:
        new_deadline = changes.pop("deadline")
        if new_deadline and c.deadline and new_deadline > c.deadline:
            c.postponement_count += 1
            postponed = True
        c.deadline = new_deadline
    for field, value in changes.items():
        setattr(c, field, value)
    ctx.db.flush()
    out = {"commitment": _commitment_dict(c, ctx.today())}
    if postponed:
        out["postponed"] = True
    return out


# ---------- complete_commitment ----------
class CompleteCommitmentInput(BaseModel):
    commitment_id: int


def complete_commitment(ctx: ToolContext, p: CompleteCommitmentInput) -> dict:
    c = _get_commitment(ctx, p.commitment_id)
    if c.status == "done":
        return {"commitment": _commitment_dict(c), "note": "Already marked done."}
    c.status = "done"
    c.completed_at = utcnow()
    ctx.db.flush()
    return {"commitment": _commitment_dict(c)}


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="propose_commitment",
            description=(
                "Record something the user said they'll do, e.g. 'I'll finish this tomorrow' or 'I need to "
                "complete this by Friday'. Recognize commitment language proactively. If the phrasing is "
                "unambiguous, set confirmed=true; otherwise leave it false and ask the user to confirm next, "
                "then call confirm_commitment."
            ),
            permission=Permission.WRITE,
            input_model=ProposeCommitmentInput,
            handler=propose_commitment,
        )
    )
    registry.register(
        Tool(
            name="confirm_commitment",
            description="Move a 'proposed' commitment to 'open' after the user confirms it. Call after the user says yes to a commitment you proposed.",
            permission=Permission.WRITE,
            input_model=ConfirmCommitmentInput,
            handler=confirm_commitment,
        )
    )
    registry.register(
        Tool(
            name="list_commitments",
            description="List the user's commitments, soonest deadline first. Use for 'what did I promise', 'my <thing> deadline', or accountability check-ins.",
            permission=Permission.READ,
            input_model=ListCommitmentsInput,
            handler=list_commitments,
        )
    )
    registry.register(
        Tool(
            name="update_commitment",
            description=(
                "Change a commitment's text, deadline, importance, or drop it. Use for 'move my <thing> deadline': deadlines "
                "belong to commitments (tasks have due dates, see update_task). Pushing the deadline later is recorded as a postponement."
            ),
            permission=Permission.WRITE,
            input_model=UpdateCommitmentInput,
            handler=update_commitment,
        )
    )
    registry.register(
        Tool(
            name="complete_commitment",
            description="Mark a commitment as fulfilled. Only call when the user says they did it.",
            permission=Permission.WRITE,
            input_model=CompleteCommitmentInput,
            handler=complete_commitment,
        )
    )
