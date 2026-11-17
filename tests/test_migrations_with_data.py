"""Upgrading a database that already has data, with foreign keys on, exactly as the app opens it.

Migrations 0007 and 0008 rebuild `conversations` (SQLite can't add a column with a default or drop one in place). The
rebuild drops the old table, which SQLite refuses while `messages` rows point at it and foreign keys are on. The first
real upgrade of a user's database failed that way: the earlier tests only migrated empty databases.
"""
from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.session import PROJECT_ROOT, init_db, make_engine


def _head() -> str:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _db_at(path, revision: str):
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)
    return engine


def _seed(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("INSERT INTO conversations (id, created_at, summary) VALUES (1, '2026-11-10', '- earlier chat'), (2, '2026-11-11', NULL)")
    con.execute("""INSERT INTO messages (conversation_id, role, content, created_at) VALUES
        (1, 'user', '[{"type":"text","text":"hi"}]', '2026-11-10'),
        (2, 'user', '[{"type":"tool_result","content":"<untrusted_content source=\\"web_fetch\\">x</untrusted_content>"}]', '2026-11-11'),
        (2, 'assistant', '[{"type":"text","text":"done"}]', '2026-11-11')""")
    con.execute("""INSERT INTO memories (kind, content, importance, conversation_id, created_at, updated_at) VALUES
        ('preference', 'Studies best after Fajr', 'high', 1, '2026-11-01', '2026-11-05')""")
    con.execute("""INSERT INTO tasks (title, status, priority, postponement_count, created_at, updated_at) VALUES
        ('Write FYP introduction', 'todo', 'high', 2, '2026-11-01', '2026-11-01')""")
    con.commit()
    con.close()


def _state(path):
    con = sqlite3.connect(path)
    out = {
        "version": con.execute("SELECT version_num FROM alembic_version").fetchone()[0],
        "conversations": con.execute("SELECT id, tainted FROM conversations ORDER BY id").fetchall(),
        "messages": con.execute("SELECT count(*) FROM messages").fetchone()[0],
        "tmp": [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE name LIKE '\\_alembic\\_tmp%' ESCAPE '\\'")],
        "fk_problems": con.execute("PRAGMA foreign_key_check").fetchall(),
    }
    con.close()
    return out


def test_upgrading_a_database_with_messages_keeps_every_row_and_foreign_keys(tmp_path):
    db = tmp_path / "agent.db"
    _db_at(db, "0006")
    _seed(db)
    engine = make_engine(f"sqlite:///{db.as_posix()}")  # the app's engine: foreign keys ON
    init_db(engine)
    s = _state(db)
    assert s["version"] == _head() and s["messages"] == 3 and s["tmp"] == [] and s["fk_problems"] == []
    assert s["conversations"] == [(1, 1), (2, 1)]  # 1 had a summary, 2 read external content: both tainted
    con = sqlite3.connect(db)
    memory = con.execute("SELECT content, kind, importance, category, topic, source, confirmed_at FROM memories").fetchall()
    con.close()
    # 0009 kept the memory word for word; it was never told who wrote it, so it says so instead of guessing
    assert memory == [("Studies best after Fajr", "preference", "high", "general", None, "unknown", "2026-11-05")]
    con = sqlite3.connect(db)
    task = con.execute("SELECT title, status, priority, postponement_count, recurrence, blocked_by_id, waiting_on, follow_up_date FROM tasks").fetchall()
    con.close()
    assert task == [("Write FYP introduction", "todo", "high", 2, None, None, None, None)]  # 0010 left it as it was
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").fetchone()[0] == 1  # switched back on for the app


def test_a_rebuild_left_half_done_by_an_earlier_failure_is_cleaned_up(tmp_path):
    """The user's database after the first failed start: a leftover empty copy blocked every later attempt."""
    db = tmp_path / "agent.db"
    _db_at(db, "0006")
    _seed(db)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE _alembic_tmp_conversations (id INTEGER PRIMARY KEY, created_at DATETIME, summary TEXT)")
    con.commit()
    con.close()
    init_db(make_engine(f"sqlite:///{db.as_posix()}"))
    s = _state(db)
    assert s["version"] == _head() and s["tmp"] == [] and s["messages"] == 3


def test_a_leftover_copy_without_its_original_is_never_dropped(tmp_path):
    """If the original table were gone, the copy would be the only one with the data: refuse rather than lose it."""
    db = tmp_path / "agent.db"
    _db_at(db, "0006")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE _alembic_tmp_widgets (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO _alembic_tmp_widgets VALUES (1)")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="no widgets table"):
        init_db(make_engine(f"sqlite:///{db.as_posix()}"))
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM _alembic_tmp_widgets").fetchone()[0] == 1
    con.close()
