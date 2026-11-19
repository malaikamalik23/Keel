"""Important dates: exams, birthdays, application windows. Not tasks (nothing to tick off), but worth a reminder.

A yearly date (a birthday) comes back every year; its next occurrence is computed, never stored.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import ImportantDate
from app.security.untrusted import sanitize_text
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


def next_occurrence(d: ImportantDate, today: date) -> date | None:
    """The next time this date comes round, on or after today. None for a one-off date that has passed."""
    if not d.yearly:
        return d.on_date if d.on_date >= today else None
    for year in (today.year, today.year + 1):
        try:
            when = d.on_date.replace(year=year)
        except ValueError:  # 29 February in a year without one
            when = date(year, 2, 28)
        if when >= today:
            return when
    return None


def upcoming(db, today: date, within_days: int) -> list[dict]:
    out = []
    for d in db.scalars(select(ImportantDate)):
        when = next_occurrence(d, today)
        if when is not None and (when - today).days <= within_days:
            out.append({"id": d.id, "title": d.title, "date": when.isoformat(), "days_left": (when - today).days,
                        "category": d.category, "remind_days_before": d.remind_days_before})
    return sorted(out, key=lambda x: (x["date"], x["id"]))


def _dict(d: ImportantDate, today: date) -> dict:
    when = next_occurrence(d, today)
    return {"id": d.id, "title": d.title, "date": d.on_date.isoformat(), "yearly": d.yearly, "category": d.category,
            "remind_days_before": d.remind_days_before, "next": when.isoformat() if when else None}


class AddImportantDateInput(BaseModel):
    title: str = Field(min_length=1, max_length=120, description="What it is, e.g. 'GRE exam', 'Ammi's birthday'.")
    on_date: date = Field(description="YYYY-MM-DD. For a birthday, any year (e.g. the birth date).")
    yearly: bool = Field(False, description="True for birthdays and anniversaries.")
    category: Literal["personal", "academic", "professional"] = "personal"
    remind_days_before: int = Field(3, ge=0, le=60, description="How many days ahead to remind (also reminded on the day).")


def add_important_date(ctx: ToolContext, p: AddImportantDateInput) -> dict:
    title = " ".join(sanitize_text(p.title).split())
    if not title:
        raise ToolError("The title is empty once invisible characters are removed.")
    d = ImportantDate(title=title, on_date=p.on_date, yearly=p.yearly, category=p.category, remind_days_before=p.remind_days_before)
    ctx.db.add(d)
    ctx.db.flush()
    out = {"important_date": _dict(d, ctx.today())}
    if out["important_date"]["next"] is None:
        out["warning"] = "That date has already passed and doesn't repeat, so no reminder will come."
    return out


class ListImportantDatesInput(BaseModel):
    within_days: int | None = Field(None, ge=0, le=400, description="Only dates coming up within this many days. Omit for all.")


def list_important_dates(ctx: ToolContext, p: ListImportantDatesInput) -> dict:
    today = ctx.today()
    if p.within_days is not None:
        return {"upcoming": upcoming(ctx.db, today, p.within_days)}
    rows = [_dict(d, today) for d in ctx.db.scalars(select(ImportantDate))]
    rows.sort(key=lambda r: (r["next"] is None, r["next"] or "", r["id"]))
    return {"total": len(rows), "important_dates": rows}


class DeleteImportantDateInput(BaseModel):
    date_id: int


def delete_important_date(ctx: ToolContext, p: DeleteImportantDateInput) -> dict:
    d = ctx.db.get(ImportantDate, p.date_id)
    if d is None:
        raise ToolError(f"No important date with id {p.date_id}.")
    title = d.title
    ctx.db.delete(d)
    ctx.db.flush()
    return {"deleted_date_id": p.date_id, "title": title}


def _summarize_delete(ctx: ToolContext, p: DeleteImportantDateInput) -> str:
    d = ctx.db.get(ImportantDate, p.date_id)
    if d is None:
        raise ToolError(f"No important date with id {p.date_id}.")
    return f'Delete the important date #{d.id}: "{d.title}" ({d.on_date.isoformat()})'


def register(registry: ToolRegistry) -> None:
    for tool in (
        Tool(
            name="add_important_date",
            description="Remember a date that matters but isn't a task: an exam, a birthday (yearly), an application window, an anniversary. Keel reminds the user a few days ahead and on the day.",
            permission=Permission.WRITE, input_model=AddImportantDateInput, handler=add_important_date,
        ),
        Tool(
            name="list_important_dates",
            description="List the user's important dates (exams, birthdays...), or only those coming up within N days.",
            permission=Permission.READ, input_model=ListImportantDatesInput, handler=list_important_dates,
        ),
        Tool(
            name="delete_important_date",
            description="Delete an important date. High impact: queued for the user's approval.",
            permission=Permission.HIGH_IMPACT, input_model=DeleteImportantDateInput, handler=delete_important_date,
            summarize=_summarize_delete,
        ),
    ):
        registry.register(tool)
