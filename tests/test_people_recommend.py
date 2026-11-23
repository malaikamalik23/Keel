"""People memory (who someone is, last contact, promises to them) and 'what should I do now' suggestions."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.api.main import create_app
from app.db.models import AuditLog, Commitment, Person, PersonInteraction, Task
from app.services import people as people_svc
from app.services.briefing import briefing_text, build_briefing
from app.services.recommend import free_window, recommend
from app.tools.groups import select_groups
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, local_client
from tests.test_dates_checkins_week import TZ, WED, _run
from tests.test_smart_planner import MEETINGS


def _now(hour: int, minute: int = 0, day: date = WED) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


# ---------- people
def test_name_variants_skip_titles_and_add_first_names():
    assert people_svc.name_variants(Person(name="Dr. Khan", aliases="Khan sahab")) == ["Dr. Khan", "Khan", "Khan sahab"]
    assert people_svc.name_variants(Person(name="Sara Ahmed")) == ["Sara Ahmed", "Sara"]
    assert not people_svc.mentions(Person(name="Dr. Khan"), "Book a dr. appointment")  # a title alone is not the person
    assert not people_svc.mentions(Person(name="Ali"), "Finalise the thesis")  # whole words only
    assert people_svc.mentions(Person(name="Ali"), "Send Ali the slides")


def test_remember_log_and_everything_about_a_person(make_container):
    c = make_container()
    with c.session_factory() as db:
        db.add_all([
            Commitment(text="Send chapter 2 to Dr. Khan", deadline=WED - timedelta(days=1), status="open"),
            Commitment(text="Call Ammi on Sunday", status="open"),
            Task(title="Revise chapter 2", waiting_on="Dr. Khan", follow_up_date=WED),
            Task(title="Print slides for Khan sahab's seminar", due_date=WED),
            Task(title="Email Khan about the old draft", status="done"),
        ])
        db.commit()
        out = _run(c, db, "remember_person", {"name": "Dr.  Khan", "relation": "supervisor", "aliases": ["Khan sahab"], "keep_in_touch_days": 14})
        assert out.status == "ok" and out.result["created"] and out.result["person"]["name"] == "Dr. Khan"
        again = _run(c, db, "remember_person", {"name": "khan sahab", "email": "khan@uni.edu"}).result  # an alias finds the same person
        assert not again["created"] and again["person"]["email"] == "khan@uni.edu" and again["person"]["relation"] == "supervisor"
        assert _run(c, db, "log_contact", {"name": "Dr. Khan", "note": "wants chapter 2 by Friday", "on_date": "2026-11-10"}).status == "ok"
        assert _run(c, db, "log_contact", {"name": "Dr. Khan", "on_date": "2099-01-01"}).status == "error"  # not in the future
        info = _run(c, db, "person_info", {"name": "Khan"}).result
        assert info["last_contact"] == "2026-11-10" and info["days_since_contact"] == 8
        assert [p["text"] for p in info["promises"]] == ["Send chapter 2 to Dr. Khan"] and info["promises"][0]["overdue"]
        assert [w["title"] for w in info["waiting_on_them"]] == ["Revise chapter 2"] and info["waiting_on_them"][0]["follow_up_due"]
        assert [t["title"] for t in info["tasks"]] == ["Print slides for Khan sahab's seminar"]  # done tasks and waits are not repeated
        assert info["interactions"][0]["text"] == "wants chapter 2 by Friday"
        assert _run(c, db, "person_info", {"name": "Nobody"}).status == "error"
        listed = _run(c, db, "list_people", {}).result["people"]
        assert listed[0]["promises"] == 1 and listed[0]["overdue_promises"] == 1 and listed[0]["follow_ups_due"] == 1
        assert not listed[0].get("reconnect_due")  # 8 days since, every 14 wanted


def test_people_endpoints_add_contact_undo_edit_and_forget():
    app = create_app(make_settings(), ScriptedLLM([]))
    client = local_client(app)
    assert client.post("/api/people", json={}).status_code == 422
    sara = client.post("/api/people", json={"name": "Sara Ahmed", "relation": "friend", "keep_in_touch_days": 7}).json()
    pid = sara["id"]
    assert sara["reconnect_due"] and client.get("/api/people").json()[0]["reconnect_due"]  # never contacted yet
    after = client.post(f"/api/people/{pid}/contacts", json={"note": "coffee"}).json()
    assert after["days_since_contact"] == 0 and "reconnect_due" not in after
    client.delete(f"/api/person-contacts/{after['interactions'][0]['id']}")  # Undo
    assert client.get(f"/api/people/{pid}").json()["last_contact"] is None
    edited = client.patch(f"/api/people/{pid}", json={"aliases": ["Saru"], "keep_in_touch_days": None, "notes": "Likes chai"}).json()
    assert edited["aliases"] == ["Saru"] and edited["keep_in_touch_days"] is None and edited["notes"] == "Likes chai"
    client.post("/api/people", json={"name": "Ali"})
    assert client.patch(f"/api/people/{pid}", json={"name": "ali"}).status_code == 409
    assert client.patch(f"/api/people/{pid}", json={"keep_in_touch_days": 0}).status_code == 422
    assert client.patch(f"/api/people/{pid}", json={}).status_code == 422
    assert client.delete(f"/api/people/{pid}").status_code == 204 and client.get(f"/api/people/{pid}").status_code == 404
    with app.state.container.session_factory() as db:
        assert db.query(PersonInteraction).count() == 0
        assert {a.tool_name for a in db.query(AuditLog)} >= {"remember_person", "log_contact", "update_person", "delete_contact", "delete_person"}
        assert {a.actor for a in db.query(AuditLog)} == {"user"}


def test_briefing_and_chat_routing_know_about_people(make_container):
    c = make_container()
    with c.session_factory() as db:
        db.add(Person(name="Ammi", keep_in_touch_days=3, last_contact=WED - timedelta(days=5)))
        db.commit()
        b = build_briefing(db, WED)
        assert b["people_to_reconnect"] == [{"name": "Ammi", "days_since": 5}]
        assert "Get in touch with: Ammi (5 days)." in briefing_text(b)
    everything = {"tasks", "people", "goals", "memory"}
    assert "people" in select_groups("did I send Sara the slides?", set(), everything, person_names=["Sara"])
    assert "people" not in select_groups("plan my sarabande practice", set(), everything, person_names=["Sara"])
    assert "people" in select_groups("when did I last talk to my supervisor", set(), everything)


# ---------- what to do now
def test_suggestions_weigh_deadlines_time_and_energy(make_container):
    c = make_container()
    with c.session_factory() as db:
        db.add_all([
            Task(title="Write thesis chapter", priority="high", estimated_minutes=120, due_date=WED + timedelta(days=2)),
            Task(title="Reply to the bursary office", estimated_minutes=10, due_date=WED),
            Task(title="Tidy notes", priority="low", estimated_minutes=15),
            Task(title="Blocked by the reply", blocked_by_id=None, waiting_on="Registrar"),  # waiting: not a task to do now
        ])
        db.commit()
        tired = recommend(db, _now(20), energy="low", minutes=20)
        titles = [s["title"] for s in tired["suggestions"]]
        assert titles[0] == "Reply to the bursary office" and "short and light, good for low energy" in tired["suggestions"][0]["reasons"]
        assert titles.index("Write thesis chapter") > titles.index("Tidy notes")  # a 2-hour task sinks when tired with 20 min
        assert all("Blocked by the reply" not in t for t in titles) and tired["note"].startswith("Low energy")
        fresh = recommend(db, _now(9), energy="high", minutes=60)
        chapter = next(s for s in fresh["suggestions"] if s["title"] == "Write thesis chapter")
        assert chapter["action"] == "start" and any("do the first 1 h" in r for r in chapter["reasons"])
        assert any(r == "a big one, while you have the energy" for r in chapter["reasons"])


def test_suggestions_include_follow_ups_promises_people_and_plan(make_container):
    c = make_container()
    with c.session_factory() as db:
        t = Task(title="Fix the CSS bug", estimated_minutes=30)
        db.add_all([
            t, Task(title="Revise chapter 2", waiting_on="Dr. Khan", follow_up_date=WED - timedelta(days=2)),
            Commitment(text="Send the form to Hina", deadline=WED, status="open"),
            Person(name="Ammi", keep_in_touch_days=7, last_contact=WED - timedelta(days=9)),
        ])
        db.commit()
        out = recommend(db, _now(15), planned_task_ids={t.id})
        kinds = {s["kind"]: s for s in out["suggestions"]}
        assert set(kinds) == {"task", "follow_up", "promise", "person"}
        assert out["suggestions"][0]["kind"] == "promise" and kinds["promise"]["reasons"] == ["you promised this, due today"]
        assert "in today's plan" in kinds["task"]["reasons"]
        assert kinds["follow_up"]["title"] == "Follow up with Dr. Khan: Revise chapter 2" and "2 days ago" in kinds["follow_up"]["reasons"][0]
        assert kinds["person"]["reasons"] == ["last talked 9 days ago", "you wanted to every 7 days"]
    c2 = make_container()  # nothing at all to do
    with c2.session_factory() as db:
        assert recommend(db, _now(21), energy="low")["note"].startswith("Nothing is waiting on you")


def test_free_window_uses_the_next_meeting_and_hides_its_title():
    src = lambda: MEETINGS  # noqa: E731 - meetings 10:00-11:00 and 14:00-15:30
    assert free_window(src, _now(9, 15), TZ, time(21, 0)) == {"minutes": 45, "until": "10:00", "busy_now": False}
    during = free_window(src, _now(10, 30), TZ, time(21, 0))
    assert during["busy_now"] and during["busy_until"] == "11:00" and during["until"] == "14:00"
    assert free_window(src, _now(16), TZ, time(17, 0))["minutes"] == 60  # end of the working day
    assert free_window(lambda: "not a calendar", _now(9), TZ, None) == {"minutes": None, "until": None, "busy_now": False}
    assert "evil" not in str(free_window(src, _now(13), TZ, None)) and "Lab" not in str(free_window(src, _now(13), TZ, None))


def test_recommendations_endpoint_and_what_next_tool(make_container):
    app = create_app(make_settings(), ScriptedLLM([]))
    client = local_client(app)
    client.post("/api/tasks", json={"title": "Quick email", "priority": "high"})
    r = client.get("/api/recommendations?energy=low&minutes=15").json()
    assert r["energy"] == "low" and r["minutes"] == 15 and r["suggestions"][0]["title"] == "Quick email"
    assert client.get("/api/recommendations?energy=sleepy").status_code == 422
    assert client.get("/api/recommendations?minutes=1").status_code == 422
    c = make_container()
    with c.session_factory() as db:
        db.add(Task(title="Read paper", estimated_minutes=40))
        db.commit()
        out = _run(c, db, "what_next", {"minutes": 45, "energy": "ok"}).result
        assert out["suggestions"][0]["title"] == "Read paper" and "fits in your 45 min" in out["suggestions"][0]["reasons"]


def test_a_task_that_does_not_fit_is_not_called_a_good_low_energy_pick(make_container):
    c = make_container()
    with c.session_factory() as db:
        db.add(Task(title="Tidy the reading list", estimated_minutes=30))
        db.commit()
        reasons = recommend(db, _now(20), energy="low", minutes=15)["suggestions"][0]["reasons"]
        assert reasons == ["needs ~30 min, more than you have"]
