"""Application help: eligibility against the saved profile, document readiness, form plans, and discovery."""
from __future__ import annotations

import json
import re
from datetime import date
from zoneinfo import ZoneInfo

from app.db.models import ApplicationDraft, Document, Memory, Opportunity
from app.integrations.gmail import Mail
from app.services.application_help import check_requirement, eligibility_report, fill_plan, profile_facts, readiness
from app.tools import build_default_registry
from app.tools.base import ToolContext
from app.tools.browser import sensitive
from app.tools.executor import ToolExecutor
from app.security.permissions import PermissionPolicy

TODAY = date(2026, 11, 22)
POSTING = """Minimum CGPA 3.0 out of 4.0.
IELTS 6.5 overall (no band below 6.0).
Applicants must hold a bachelor's degree in computer science or a related field.
Open to Pakistani nationals only.
Applicants must be under 35 years of age at the deadline.
At least 2 years of relevant work experience.
A strong commitment to returning home after the programme."""


def mem(id_, content, topic=None, category="academic"):
    return Memory(id=id_, content=content, topic=topic, category=category, kind="fact")


PROFILE = [mem(1, "CGPA 3.4/4.0", "cgpa"), mem(2, "BS Computer Science, NUST, graduating 2026 (final year)", "degree"),
           mem(3, "Pakistani", "nationality", "personal"), mem(4, "2003-05-14", "date of birth", "personal"),
           mem(5, "TOEFL iBT 95", "toefl"), mem(6, "Malaika Malik", "full name", "personal"), mem(7, "malaika@example.com", "email", "personal"),
           mem(8, "Supervisor is Dr. Khan", "supervisor")]


def statuses(report):
    return {c["kind"]: c["status"] for c in report["checks"]}


def test_eligibility_is_computed_from_the_saved_profile_and_honest_about_gaps():
    o = Opportunity(id=1, title="DAAD EPOS", organization="DAAD", eligibility=POSTING, deadline=date(2026, 12, 15))
    r = eligibility_report(o, PROFILE, TODAY)
    assert statuses(r) == {"cgpa": "met", "ielts": "unknown", "degree": "check", "nationality": "met", "age": "met",
                           "experience": "unknown", "other": "check"}
    ielts = next(c for c in r["checks"] if c["kind"] == "ielts")
    assert "you have TOEFL" in ielts["why"]  # points at what the user does have
    degree = next(c for c in r["checks"] if c["kind"] == "degree")
    assert "final-year" in degree["why"]
    assert r["verdict"] == "Nothing checked fails, but some can't be confirmed yet."
    assert r["deadline"] == {"date": "2026-12-15", "days_left": 23}
    assert any("ielts" in a for a in r["ask_user"]) and any("experience" in a for a in r["ask_user"])


def test_failing_requirements_are_reported_as_not_met():
    weak = [mem(1, "My CGPA is 2.8", "cgpa"), mem(2, "1985-01-01", "dob"), mem(3, "Indian citizen", "nationality"), mem(4, "IELTS 7.0", "ielts"),
            mem(5, "MS Data Science, LUMS (completed 2024)", "degree"), mem(6, "5 years", "work experience")]
    r = eligibility_report(Opportunity(id=2, title="x", eligibility=POSTING), weak, TODAY)
    assert statuses(r) | {"other": "check"} == {"cgpa": "not_met", "ielts": "met", "degree": "met", "nationality": "not_met",
                                                "age": "not_met", "experience": "met", "other": "check"}
    assert r["verdict"] == "At least one stated requirement isn't met."
    assert eligibility_report(Opportunity(id=3, title="y"), weak, TODAY)["verdict"].startswith("No requirements are saved")


def test_requirement_parsing_edge_cases():
    facts = profile_facts(PROFILE)
    assert check_requirement("GPA of at least 3.5/5.0", facts, TODAY)["status"] == "check"  # different scale: not guessed
    assert check_requirement("TOEFL 100 or IELTS 7", facts, TODAY)["status"] == "unknown"  # IELTS matched first: says what's missing
    assert check_requirement("Citizens of all countries except Pakistan", facts, TODAY)["status"] == "check"
    assert check_requirement("Master's degree required", facts, TODAY)["status"] == "not_met"
    assert profile_facts([mem(9, "My CGPA is 3.1 and I study at FAST")])["cgpa"]["value"] == "My CGPA is 3.1 and I study at FAST"


def test_readiness_finds_files_and_drafts_for_each_document():
    o = Opportunity(id=4, title="Fulbright", documents=[
        {"name": "CV", "status": "needed"}, {"name": "Statement of purpose", "status": "drafting"},
        {"name": "Transcript", "status": "needed"}, {"name": "Recommendation letter", "status": "requested", "from": "Dr. Khan"},
        {"name": "Passport copy", "status": "ready"}, {"name": "Writing sample", "status": "needed"}])
    docs = [Document(path=p, sha256="x", size=1, mtime=0) for p in ("cv/Malaika_CV_2026.pdf", "uni/transcript-bs.pdf", "notes/thesis.md")]
    drafts = [ApplicationDraft(id=3, opportunity_id=4, kind="sop", title="Fulbright SOP v2", content="..."),
              ApplicationDraft(id=4, opportunity_id=99, kind="sop", title="Other SOP", content="...")]
    r = readiness(o, docs, drafts)
    by = {d["name"]: d for d in r["documents"]}
    assert by["CV"]["files_in_notes"] == ["cv/Malaika_CV_2026.pdf"] and "check it fits" in by["CV"]["next"]
    assert by["Statement of purpose"]["drafts"] == [{"id": 3, "title": "Fulbright SOP v2"}]  # not another application's draft
    assert by["Transcript"]["files_in_notes"] == ["uni/transcript-bs.pdf"]
    assert by["Recommendation letter"]["next"] == "Waiting for Dr. Khan." and by["Passport copy"]["next"] == "Done."
    assert by["Writing sample"]["files_in_notes"] == [] and by["Writing sample"]["next"].startswith("Nothing found yet")
    assert r["still_needed"] == ["CV", "Statement of purpose", "Transcript", "Recommendation letter", "Writing sample"] and not r["ready"]


def el(ref, label, tag="input", **kw):
    return {"ref": ref, "tag": tag, "type": "", "label": label, "name": "", "autocomplete": "", "options": [], **kw}


def test_fill_plan_uses_saved_facts_and_drafts_and_leaves_secrets_to_the_user():
    elements = [el("e1", "Full name"), el("e2", "Email address", type="email"), el("e3", "Nationality", "select", options=["Germany", "Pakistan"]),
                el("e4", "Password", type="password"), el("e5", "CNIC number"), el("e6", "Statement of purpose", "textarea"),
                el("e7", "Favourite colour"), el("e8", "Submit", "input", type="submit"), el("e9", "CV", type="file")]
    drafts = [ApplicationDraft(id=3, kind="sop", title="SOP v2", content="I study computer science at NUST... [ADD: CGPA]")]
    out = fill_plan(elements, profile_facts(PROFILE), drafts, sensitive)
    proposed = {p["ref"]: p["value"] for p in out["proposed"]}
    assert proposed == {"e1": "Malaika Malik", "e2": "malaika@example.com", "e3": "Pakistan", "e6": drafts[0].content}
    left = {x["ref"]: x["why"] for x in out["left_for_user"]}
    assert set(left) == {"e4", "e5", "e7"} and "the user types it" in left["e4"] and "the user types it" in left["e5"]
    assert "[ADD:" in out["warning"]
    assert next(p for p in out["proposed"] if p["ref"] == "e1")["from"] == "memory #6 (name)"


class FakeGmail:
    def search(self, query, limit=10):
        self.query = query
        return [Mail(id="m1", thread_id="t1", sender="DAAD <info@daad.de>", to="me", cc="", subject="DAAD EPOS scholarship 2027: call open", date="", snippet="Deadline 15 Dec"),
                Mail(id="m2", thread_id="t2", sender="HEC <hec@hec.gov.pk>", to="me", cc="", subject="HEC Overseas Scholarship call", date="", snippet="Apply by 1 Dec")]


def test_find_opportunities_searches_email_and_marks_tracked(make_container):
    gmail = FakeGmail()
    ex = ToolExecutor(build_default_registry(gmail=gmail), PermissionPolicy())
    c = make_container()
    with c.session_factory() as db:
        db.add(Opportunity(title="DAAD EPOS scholarship", organization="DAAD", url="https://www.daad.de/epos/"))
        db.commit()
        ctx = ToolContext(db=db, tz=ZoneInfo("Asia/Karachi"))
        out = ex.run(ctx, "find_opportunities", {"query": "MS computer science Germany"})
        assert out.status == "ok" and ctx.tainted
        data = json.loads(re.search(r"\n(\{.*\})\n</untrusted_content>", out.result["untrusted_data"], re.S).group(1))
    assert data["email"][0]["already_tracked"] == 1 and "already_tracked" not in data["email"][1]
    assert "web" not in data  # no web search
    assert "scholarship OR" in gmail.query and "newer_than:60d" in gmail.query


def test_tools_by_name_and_clear_errors(make_container):
    ex = ToolExecutor(build_default_registry(), PermissionPolicy())
    assert ex.registry.get("find_opportunities") is None and ex.registry.get("application_fill_plan") is None  # need Gmail / the browser
    c = make_container()
    with c.session_factory() as db:
        db.add_all([Opportunity(title="Fulbright MS", eligibility="Minimum CGPA 3.0 out of 4.0."), *[mem(None, m.content, m.topic) for m in PROFILE[:1]]])
        db.commit()
        ctx = ToolContext(db=db, tz=ZoneInfo("Asia/Karachi"))
        out = ex.run(ctx, "check_eligibility", {"search": "fulbright"})
        assert out.status == "ok" and out.result["checks"][0]["status"] == "met"
        assert ex.run(ctx, "check_eligibility", {"search": "chevening"}).status == "error"
        assert ex.run(ctx, "application_readiness", {}).status == "error"


def test_dashboard_endpoints():
    from app.api.main import create_app
    from tests.conftest import make_settings
    from tests.fakes import ScriptedLLM, local_client

    app = create_app(make_settings(agent_timezone="Asia/Karachi"), ScriptedLLM([]))
    with app.state.container.session_factory() as db:
        db.add_all([Opportunity(title="Fulbright", eligibility="Minimum CGPA 3.0 out of 4.0.", documents=[{"name": "CV", "status": "needed"}]),
                    Memory(content="CGPA 3.4/4.0", topic="cgpa", category="academic", kind="fact")])
        db.commit()
    client = local_client(app)
    e = client.get("/api/opportunities/1/eligibility").json()
    assert e["checks"][0]["status"] == "met" and e["verdict"] == "Every requirement Keel could check is met."
    r = client.get("/api/opportunities/1/readiness").json()
    assert r["still_needed"] == ["CV"]
    assert client.get("/api/opportunities/9/eligibility").status_code == 404
