from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.db.models import Person, PersonInteraction
from app.services import people as svc
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


def _get(ctx: ToolContext, name: str) -> Person:
    p = svc.find(ctx.db, name)
    if p is None:
        raise ToolError(f"No one called '{name}' is saved yet. Use remember_person first.")
    return p


# ---------- remember_person ----------
class RememberPersonInput(BaseModel):
    name: str = Field(min_length=2, max_length=120, description="Their name as the user says it, e.g. 'Dr. Khan' or 'Sara Ahmed'. An existing person with this name or alias is updated.")
    relation: str | None = Field(None, max_length=60, description="Who they are to the user: supervisor, friend, sister, recruiter...")
    notes: str | None = Field(None, max_length=2000, description="Things worth remembering about them. Replaces the notes; include the old ones you want to keep.")
    email: str | None = Field(None, max_length=200)
    aliases: list[str] | None = Field(None, max_length=10, description="Other names the user uses for them, e.g. ['Khan sahab'].")
    keep_in_touch_days: int | None = Field(None, ge=1, le=730, description="Nudge the user when they haven't been in touch for this many days.")


def remember_person(ctx: ToolContext, p: RememberPersonInput) -> dict:
    name = " ".join(p.name.split())
    person = svc.find(ctx.db, name)
    created = person is None
    if created:
        person = Person(name=name)
        ctx.db.add(person)
    for field in ("relation", "notes", "email", "keep_in_touch_days"):
        value = getattr(p, field)
        if value is not None:
            setattr(person, field, value.strip() if isinstance(value, str) else value)
    if p.aliases is not None:
        person.aliases = ", ".join(a.strip().replace(",", " ") for a in p.aliases if a.strip())[:300] or None
    ctx.db.flush()
    return {"created": created, "person": svc.person_dict(person, ctx.today())}


# ---------- log_contact ----------
class LogContactInput(BaseModel):
    name: str = Field(min_length=2, max_length=120, description="Who the user talked to, met or heard from.")
    note: str = Field("", max_length=500, description="A line about it in the user's words, e.g. 'discussed chapter 2, she wants it by Friday'.")
    on_date: date | None = Field(None, description="When, as YYYY-MM-DD (default today).")


def log_contact(ctx: ToolContext, p: LogContactInput) -> dict:
    person = _get(ctx, p.name)
    when = p.on_date or ctx.today()
    if when > ctx.today():
        raise ToolError("That date is in the future: log a contact after it happens.")
    ctx.db.add(PersonInteraction(person_id=person.id, on_date=when, text=p.note.strip()))
    svc.refresh_last_contact(ctx.db, person)
    return {"logged": True, "person": svc.person_dict(person, ctx.today())}


# ---------- person_info ----------
class PersonInfoInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)


def person_info(ctx: ToolContext, p: PersonInfoInput) -> dict:
    return svc.summary(ctx.db, _get(ctx, p.name), ctx.today())


# ---------- list_people ----------
class ListPeopleInput(BaseModel):
    reconnect_only: bool = Field(False, description="Only people the user wanted to keep in touch with and hasn't lately.")


def list_people(ctx: ToolContext, p: ListPeopleInput) -> dict:
    rows = svc.overview(ctx.db, ctx.today())
    if p.reconnect_only:
        rows = [r for r in rows if r.get("reconnect_due")]
    return {"total": len(rows), "people": rows}


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="remember_person",
        description=(
            "Save or update someone in the user's life: who they are (relation), notes, email, other names, and how often "
            "the user wants to keep in touch. Use when the user tells you about a person ('Dr. Khan is my supervisor', "
            "'remind me to talk to Ammi every week')."
        ),
        permission=Permission.WRITE, input_model=RememberPersonInput, handler=remember_person,
    ))
    registry.register(Tool(
        name="log_contact",
        description="Record that the user talked to, met or heard from someone, with a line about it. Updates when they last spoke.",
        permission=Permission.WRITE, input_model=LogContactInput, handler=log_contact,
    ))
    registry.register(Tool(
        name="person_info",
        description=(
            "Everything about one person: who they are, when the user last talked to them, open promises to them, what "
            "the user is waiting on them for, tasks about them, memories that mention them, and recent contacts. Use for "
            "'who is X', 'what did I promise X', 'when did I last talk to X', or before the user meets or messages someone."
        ),
        permission=Permission.READ, input_model=PersonInfoInput, handler=person_info,
    ))
    registry.register(Tool(
        name="list_people",
        description="Everyone saved, with open promises and waits per person; reconnect_only=true for people the user is overdue to get in touch with.",
        permission=Permission.READ, input_model=ListPeopleInput, handler=list_people,
    ))
