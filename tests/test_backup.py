import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scripts.backup import backup, sqlite_path


def _db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("INSERT INTO tasks (title) VALUES ('Write FYP intro')")
    con.commit()
    return con


def test_backup_is_a_complete_copy_taken_while_a_writer_holds_the_database_open(tmp_path):
    src = tmp_path / "agent.db"
    live = _db(src)  # still open, like the running server
    live.execute("INSERT INTO tasks (title) VALUES ('Second')")
    live.commit()

    out = backup(src, tmp_path / "backups")

    copy = sqlite3.connect(out)
    assert [r[0] for r in copy.execute("SELECT title FROM tasks ORDER BY id")] == ["Write FYP intro", "Second"]
    copy.close()
    live.close()


def test_backup_never_modifies_the_source(tmp_path):
    src = tmp_path / "agent.db"
    _db(src).close()
    before = src.read_bytes()
    backup(src, tmp_path / "backups")
    assert src.read_bytes() == before


def test_only_the_newest_backups_are_kept(tmp_path):
    src = tmp_path / "agent.db"
    _db(src).close()
    start = datetime(2026, 11, 1, tzinfo=UTC)
    for day in range(5):
        backup(src, tmp_path / "backups", keep=3, now=start + timedelta(days=day))
    kept = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert kept == ["agent-20261103-000000.db", "agent-20261104-000000.db", "agent-20261105-000000.db"]


def test_only_file_databases_are_accepted(tmp_path):
    assert sqlite_path("sqlite:///./data/agent.db").name == "agent.db"
    for url in ("sqlite://", "sqlite:///:memory:", "postgresql://u@h/db"):
        with pytest.raises(SystemExit):
            sqlite_path(url)
    with pytest.raises(SystemExit, match="No database"):
        backup(tmp_path / "missing.db", tmp_path / "backups")
