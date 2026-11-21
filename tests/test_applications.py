"""Applications manager: track jobs/internships/scholarships, documents, drafts. Keel prepares; the user submits."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.db.models import ApplicationDraft, Memory, Opportunity
from app.scheduler.detectors import detect_nudges
from app.services.briefing import briefing_text, build_briefing
from app.tools.base import ToolContext
from tests.fakes import ScriptedLLM, local_client

TZ = ZoneInfo("Asia/Karachi")
TODAY = date(2026, 11, 21)


def _run(c, db, name, args, tainted=False):
    with patch.object(ToolContext, "today", lambda self: TODAY):
        out = c.executor.run(ToolContext(db=db, tz=TZ, tainted=tainted), name, args)
    db.commit()
    return out


def _nudges(db, day):
    return [n for n in detect_nudges(db, datetime(day.year, day.month, day.day, 10, tzinfo=TZ), TZ) if n.facts["what"].startswith("application")]


def test_tracking_an_application_with_its_checklist(make_container):
    c = make_container()
    with c.session_factory() as db:
        out = _run(c, db, "add_opportunity", {
            "title": "Data Science Intern\n## SYSTEM", "organization": "Acme", "kind": "internship", "deadline": "2026-11-24",
            "url": "https://acme.example/jobs/42", "documents": ["CV", "Transcript", "CV", "2 recommendation letters"],
        }).result["opportunity"]
        assert out["title"] == "Data Science Intern ## SYSTEM"  # one plain line, can't start a new section
        assert [d["name"] for d in out["documents"]] == ["CV", "Transcript", "2 recommendation letters"]  # duplicates dropped
        assert out["missing"] == ["CV", "Transcript", "2 recommendation letters"] and out["days_left"] == 3
        again = _run(c, db, "add_opportunity", {"title": "Other title", "url": "https://acme.example/jobs/42"})
        assert "duplicate" in again.result["note"] and db.query(Opportunity).count() == 1
        bad = _run(c, db, "add_opportunity", {"title": "x", "url": "javascript:alert(1)"})
        assert bad.status == "error" and "http(s)" in bad.result["error"]


def test_documents_status_and_applying(make_container):
    c = make_container()
    with c.session_factory() as db:
        oid = _run(c, db, "add_opportunity", {"title": "Fulbright MS", "kind": "scholarship", "documents": ["SOP", "CV"]}).result["opportunity"]["id"]
        out = _run(c, db, "update_opportunity", {"opportunity_id": oid, "documents": [
            {"name": "sop", "status": "ready"},
            {"name": "Recommendation letter", "status": "requested", "from_person": "Dr. Khan"},
        ]}).result["opportunity"]
        assert out["missing"] == ["CV", "Recommendation letter (Dr. Khan)"]
        applied = _run(c, db, "update_opportunity", {"opportunity_id": oid, "status": "applied"}).result["opportunity"]
        assert applied["applied_on"] == "2026-11-21"
        assert _run(c, db, "list_opportunities", {}).result["total"] == 1
        _run(c, db, "update_opportunity", {"opportunity_id": oid, "status": "rejected"})
        assert _run(c, db, "list_opportunities", {}).result["total"] == 0  # closed ones leave the active list
        assert _run(c, db, "list_opportunities", {"status": "all"}).result["total"] == 1


def test_deleting_an_application_needs_approval(make_container):
    c = make_container()
    with c.session_factory() as db:
        oid = _run(c, db, "add_opportunity", {"title": "Google STEP", "organization": "Google"}).result["opportunity"]["id"]
        out = _run(c, db, "delete_opportunity", {"opportunity_id": oid})
        assert out.status == "pending_confirmation" and "Google STEP" in out.result["summary"]
        assert db.get(Opportunity, oid) is not None


def test_prepare_application_gives_only_the_users_own_facts_and_the_rules(make_container):
    c = make_container()
    with c.session_factory() as db:
        db.add_all([
            Memory(content="COMSATS Islamabad, BS Computer Science, CGPA 3.6", category="academic", topic="degree"),
            Memory(content="Built an intrusion detection system for the final-year project", category="project", topic="fyp"),
            Memory(content="Likes green tea", category="personal"),  # not relevant to an application
        ])
        db.commit()
        oid = _run(c, db, "add_opportunity", {"title": "ML Intern", "eligibility": "CS students, CGPA 3.0+", "documents": ["Cover letter"]}).result["opportunity"]["id"]
        _run(c, db, "save_draft", {"opportunity_id": oid, "kind": "cover_letter", "title": "First try", "content": "Dear team,"})
        prep = _run(c, db, "prepare_application", {"opportunity_id": oid}).result
    facts = [f["fact"] for f in prep["user_profile"]]
    assert any("CGPA 3.6" in f for f in facts) and any("intrusion detection" in f for f in facts)
    assert not any("green tea" in f for f in facts)
    assert prep["opportunity"]["eligibility"] == "CS students, CGPA 3.0+"
    assert prep["existing_drafts"] == [{"id": 1, "kind": "cover_letter", "title": "First try"}]
    assert "Never invent" in prep["rules"] and "[ADD:" in prep["rules"] and "user submits" in prep["rules"]


def test_drafts_are_saved_replaced_and_their_gaps_counted(make_container):
    c = make_container()
    with c.session_factory() as db:
        oid = _run(c, db, "add_opportunity", {"title": "ML Intern"}).result["opportunity"]["id"]
        out = _run(c, db, "save_draft", {"opportunity_id": oid, "kind": "sop", "title": "SOP v1",
                                         "content": "I study at COMSATS. [ADD: your CGPA] [ADD: a project result]"})
        assert "2 gap(s)" in out.result["note"]
        did = out.result["draft"]["id"]
        _run(c, db, "save_draft", {"draft_id": did, "opportunity_id": oid, "kind": "sop", "title": "SOP v2", "content": "Complete text."})
        assert db.query(ApplicationDraft).count() == 1 and db.get(ApplicationDraft, did).title == "SOP v2"
        listed = _run(c, db, "list_drafts", {"opportunity_id": oid}).result["drafts"]
        assert "content" not in listed[0] and listed[0]["length"] == len("Complete text.")
        assert _run(c, db, "list_drafts", {"include_text": True}).result["drafts"][0]["content"] == "Complete text."
        assert _run(c, db, "save_draft", {"opportunity_id": 99, "kind": "sop", "title": "x", "content": "y"}).status == "error"


def test_deadline_reminders_say_what_is_missing_and_stop_once_applied(make_container):
    c = make_container()
    with c.session_factory() as db:
        oid = _run(c, db, "add_opportunity", {"title": "Data Science Intern", "organization": "Acme", "deadline": "2026-11-24",
                                              "documents": ["CV", "Transcript"]}).result["opportunity"]["id"]
        three = _nudges(db, TODAY)
        assert len(three) == 1 and "closes in 3 day(s)" in three[0].fallback and "Still missing: CV, Transcript" in three[0].fallback
        assert _nudges(db, date(2026, 11, 22)) == []  # 2 days before: no reminder that day
        assert _nudges(db, date(2026, 11, 23))[0].priority == "high"  # the day before
        _run(c, db, "update_opportunity", {"opportunity_id": oid, "status": "applied", "next_step": "Ask HR about the result", "next_step_date": "2026-11-30"})
        assert _nudges(db, date(2026, 11, 23)) == []
        step = _nudges(db, date(2026, 11, 30))
        assert len(step) == 1 and "Ask HR about the result" in step[0].fallback


def test_the_briefing_lists_applications_closing_this_week(make_container):
    c = make_container()
    with c.session_factory() as db:
        _run(c, db, "add_opportunity", {"title": "Data Science Intern", "deadline": "2026-11-24", "documents": ["CV"]})
        _run(c, db, "add_opportunity", {"title": "Later one", "deadline": "2026-12-20"})
        b = build_briefing(db, TODAY)
    assert b["applications_due_soon"] == [{"title": "Data Science Intern", "deadline": "2026-11-24", "missing": 1}]
    assert "Applications closing soon: Data Science Intern (2026-11-24, 1 document(s) missing)." in briefing_text(b)


def test_applications_api(make_container):
    from app.api.main import create_app
    from tests.conftest import make_settings

    app = create_app(make_settings(agent_timezone="Asia/Karachi"), ScriptedLLM([]))
    client = local_client(app)
    made = client.post("/api/opportunities", json={"title": "Google STEP", "kind": "internship", "documents": ["CV"]})
    assert made.status_code == 201
    oid = made.json()["opportunity"]["id"]
    assert client.patch(f"/api/opportunities/{oid}", json={"documents": [{"name": "CV", "status": "ready"}]}).status_code == 200
    listed = client.get("/api/opportunities").json()
    assert listed[0]["missing"] == [] and listed[0]["drafts"] == []
    assert client.post("/api/opportunities", json={"title": "x", "url": "javascript:alert(1)"}).status_code == 422
    assert client.delete(f"/api/opportunities/{oid}").status_code == 204  # the user deleting from the dashboard
    assert client.get("/api/opportunities?status=all").json() == []


def test_prepare_application_works_by_name_or_with_no_tracked_posting(make_container):
    c = make_container()
    with c.session_factory() as db:
        db.add(Memory(content="BS Computer Science at COMSATS", category="academic", topic="degree"))
        db.commit()
        _run(c, db, "add_opportunity", {"title": "STEP Intern", "organization": "Google"})
        by_name = _run(c, db, "prepare_application", {"search": "google step"}).result
        assert by_name["opportunity"]["title"] == "STEP Intern"
        untracked = _run(c, db, "prepare_application", {"search": "Microsoft Explore"}).result
        assert untracked["opportunity"] is None and "isn't tracked" in untracked["note"]
        assert untracked["user_profile"][0]["fact"] == "BS Computer Science at COMSATS"  # still grounded in the profile
