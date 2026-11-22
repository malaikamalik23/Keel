"""Memory lifecycle: confidence, facts that stop being true (kept as history, never treated as current), facts that hold
until a date, the old wording kept when a memory is edited, and links between related memories."""
from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.agent.context import build_context_pack
from app.api.main import create_app
from app.config import Settings
from app.db.models import Memory, utcnow
from app.tools.base import ToolContext
from tests.fakes import ScriptedLLM, local_client

TZ = ZoneInfo("Asia/Karachi")


def _run(c, db, name, args, actor="agent"):
    ctx = ToolContext(db=db, tz=TZ)
    ctx.actor = actor
    out = c.executor.run(ctx, name, args) if actor == "agent" else c.executor.run_as_user(ctx, name, args)
    db.commit()
    return out


def test_a_job_change_marks_the_old_fact_outdated_and_only_the_new_one_is_current(make_container):
    c = make_container()
    with c.session_factory() as db:
        old = _run(c, db, "save_memory", {"content": "Works at Systems Ltd as a QA intern", "category": "professional", "topic": "employer"})
        old_id = old.result["memory"]["id"]
        # the topic is taken, so the new job is a conflict until the old one stops being current
        assert _run(c, db, "save_memory", {"content": "Works at Arbisoft as a junior developer", "category": "professional",
                                            "topic": "employer"}).result["saved"] is False
        assert _run(c, db, "update_memory", {"memory_id": old_id, "status": "outdated"}).status == "ok"
        new = _run(c, db, "save_memory", {"content": "Works at Arbisoft as a junior developer", "category": "professional", "topic": "employer"})
        new_id = new.result["memory"]["id"]
        _run(c, db, "update_memory", {"memory_id": old_id, "replaced_by_id": new_id})
        assert db.get(Memory, old_id).replaced_by_id == new_id

        pack = build_context_pack(db, date(2026, 11, 23), "where do I work?")
        assert "Arbisoft" in pack and "Systems Ltd" not in pack  # the old employer is never "the user's employer"
        found = _run(c, db, "search_memories", {"query": "work"}).result
        assert [m["content"] for m in found["memories"]] == ["Works at Arbisoft as a junior developer"]
        history = _run(c, db, "search_memories", {"query": "work", "include_outdated": True}).result
        assert {m["status"] for m in history["memories"]} == {"current", "outdated"} and "history" in history["note_outdated"]
        # making the old one current again while the new one holds the topic is refused
        back = _run(c, db, "update_memory", {"memory_id": old_id, "status": "current"})
        assert back.status == "error" and "Mark that one outdated first" in back.result["error"]


def test_a_fact_with_an_end_date_stops_being_current_and_is_offered_for_review(make_container):
    c = make_container()
    with c.session_factory() as db:
        today = utcnow().date()
        _run(c, db, "save_memory", {"content": "Staying in Lahore for the internship", "category": "personal", "valid_until": (today - timedelta(days=1)).isoformat()})
        _run(c, db, "save_memory", {"content": "Prefers studying late at night", "kind": "preference", "category": "personal"})
        pack = build_context_pack(db, today, "where am I staying")
        assert "Lahore" not in pack and "late at night" in pack
        review = _run(c, db, "review_memories", {}).result
        assert [m["content"] for m in review["stale"]] == ["Staying in Lahore for the internship"]


def test_confidence_editing_keeps_history_and_low_confidence_is_reviewed(make_container):
    c = make_container()
    with c.session_factory() as db:
        guess = _run(c, db, "save_memory", {"content": "Seems to prefer short replies", "kind": "preference", "confidence": "low"}).result["memory"]
        mine = _run(c, db, "save_memory", {"content": "My CGPA is 3.4", "category": "academic"}, actor="user").result["memory"]
        assert guess["confidence"] == "low" and mine["confidence"] == "high"  # what you type yourself is certain
        assert [m["id"] for m in _run(c, db, "review_memories", {}).result["stale"]] == [guess["id"]]
        _run(c, db, "update_memory", {"memory_id": guess["id"], "confirm": True})
        assert db.get(Memory, guess["id"]).confidence == "high"  # confirmed by the user
        _run(c, db, "update_memory", {"memory_id": mine["id"], "content": "My CGPA is 3.6"}, actor="user")
        old = db.query(Memory).filter_by(status="outdated").one()
        assert (old.content, old.replaced_by_id) == ("My CGPA is 3.4", mine["id"])
        assert "3.4" not in build_context_pack(db, utcnow().date(), "what's my cgpa")


def test_dashboard_memory_filters_lifecycle_fields_and_links():
    app = create_app(Settings(_env_file=None, database_url="sqlite://", anthropic_api_key=None), ScriptedLLM([]))
    client = local_client(app)
    a = client.post("/api/memories", json={"content": "FYP is about phishing detection", "category": "project"}).json()
    b = client.post("/api/memories", json={"content": "Supervisor is Dr. Khan", "category": "academic", "topic": "supervisor"}).json()
    assert a["confidence"] == "high" and a["status"] == "current"
    assert client.post(f"/api/memories/{a['id']}/links", json={"other_id": b["id"], "note": "FYP supervisor"}).json() == {"linked": [b["id"]]}
    client.post(f"/api/memories/{b['id']}/links", json={"other_id": a["id"]})  # the same pair again: still one link
    listed = {m["id"]: m for m in client.get("/api/memories").json()}
    assert listed[a["id"]]["linked"] == [b["id"]] and listed[b["id"]]["linked"] == [a["id"]]
    assert client.post(f"/api/memories/{a['id']}/links", json={"other_id": a["id"]}).status_code == 422
    assert client.post(f"/api/memories/{a['id']}/links", json={"other_id": 999}).status_code == 422

    r = client.patch(f"/api/memories/{b['id']}", json={"status": "outdated"})
    assert r.status_code == 200 and r.json()["status"] == "outdated"
    assert [m["id"] for m in client.get("/api/memories").json()] == [a["id"]]  # current only, by default
    assert [m["id"] for m in client.get("/api/memories?status=outdated").json()] == [b["id"]]
    assert len(client.get("/api/memories?status=all").json()) == 2
    assert client.delete(f"/api/memories/{a['id']}/links/{b['id']}").status_code == 204
    assert client.delete(f"/api/memories/{a['id']}/links/{b['id']}").status_code == 404
