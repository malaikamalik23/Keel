"""Applications manager: jobs, internships, scholarships, programmes. Keel tracks and prepares; the user submits.

Keel never submits anything and never invents qualifications. `prepare_application` hands the model the opportunity,
the missing documents and the user's own profile, so drafts are built only from what the user has actually said; a
fact that isn't known is left as a visible [ADD: ...] gap for the user to fill.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import ApplicationDraft, Memory, Opportunity
from app.security.untrusted import sanitize_text
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

Kind = Literal["job", "internship", "scholarship", "program", "other"]
Status = Literal["interested", "preparing", "applied", "interview", "offer", "accepted", "rejected", "withdrawn"]
DocStatus = Literal["needed", "drafting", "ready", "requested", "received"]
DraftKind = Literal["cover_letter", "sop", "cv", "answer", "follow_up", "interview_prep", "other"]
CLOSED = ("accepted", "rejected", "withdrawn")
DONE_DOCS = ("ready", "received")


def _line(text: str | None, n: int) -> str | None:
    """Plain, single-line text (fields can come from a web page the user read)."""
    if text is None:
        return None
    return " ".join(sanitize_text(text).split())[:n] or None


def _block(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    return sanitize_text(text).strip()[:n] or None


def _url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url.lower().startswith(("https://", "http://")) or any(c.isspace() for c in url):
        raise ToolError("The link must be a plain http(s) address.")
    return url[:500]


def missing_documents(o: Opportunity) -> list[str]:
    return [d["name"] + (f" ({d['from']})" if d.get("from") else "") for d in (o.documents or []) if d.get("status") not in DONE_DOCS]


def opportunity_dict(o: Opportunity, today: date | None = None) -> dict:
    d = {
        "id": o.id, "kind": o.kind, "title": o.title, "organization": o.organization, "url": o.url, "location": o.location,
        "deadline": o.deadline.isoformat() if o.deadline else None, "status": o.status,
        "documents": o.documents or [], "missing": missing_documents(o),
        "next_step": o.next_step, "next_step_date": o.next_step_date.isoformat() if o.next_step_date else None,
        "applied_on": o.applied_on.isoformat() if o.applied_on else None,
    }
    if today and o.deadline and o.status not in CLOSED and o.status not in ("applied", "interview", "offer"):
        d["days_left"] = (o.deadline - today).days
    return d


def _get(ctx: ToolContext, opportunity_id: int) -> Opportunity:
    o = ctx.db.get(Opportunity, opportunity_id)
    if o is None:
        raise ToolError(f"No opportunity with id {opportunity_id}.")
    return o


# ---------- add_opportunity
class AddOpportunityInput(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="e.g. 'Data Science Intern', 'Fulbright MS scholarship'.")
    kind: Kind = "job"
    organization: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=500, description="The original posting, so the user can check it.")
    location: str | None = Field(None, max_length=120)
    deadline: date | None = None
    eligibility: str | None = Field(None, max_length=2000, description="Requirements as stated in the posting.")
    documents: list[str] = Field(default_factory=list, max_length=20, description="Required documents, e.g. ['CV', 'SOP', 'Transcript', '2 recommendation letters'].")
    notes: str | None = Field(None, max_length=2000)


def add_opportunity(ctx: ToolContext, p: AddOpportunityInput) -> dict:
    url = _url(p.url)
    title, org = _line(p.title, 200), _line(p.organization, 200)
    if not title:
        raise ToolError("The title is empty once invisible characters are removed.")
    existing = ctx.db.scalars(select(Opportunity).where(Opportunity.url == url)).first() if url else None
    existing = existing or ctx.db.scalars(select(Opportunity).where(Opportunity.title == title, Opportunity.organization == org)).first()
    if existing:
        return {"opportunity": opportunity_dict(existing, ctx.today()), "note": "Already tracked (duplicate skipped)."}
    docs = [{"name": n, "status": "needed"} for n in dict.fromkeys(filter(None, (_line(d, 80) for d in p.documents)))]
    o = Opportunity(kind=p.kind, title=title, organization=org, url=url, location=_line(p.location, 120), deadline=p.deadline,
                    eligibility=_block(p.eligibility, 2000), documents=docs, notes=_block(p.notes, 2000))
    ctx.db.add(o)
    ctx.db.flush()
    out = {"opportunity": opportunity_dict(o, ctx.today())}
    if p.deadline and p.deadline < ctx.today():
        out["warning"] = "That deadline has already passed."
    return out


# ---------- list_opportunities
class ListOpportunitiesInput(BaseModel):
    status: Literal["active", "all"] | Status = Field("active", description="active = everything not accepted, rejected or withdrawn.")
    kind: Kind | None = None
    due_within_days: int | None = Field(None, ge=0, le=365)


def list_opportunities(ctx: ToolContext, p: ListOpportunitiesInput) -> dict:
    stmt = select(Opportunity)
    if p.status == "active":
        stmt = stmt.where(Opportunity.status.notin_(CLOSED))
    elif p.status != "all":
        stmt = stmt.where(Opportunity.status == p.status)
    if p.kind:
        stmt = stmt.where(Opportunity.kind == p.kind)
    today = ctx.today()
    rows = list(ctx.db.scalars(stmt))
    if p.due_within_days is not None:
        rows = [o for o in rows if o.deadline and 0 <= (o.deadline - today).days <= p.due_within_days]
    rows.sort(key=lambda o: (o.deadline is None, o.deadline or date.max, o.id))
    return {"total": len(rows), "opportunities": [opportunity_dict(o, today) for o in rows]}


# ---------- update_opportunity
class DocumentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80, description="Document name as in the checklist (added if new).")
    status: DocStatus
    from_person: str | None = Field(None, max_length=80, description="For a recommendation letter: who writes it.")


class UpdateOpportunityInput(BaseModel):
    opportunity_id: int
    status: Status | None = None
    deadline: date | None = None
    eligibility: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=2000)
    next_step: str | None = Field(None, max_length=200, description="e.g. 'Interview on Zoom', 'Ask HR about the result'.")
    next_step_date: date | None = None
    documents: list[DocumentUpdate] | None = Field(None, max_length=20)


def update_opportunity(ctx: ToolContext, p: UpdateOpportunityInput) -> dict:
    o = _get(ctx, p.opportunity_id)
    changes = p.model_dump(exclude_unset=True, exclude={"opportunity_id", "documents"})
    if not changes and not p.documents:
        raise ToolError("No fields to update were provided.")
    for field, value in changes.items():
        if field in ("eligibility", "notes"):
            value = _block(value, 2000)
        elif field == "next_step":
            value = _line(value, 200)
        setattr(o, field, value)
    if p.status == "applied" and not o.applied_on:
        o.applied_on = ctx.today()
    if p.documents:
        docs = [dict(d) for d in (o.documents or [])]
        for u in p.documents:
            name = _line(u.name, 80)
            entry = next((d for d in docs if d["name"].lower() == name.lower()), None)
            if entry is None:
                entry = {"name": name}
                docs.append(entry)
            entry["status"] = u.status
            if u.from_person:
                entry["from"] = _line(u.from_person, 80)
        o.documents = docs  # reassign so the JSON column is saved
    ctx.db.flush()
    return {"opportunity": opportunity_dict(o, ctx.today())}


# ---------- delete_opportunity (HIGH IMPACT)
class DeleteOpportunityInput(BaseModel):
    opportunity_id: int


def delete_opportunity(ctx: ToolContext, p: DeleteOpportunityInput) -> dict:
    o = _get(ctx, p.opportunity_id)
    title = o.title
    ctx.db.delete(o)
    ctx.db.flush()
    return {"deleted_opportunity_id": p.opportunity_id, "title": title}


def _summarize_delete(ctx: ToolContext, p: DeleteOpportunityInput) -> str:
    o = _get(ctx, p.opportunity_id)
    return f'Delete the application #{o.id}: "{o.title}"{" at " + o.organization if o.organization else ""} and its drafts'


# ---------- prepare_application
class PrepareApplicationInput(BaseModel):
    opportunity_id: int | None = None
    search: str | None = Field(None, max_length=200, description="Name of the posting if you don't have its id, e.g. 'Google STEP'.")


PROFILE_LIMIT = 30


def _linked(ctx: ToolContext, o: Opportunity) -> list[dict]:
    from app.tools.files import documents_linked_to

    return documents_linked_to(ctx.db, "opportunity", str(o.id))


def prepare_application(ctx: ToolContext, p: PrepareApplicationInput) -> dict:
    """Everything needed to draft for this opportunity, and nothing invented: the posting, the checklist, the user's
    own profile and background, and the drafts already written."""
    from app.tools.memory import rank, words

    o = _get(ctx, p.opportunity_id) if p.opportunity_id is not None else None
    if o is None and p.search:
        q = words(p.search)
        scored = [(len(q & words(f"{x.title} {x.organization or ''}")), x) for x in ctx.db.scalars(select(Opportunity))]
        best = max(scored, key=lambda s: (s[0], s[1].id), default=(0, None))
        o = best[1] if best[0] else None

    mine = [m for m in ctx.db.scalars(select(Memory)) if m.topic or m.category in ("academic", "professional")]
    profile = [{"id": m.id, "category": m.category, "topic": m.topic, "fact": m.content[:300]} for m in rank(mine, set())[:PROFILE_LIMIT]]
    drafts = [{"id": d.id, "kind": d.kind, "title": d.title}
              for d in ctx.db.scalars(select(ApplicationDraft).where(ApplicationDraft.opportunity_id == o.id))] if o else []
    return {
        "opportunity": {**opportunity_dict(o, ctx.today()), "eligibility": o.eligibility, "notes": o.notes} if o else None,
        **({} if o else {"note": "This posting isn't tracked: draft from the profile, ask for the posting's requirements, and offer add_opportunity."}),
        "user_profile": profile,
        "existing_drafts": drafts,
        **({"linked_documents": _linked(ctx, o)} if o else {}),
        "rules": (
            "Use only facts in user_profile or that the user has told you in this conversation. Never invent grades, "
            "degrees, jobs, skills, awards, dates or numbers. Where a needed fact is missing, write [ADD: what is needed] "
            "and ask the user. Compare eligibility against the profile honestly, including what is unclear or missing. "
            "Keel does not submit applications: the user submits them."
        ),
    }


# ---------- drafts
class SaveDraftInput(BaseModel):
    opportunity_id: int | None = None
    kind: DraftKind
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    draft_id: int | None = Field(None, description="Replace an existing draft instead of adding a new one.")


def save_draft(ctx: ToolContext, p: SaveDraftInput) -> dict:
    if p.opportunity_id is not None:
        _get(ctx, p.opportunity_id)
    content = sanitize_text(p.content).strip()
    if not content:
        raise ToolError("The draft is empty once invisible characters are removed.")
    if p.draft_id is not None:
        d = ctx.db.get(ApplicationDraft, p.draft_id)
        if d is None:
            raise ToolError(f"No draft with id {p.draft_id}.")
        d.kind, d.title, d.content = p.kind, _line(p.title, 200), content
    else:
        d = ApplicationDraft(opportunity_id=p.opportunity_id, kind=p.kind, title=_line(p.title, 200), content=content)
        ctx.db.add(d)
    ctx.db.flush()
    gaps = content.count("[ADD:")
    out = {"draft": {"id": d.id, "kind": d.kind, "title": d.title, "length": len(content)}}
    if gaps:
        out["note"] = f"{gaps} gap(s) marked [ADD: ...] for the user to fill before using it."
    return out


class ListDraftsInput(BaseModel):
    opportunity_id: int | None = None
    include_text: bool = Field(False, description="True to get the full text (long); false lists titles only.")


def list_drafts(ctx: ToolContext, p: ListDraftsInput) -> dict:
    stmt = select(ApplicationDraft).order_by(ApplicationDraft.updated_at.desc())
    if p.opportunity_id is not None:
        stmt = stmt.where(ApplicationDraft.opportunity_id == p.opportunity_id)
    rows = list(ctx.db.scalars(stmt.limit(30)))
    return {"drafts": [{"id": d.id, "opportunity_id": d.opportunity_id, "kind": d.kind, "title": d.title,
                        **({"content": d.content} if p.include_text else {"length": len(d.content)})} for d in rows]}


def register(registry: ToolRegistry) -> None:
    for tool in (
        Tool(name="add_opportunity", permission=Permission.WRITE, input_model=AddOpportunityInput, handler=add_opportunity,
             description="Track a job, internship, scholarship or programme the user is interested in: deadline, link, "
                         "requirements, and the documents it needs. Keel prepares applications; the user submits them."),
        Tool(name="list_opportunities", permission=Permission.READ, input_model=ListOpportunitiesInput, handler=list_opportunities,
             description="List tracked applications (active by default), soonest deadline first, with what is still missing."),
        Tool(name="update_opportunity", permission=Permission.WRITE, input_model=UpdateOpportunityInput, handler=update_opportunity,
             description="Update an application: status (applied, interview, offer, rejected...), deadline, next step and its "
                         "date, notes, and the document checklist (e.g. SOP ready, recommendation requested from Dr. Khan)."),
        Tool(name="delete_opportunity", permission=Permission.HIGH_IMPACT, input_model=DeleteOpportunityInput, handler=delete_opportunity,
             summarize=_summarize_delete, description="Delete a tracked application and its drafts. High impact: queued for approval."),
        Tool(name="prepare_application", permission=Permission.READ, input_model=PrepareApplicationInput, handler=prepare_application,
             description="Call FIRST, before writing any cover letter, SOP, CV section or application answer, or judging "
                         "eligibility: returns the user's own profile (and the posting, by id or name, if tracked). Works "
                         "without an id. Draft only from these facts; mark anything missing as [ADD: ...]."),
        Tool(name="save_draft", permission=Permission.WRITE, input_model=SaveDraftInput, handler=save_draft,
             description="Save a draft you wrote with the user (cover letter, SOP, CV section, answer, follow-up email, interview prep)."),
        Tool(name="list_drafts", permission=Permission.READ, input_model=ListDraftsInput, handler=list_drafts,
             description="List saved application drafts, optionally for one application, with or without their text."),
    ):
        registry.register(tool)
