"""Application help: discovery, eligibility, document readiness and form plans. All reads; nothing here submits.
The rules live in app/services/application_help.py, so the answers are computed, not guessed."""
from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import ApplicationDraft, Document, Memory, Opportunity
from app.services.application_help import eligibility_report, fill_plan, profile_facts, readiness
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

APPLICATION_WORDS = "(scholarship OR fellowship OR internship OR admission OR \"call for applications\" OR vacancy OR deadline)"


class OpportunityRef(BaseModel):
    opportunity_id: int | None = None
    search: str | None = Field(None, max_length=200, description="The posting's name if you don't have its id.")


class FindInput(BaseModel):
    query: str = Field(min_length=2, max_length=200, description="What to look for, e.g. 'fully funded MS computer science Germany'.")
    include_email: bool = Field(True, description="Also look through the user's Gmail (last 60 days), if connected.")
    count: int = Field(6, ge=1, le=10)


def _find(ctx: ToolContext, p: OpportunityRef) -> Opportunity:
    from app.tools.memory import words

    if p.opportunity_id is not None:
        o = ctx.db.get(Opportunity, p.opportunity_id)
        if o is None:
            raise ToolError(f"No opportunity with id {p.opportunity_id}.")
        return o
    if p.search:
        q = words(p.search)
        scored = [(len(q & words(f"{x.title} {x.organization or ''}")), x) for x in ctx.db.scalars(select(Opportunity))]
        best = max(scored, key=lambda s: (s[0], s[1].id), default=(0, None))
        if best[0]:
            return best[1]
        raise ToolError(f"No tracked posting matches '{p.search}'. Track it with add_opportunity first.")
    raise ToolError("Say which posting: its id, or its name in search.")


def _memories(ctx: ToolContext) -> list[Memory]:
    return list(ctx.db.scalars(select(Memory)))


def register(registry: ToolRegistry, *, search=None, gmail=None, browser=None) -> None:
    def check_eligibility(ctx: ToolContext, p: OpportunityRef) -> dict:
        return eligibility_report(_find(ctx, p), _memories(ctx), ctx.today())

    def application_readiness(ctx: ToolContext, p: OpportunityRef) -> dict:
        o = _find(ctx, p)
        return readiness(o, list(ctx.db.scalars(select(Document))), list(ctx.db.scalars(select(ApplicationDraft))))

    registry.register(Tool(name="check_eligibility", permission=Permission.READ, input_model=OpportunityRef, handler=check_eligibility,
                           description="Check a tracked posting's requirements (CGPA, IELTS/TOEFL, degree, age, nationality, "
                                       "experience) against the user's saved profile: met, not met, unknown or check-yourself."))
    registry.register(Tool(name="application_readiness", permission=Permission.READ, input_model=OpportunityRef, handler=application_readiness,
                           description="For each document an application needs: matching files in the notes, drafts already "
                                       "written, and what's still missing."))

    if search is not None or gmail is not None:
        def find_opportunities(ctx: ToolContext, p: FindInput) -> dict:
            from app.tools.memory import words

            tracked = list(ctx.db.scalars(select(Opportunity)))
            def known(title: str, url: str = "") -> int | None:
                t = words(title)
                for o in tracked:
                    if (url and o.url and url.split("#")[0].rstrip("/") == o.url.rstrip("/")) or (t and len(t & words(o.title)) >= max(2, len(words(o.title)) // 2)):
                        return o.id
                return None

            out: dict = {"query": p.query}
            if search is not None:
                try:
                    results = search.search(f"{p.query} {APPLICATION_WORDS}", p.count)
                    out["web"] = [{**r, **({"already_tracked": i} if (i := known(r["title"], r["url"])) else {})} for r in results]
                except Exception as exc:  # noqa: BLE001 - one source failing shouldn't hide the other
                    out["web_error"] = str(exc)[:200]
            if gmail is not None and p.include_email:
                try:
                    mails = gmail.search(f"{p.query} {APPLICATION_WORDS} newer_than:60d", p.count)
                    out["email"] = [{**m.summary(), **({"already_tracked": i} if (i := known(m.subject)) else {})} for m in mails]
                except Exception as exc:  # noqa: BLE001
                    out["email_error"] = str(exc)[:200]
            out["next"] = ("Read a promising posting (web_fetch, browser_open or gmail_read) before tracking it with add_opportunity, "
                           "copying its deadline, requirements and required documents from the posting itself.")
            return out

        registry.register(Tool(name="find_opportunities", permission=Permission.READ, input_model=FindInput, handler=find_opportunities,
                               returns_untrusted=True,
                               description="Look for scholarships, internships, jobs or programmes on the web and in the user's email; "
                                           "marks the ones already tracked."))

    if browser is not None:
        class FillPlanInput(BaseModel):
            opportunity_id: int | None = Field(None, description="Use this application's drafts first (optional).")

        def application_fill_plan(ctx: ToolContext, p: FillPlanInput) -> dict:
            from app.tools.browser import sensitive

            snap = browser.last
            if snap is None:
                raise ToolError("No page is open in Keel's browser. Open the application form with browser_open first.")
            drafts = list(ctx.db.scalars(select(ApplicationDraft).order_by(ApplicationDraft.updated_at.desc())))
            if p.opportunity_id is not None:
                drafts.sort(key=lambda d: d.opportunity_id != p.opportunity_id)
            out = fill_plan(snap.elements, profile_facts(_memories(ctx)), drafts, sensitive)
            return {"page": {"url": snap.url, "title": snap.title}, **out}

        registry.register(Tool(name="application_fill_plan", permission=Permission.READ, input_model=FillPlanInput, handler=application_fill_plan,
                               returns_untrusted=True,
                               description="For the form open in Keel's browser: which saved profile fact or draft fits each field, and "
                                           "which fields the user must fill. Then browser_fill (approved by the user)."))
