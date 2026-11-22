"""Capture inbox: rule-based suggestions (no model call), and nothing is saved until the user files it, through the
same tools as chat (validated, audited as the user, refused when that integration is switched off)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.api.main import create_app
from app.config import Settings
from app.db.models import AuditLog, InboxItem, Memory, Project, ProjectEntry, Reminder, Task
from app.services.capture import parse_when, suggest
from tests.fakes import ScriptedLLM, local_client

NOW = datetime(2026, 11, 9, 9, 30, tzinfo=ZoneInfo("Asia/Karachi"))  # a Monday morning
FYP = [(1, "FYP: intrusion detection")]


@pytest.mark.parametrize("text,kind,fields", [
    ("I need to renew my passport", "task", {"title": "Renew my passport"}),
    ("submit HEC form by Friday", "task", {"title": "Submit HEC form", "due_date": "2026-11-13"}),
    ("I need to discuss the project with Alex", "task", {"title": "Discuss the project with Alex"}),
    ("remind me tomorrow at 9 to call the bank", "reminder", {"text": "Call the bank", "remind_at": "2026-11-10T09:00"}),
    ("gym at 6pm", "reminder", {"text": "Gym", "remind_at": "2026-11-09T18:00"}),
    ("in 2 hours check the oven", "reminder", {"text": "Check the oven", "remind_at": "2026-11-09T11:30"}),
    ("dentist on 15 dec at 4:30 pm", "reminder", {"text": "Dentist", "remind_at": "2026-12-15T16:30"}),
    ("kal subah Ammi ko call karna", "reminder", {"text": "Ammi ko call karna", "remind_at": "2026-11-10T09:00"}),
    ("remind me to pay rent", "reminder", {"text": "Pay rent", "remind_at": "2026-11-10T09:00"}),  # no time: 9am tomorrow
    ("Remember that my supervisor is Dr. Khan", "memory", {"content": "My supervisor is Dr. Khan", "kind": "fact"}),
    ("I prefer studying late at night", "memory", {"kind": "preference"}),
    ("Remember this business idea: tutoring app for O-levels", "note", {"title": "Business idea: tutoring app for O-levels"}),
    ("Save this article for later https://example.com/a", "note", {"title": "Article for later"}),
    ("FYP is blocked waiting on the dataset", "project_update", {"project_id": 1, "kind": "blocker"}),
    ("FYP: finished the training script", "project_update", {"project_id": 1, "kind": "update"}),
])
def test_suggestions(text, kind, fields):
    s = suggest(text, NOW, FYP)
    assert s["kind"] == kind, s
    assert {k: s["fields"][k] for k in fields} == fields
    assert s["why"] and kind not in s["alternatives"]


def test_without_a_notes_folder_or_projects_those_types_are_not_offered():
    s = suggest("Save this article https://example.com/a", NOW, [], notes=False)
    assert s["kind"] != "note" and "note" not in s["alternatives"] and "project_update" not in s["alternatives"]


def test_dates_said_in_the_past_roll_forward_and_unclear_text_stays_unclear():
    assert parse_when("call at 9am", NOW)[1].date().isoformat() == "2026-11-10"  # 9:00 has passed today, so tomorrow
    assert parse_when("monday standup", NOW)[0].isoformat() == "2026-11-16"  # today is Monday: the next one
    assert parse_when("buy milk", NOW) == (None, None, [])


def _app(**kw):
    settings = Settings(_env_file=None, database_url="sqlite://", anthropic_api_key=None, agent_timezone="Asia/Karachi", **kw)
    app = create_app(settings, ScriptedLLM([]))
    return app, local_client(app)


def test_capturing_saves_nothing_but_the_inbox_item():
    app, client = _app()
    r = client.post("/api/inbox", json={"text": "  remind me tomorrow at 9 to call the bank "})
    assert r.status_code == 201 and r.json()["suggestion"]["kind"] == "reminder" and r.json()["text"] == "remind me tomorrow at 9 to call the bank"
    with app.state.container.session_factory() as db:
        assert db.query(Reminder).count() == 0 and db.query(Task).count() == 0
    assert client.post("/api/inbox", json={"text": "   "}).status_code == 422
    assert [i["id"] for i in client.get("/api/inbox").json()["new"]] == [1]


def test_filing_each_type_through_the_tools_as_the_user(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    app, client = _app(notes_dir=str(notes))
    with app.state.container.session_factory() as db:
        db.add(Project(name="FYP"))
        db.commit()
    cases = [
        ("task", {"title": "Renew my passport", "due_date": "2026-12-01"}, "task #1"),
        ("reminder", {"text": "Call the bank", "remind_at": "2099-01-01T09:00"}, "reminder #1"),
        ("memory", {"content": "Supervisor is Dr. Khan", "kind": "fact"}, "memory #1"),
        ("note", {"title": "Business idea", "content": "Tutoring app for O-levels"}, "note inbox/"),
        ("project_update", {"project_id": 1, "kind": "blocker", "text": "Waiting on the dataset"}, "project update #1"),
    ]
    for kind, fields, label in cases:
        item = client.post("/api/inbox", json={"text": f"something to file as {kind}"}).json()
        r = client.post(f"/api/inbox/{item['id']}/file", json={"kind": kind, "fields": fields})
        assert r.status_code == 200 and r.json()["status"] == "filed" and r.json()["filed_as"].startswith(label), (kind, r.text)
        assert client.post(f"/api/inbox/{item['id']}/file", json={"kind": kind, "fields": fields}).status_code == 409  # only once
    with app.state.container.session_factory() as db:
        assert db.query(Task).one().title == "Renew my passport" and db.query(Reminder).count() == 1
        assert db.query(Memory).one().content == "Supervisor is Dr. Khan" and db.query(ProjectEntry).one().kind == "blocker"
        assert {a.actor for a in db.query(AuditLog)} == {"user"}
    note = next((notes / "inbox").glob("*.md"))
    assert note.read_text(encoding="utf-8").startswith("# Business idea")


def test_a_bad_value_or_a_switched_off_integration_files_nothing():
    app, client = _app()
    item = client.post("/api/inbox", json={"text": "call the bank"}).json()
    r = client.post(f"/api/inbox/{item['id']}/file", json={"kind": "reminder", "fields": {"text": "Call", "remind_at": "2001-01-01T09:00"}})
    assert r.status_code == 422 and "past" in r.json()["detail"]
    assert client.post(f"/api/inbox/{item['id']}/file", json={"kind": "spaceship", "fields": {}}).status_code == 422
    assert client.post(f"/api/inbox/{item['id']}/file", json={"kind": "note", "fields": {"title": "x"}}).status_code == 409  # no NOTES_DIR
    with app.state.container.session_factory() as db:
        assert db.get(InboxItem, item["id"]).status == "new" and db.query(Reminder).count() == 0
    assert client.post(f"/api/inbox/{item['id']}/dismiss").json()["status"] == "dismissed"
    assert client.post(f"/api/inbox/{item['id']}/file", json={"kind": "task", "fields": {"title": "x"}}).status_code == 409
    assert client.post("/api/inbox/999/dismiss").status_code == 404


def test_a_switched_off_integration_files_nothing(tmp_path):
    from app.services.switches import set_switch

    notes = tmp_path / "notes"
    notes.mkdir()
    app, client = _app(notes_dir=str(notes))
    with app.state.container.session_factory() as db:
        set_switch(db, "notes", False)
    item = client.post("/api/inbox", json={"text": "idea: tutoring app"}).json()
    r = client.post(f"/api/inbox/{item['id']}/file", json={"kind": "note", "fields": {"title": "Idea", "content": "Tutoring app"}})
    assert r.status_code == 422 and "switched off" in r.json()["detail"]
    assert not (notes / "inbox").exists()
