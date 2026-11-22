"""Personal onboarding: answers become memories and goals, personal settings override .env and apply without a restart,
and nothing else (keys, addresses, switches) can be changed this way."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from app.api.main import create_app
from app.config import Settings
from app.container import build_container
from app.db.models import Goal, Memory
from tests.fakes import ScriptedLLM, local_client


def _app(tmp_path=None):
    url = f"sqlite:///{(tmp_path / 'p.db').as_posix()}" if tmp_path else "sqlite://"
    return create_app(Settings(_env_file=None, database_url=url, anthropic_api_key=None, agent_timezone="UTC"), ScriptedLLM([]))


def test_first_run_then_answers_become_memories_and_goals():
    app = _app()
    client = local_client(app)
    assert client.get("/api/profile").json()["state"] == "new"
    r = client.post("/api/profile", json={"state": "done", "answers": {
        "name": "Malaika Adnan", "address_as": "Mali", "goals": ["Finish my FYP", "finish my fyp", "Get a funded master's"],
        "responsibilities": ["TA for Data Structures"], "preferences": ["Focus best late at night", "  "]}})
    assert r.status_code == 200 and r.json()["saved"] == {"memories": 4, "goals": 2}
    p = client.get("/api/profile").json()
    assert (p["state"], p["name"], p["address_as"]) == ("done", "Malaika Adnan", "Mali")
    assert p["goals"] == ["Finish my FYP", "Get a funded master's"] and p["preferences"] == ["Focus best late at night"]
    assert p["responsibilities"] == ["TA for Data Structures"]
    with app.state.container.session_factory() as db:
        assert {m.source for m in db.query(Memory)} == {"user"}
        # editing the profile later replaces the name instead of keeping two
        client.post("/api/profile", json={"answers": {"name": "Malaika", "goals": ["Finish my FYP"]}})
        assert [m.content for m in db.query(Memory).filter(Memory.topic == "name")] == ["Name: Malaika"]
        assert db.query(Goal).count() == 2


def test_skipping_is_remembered_and_answers_nothing():
    client = local_client(_app())
    client.post("/api/profile", json={"state": "skipped"})
    p = client.get("/api/profile").json()
    assert p["state"] == "skipped" and p["name"] == "" and p["goals"] == []


def test_personal_settings_apply_at_once_and_survive_a_restart(tmp_path):
    app = _app(tmp_path)
    client = local_client(app)
    old = app.state.container
    r = client.post("/api/profile", json={"settings": {"agent_timezone": "Asia/Karachi", "workday_start": "08:30", "notification_max_per_day": 3}})
    assert r.status_code == 200 and r.json()["settings_changed"] == ["agent_timezone", "notification_max_per_day", "workday_start"]
    new = app.state.container
    assert new is not old and new.engine is old.engine  # same database, rebuilt around it
    assert new.settings.agent_timezone == "Asia/Karachi" and new.agent.tz == ZoneInfo("Asia/Karachi") and new.scheduler.tz == ZoneInfo("Asia/Karachi")
    assert new.settings.workday_start == "08:30" and new.settings.notification_max_per_day == 3
    assert sorted(client.get("/api/profile").json()["overridden"]) == ["agent_timezone", "notification_max_per_day", "workday_start"]
    # a restart reads them back from the database, over .env
    again = build_container(Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'p.db').as_posix()}", anthropic_api_key=None,
                                     agent_timezone="UTC"), llm=ScriptedLLM([]))
    assert again.settings.agent_timezone == "Asia/Karachi" and again.settings.workday_start == "08:30"


def test_bad_or_unsafe_settings_change_nothing():
    app = _app()
    client = local_client(app)
    for bad in ({"agent_timezone": "Mars/Olympus"}, {"workday_start": "25:00"}, {"notification_max_per_day": 99},
                {"api_key": "x"}, {"email_to": "attacker@example.com"}, {"browser_enabled": True},
                {"agent_timezone": "Asia/Karachi", "gmail_send_enabled": True}):
        r = client.post("/api/profile", json={"settings": bad, "answers": {"name": "Should not be saved"}})
        assert r.status_code == 422, bad
    p = client.get("/api/profile").json()
    assert p["overridden"] == [] and p["name"] == "" and app.state.container.settings.agent_timezone == "UTC"
    assert client.post("/api/profile", json={"state": "maybe"}).status_code == 422
