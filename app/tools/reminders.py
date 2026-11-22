from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, WithJsonSchema
from sqlalchemy import select

from app.db.models import Reminder
from app.services.timeutil import local_to_naive_utc, naive_utc_to_local
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


def _reminder_dict(r: Reminder, ctx: ToolContext) -> dict:
    return {
        "id": r.id, "text": r.text, "status": r.status,
        "remind_at": naive_utc_to_local(r.fire_at, ctx.tz).isoformat(timespec="minutes"),
        "linked_type": r.linked_type, "linked_id": r.linked_id,
    }


class CreateReminderInput(BaseModel):
    text: str = Field(min_length=1, max_length=300, description="What to remind the user about, phrased as the message they will read.")
    # Advertised as a plain string, not JSON Schema "date-time": that format demands seconds and a UTC offset, and
    # providers that validate tool calls server-side (Groq) rejected the local '2026-11-08T09:00' this description asks for.
    remind_at: Annotated[datetime, WithJsonSchema({"type": "string"})] = Field(description="When to send it, as an ISO-8601 local date-time in the user's timezone, e.g. 2026-11-08T09:00. Resolve 'tomorrow morning' against the current time.")
    linked_type: Literal["task", "commitment", "project", "goal"] | None = None
    linked_id: int | None = None


def create_reminder(ctx: ToolContext, p: CreateReminderInput) -> dict:
    fire_at = local_to_naive_utc(p.remind_at, ctx.tz)
    if fire_at <= ctx.now().astimezone(UTC).replace(tzinfo=None):  # the turn's clock, like every other date the tools resolve
        raise ToolError("That time is in the past. Ask the user for a future time.")
    if (p.linked_type is None) != (p.linked_id is None):
        raise ToolError("linked_type and linked_id must be given together.")
    dup = ctx.db.scalars(select(Reminder).where(Reminder.status == "pending", Reminder.text == p.text.strip(), Reminder.fire_at == fire_at)).first()
    if dup:
        return {"reminder": _reminder_dict(dup, ctx), "note": "Already set (duplicate skipped)."}
    r = Reminder(text=p.text.strip(), fire_at=fire_at, linked_type=p.linked_type, linked_id=p.linked_id)
    ctx.db.add(r)
    ctx.db.flush()
    return {"reminder": _reminder_dict(r, ctx)}


class ListRemindersInput(BaseModel):
    status: Literal["pending", "sent", "cancelled", "failed", "all"] = "pending"


def list_reminders(ctx: ToolContext, p: ListRemindersInput) -> dict:
    stmt = select(Reminder).order_by(Reminder.fire_at)
    if p.status != "all":
        stmt = stmt.where(Reminder.status == p.status)
    rows = list(ctx.db.scalars(stmt.limit(50)))
    return {"total": len(rows), "reminders": [_reminder_dict(r, ctx) for r in rows]}


class CancelReminderInput(BaseModel):
    reminder_id: int


def cancel_reminder(ctx: ToolContext, p: CancelReminderInput) -> dict:
    r = ctx.db.get(Reminder, p.reminder_id)
    if r is None:
        raise ToolError(f"No reminder with id {p.reminder_id}.")
    if r.status != "pending":
        raise ToolError(f"Reminder #{r.id} is already {r.status}.")
    r.status = "cancelled"
    ctx.db.flush()
    return {"reminder": _reminder_dict(r, ctx)}


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="create_reminder",
        description="Schedule a one-off reminder message for a specific future time ('remind me tomorrow at 9 to email Dr. Khan'). It is delivered through the user's notification channel.",
        permission=Permission.WRITE, input_model=CreateReminderInput, handler=create_reminder,
    ))
    registry.register(Tool(
        name="list_reminders", description="List the user's reminders (pending by default).",
        permission=Permission.READ, input_model=ListRemindersInput, handler=list_reminders,
    ))
    registry.register(Tool(
        name="cancel_reminder", description="Cancel a pending reminder.",
        permission=Permission.WRITE, input_model=CancelReminderInput, handler=cancel_reminder,
    ))
