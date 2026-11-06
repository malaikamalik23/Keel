from datetime import date, timedelta

from app.agent.context import build_context_pack
from app.db.models import Commitment, Memory, Task
from tests.fakes import ScriptedLLM, text

TODAY = date(2026, 11, 7)


def _db(make_container):
    c = make_container()
    return c, c.session_factory()


def test_empty_database_gives_empty_pack(make_container):
    _, db = _db(make_container)
    assert build_context_pack(db, TODAY, "hello") == ""


def test_pack_flags_overdue_slipping_and_preferences(make_container):
    _, db = _db(make_container)
    db.add_all([
        Task(title="Write FYP intro", due_date=TODAY - timedelta(days=1), postponement_count=3),
        Task(title="Later task", due_date=TODAY + timedelta(days=30)),
        Task(title="Done task", status="done"),
        Commitment(text="Finish GRE quant set", deadline=TODAY + timedelta(days=1), status="open"),
        Commitment(text="Far away promise", deadline=TODAY + timedelta(days=20), status="open"),
        Commitment(text="Not yet confirmed", status="proposed"),
        Memory(kind="preference", content="Focuses best in the morning."),
    ])
    db.commit()
    pack = build_context_pack(db, TODAY, "")
    assert "Write FYP intro" in pack and "OVERDUE" in pack and "postponed 3x" in pack
    assert "Done task" not in pack
    assert "Finish GRE quant set" in pack and "Far away promise" not in pack
    assert "Not yet confirmed" in pack  # surfaced as awaiting confirmation
    assert "Repeatedly postponed" in pack
    assert "Focuses best in the morning." in pack
    # earliest deadline first
    assert pack.index("Write FYP intro") < pack.index("Later task")


def test_only_relevant_non_preference_memories_are_included(make_container):
    _, db = _db(make_container)
    db.add_all([
        Memory(kind="fact", content="FYP supervisor is Dr. Khan."),
        Memory(kind="context", content="Has a cat named Biscuit."),
    ])
    db.commit()
    pack = build_context_pack(db, TODAY, "what did my supervisor say about the FYP?")
    assert "supervisor is Dr. Khan" in pack and "Biscuit" not in pack
    assert "Biscuit" not in build_context_pack(db, TODAY, "hello there")


def test_pack_is_size_capped(make_container):
    _, db = _db(make_container)
    db.add_all([Memory(kind="preference", content="x" * 900) for _ in range(20)])
    db.commit()
    assert len(build_context_pack(db, TODAY, "")) <= 3000


def test_agent_injects_snapshot_into_system_prompt(make_container):
    llm = ScriptedLLM([text("ok")])
    c = make_container(llm=llm)
    with c.session_factory() as db:
        db.add(Task(title="Read the HTTP spec"))
        db.commit()
        c.agent.chat(db, "what should I do?")
    assert "Current snapshot" in llm.calls[0]["system"] and "Read the HTTP spec" in llm.calls[0]["system"]


def test_agent_omits_snapshot_section_when_nothing_to_show(make_container):
    llm = ScriptedLLM([text("ok")])
    c = make_container(llm=llm)
    with c.session_factory() as db:
        c.agent.chat(db, "hi")
    assert "Current snapshot" not in llm.calls[0]["system"]
