"""Encrypted backups: what goes in, what never does, and that tampering, wrong passphrases and hostile files are refused."""
from __future__ import annotations

import io
import os
import sqlite3
import tarfile
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.services import backup_crypto as C
from app.services import backups as B

PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(C, "SCRYPT_LOG_N", 10)  # the real files use 2^17; the value is stored in each header


def _db(path, rows=("Write FYP intro",)):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("CREATE TABLE alembic_version (version_num TEXT)")
    con.execute("INSERT INTO alembic_version VALUES ('0018')")
    con.executemany("INSERT INTO tasks (title) VALUES (?)", [(r,) for r in rows])
    con.commit()
    con.close()
    return path


def _notes(root):
    (root / "applications" / "daad").mkdir(parents=True)
    (root / "journal.md").write_text("Met Dr. Khan today.", encoding="utf-8")
    (root / "applications" / "daad" / "transcript.pdf").write_bytes(os.urandom(200_000))  # several chunks
    (root / ".keel-backups").mkdir()
    (root / ".keel-backups" / "old.md").write_text("old version", encoding="utf-8")
    (root / ".env").write_text("GROQ_API_KEY=gsk_secret", encoding="utf-8")
    return root


def _titles(path):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT title FROM tasks ORDER BY id")]
    finally:
        con.close()


@pytest.mark.parametrize("size", [0, 1, C.CHUNK - 1, C.CHUNK, C.CHUNK + 1, 3 * C.CHUNK])
def test_stream_round_trips_at_every_chunk_boundary(size):
    data = os.urandom(size)
    raw = io.BytesIO()
    w = C.EncryptingWriter(raw, PASS)
    for i in range(0, size, 7000):
        w.write(data[i:i + 7000])
    w.close()
    assert C.DecryptingReader(io.BytesIO(raw.getvalue()), PASS).read() == data


def test_tampering_truncation_and_wrong_passphrases_are_refused():
    raw = io.BytesIO()
    w = C.EncryptingWriter(raw, PASS)
    w.write(os.urandom(3 * C.CHUNK))
    w.close()
    blob = raw.getvalue()
    chunk = C.CHUNK + C.TAG
    cases = {
        "wrong passphrase": (blob, "correct horse battery stapler"),
        "a flipped byte": (blob[:C.HEADER_LEN + chunk + 5] + bytes([blob[C.HEADER_LEN + chunk + 5] ^ 1]) + blob[C.HEADER_LEN + chunk + 6:], PASS),
        "cut at a chunk boundary": (blob[:C.HEADER_LEN + 2 * chunk], PASS),
        "cut mid-chunk": (blob[:-100], PASS),
        "extra bytes": (blob + b"x" * 20, PASS),
        "chunks swapped": (blob[:C.HEADER_LEN] + blob[C.HEADER_LEN + chunk:C.HEADER_LEN + 2 * chunk] + blob[C.HEADER_LEN:C.HEADER_LEN + chunk] + blob[C.HEADER_LEN + 2 * chunk:], PASS),
        "a changed salt": (blob[:12] + bytes([blob[12] ^ 1]) + blob[13:], PASS),
    }
    for name, (data, pw) in cases.items():
        with pytest.raises(C.BackupCryptoError):
            C.DecryptingReader(io.BytesIO(data), pw).read()
            pytest.fail(name)
    with pytest.raises(C.BackupCryptoError, match="at least 12"):
        C.EncryptingWriter(io.BytesIO(), "short")


def test_backup_holds_the_database_and_notes_encrypted_and_never_secrets_or_hidden_files(tmp_path):
    db = _db(tmp_path / "agent.db")
    notes = _notes(tmp_path / "notes")
    out = B.create_backup(db, tmp_path / "backups", PASS, notes_dir=notes)
    assert out.name.endswith(".keelbak") and C.is_encrypted(out) and not list(out.parent.glob("*.partial"))
    blob = out.read_bytes()
    for plain in (b"Write FYP intro", b"Met Dr. Khan", b"gsk_secret", b"SQLite format"):
        assert plain not in blob
    summary = B.verify_backup(out, PASS)
    assert summary["notes"] == 2 and summary["db_revision"] == "0018"  # journal.md and transcript.pdf only

    with pytest.raises(B.BackupError, match="Wrong passphrase"):
        B.verify_backup(out, "not the passphrase at all")


def test_restore_on_a_new_device_and_over_existing_data(tmp_path):
    db = _db(tmp_path / "agent.db")
    notes = _notes(tmp_path / "notes")
    out = B.create_backup(db, tmp_path / "backups", PASS, notes_dir=notes)

    # A new device: no database, empty notes folder.
    new_db, new_notes = tmp_path / "laptop" / "data" / "agent.db", tmp_path / "laptop" / "notes"
    new_notes.mkdir(parents=True)
    r = B.restore_backup(out, PASS, new_db, notes_dir=new_notes)
    assert _titles(new_db) == ["Write FYP intro"] and r["previous_db"] is None and r["notes_restored"] == 2
    assert (new_notes / "applications" / "daad" / "transcript.pdf").read_bytes() == (notes / "applications" / "daad" / "transcript.pdf").read_bytes()
    assert not (new_notes / ".env").exists() and not (new_notes / ".keel-backups").exists()

    # The same device later: the current database is kept, existing notes are never overwritten.
    con = sqlite3.connect(db)
    con.execute("INSERT INTO tasks (title) VALUES ('Added after the backup')")
    con.commit()
    con.close()
    (notes / "journal.md").write_text("Edited after the backup.", encoding="utf-8")
    r = B.restore_backup(out, PASS, db, notes_dir=notes, now=datetime(2026, 11, 23, tzinfo=UTC))
    assert _titles(db) == ["Write FYP intro"]
    assert r["previous_db"] == "agent.before-restore-20261123-000000.db"
    assert _titles(tmp_path / r["previous_db"]) == ["Write FYP intro", "Added after the backup"]
    assert (notes / "journal.md").read_text(encoding="utf-8") == "Edited after the backup." and r["notes_skipped_existing"] == ["applications/daad/transcript.pdf", "journal.md"]
    assert not list(tmp_path.glob(".keel-restore-*"))  # the staging folder is cleaned up


def test_restore_refuses_a_failed_check_without_touching_anything(tmp_path):
    db = _db(tmp_path / "agent.db")
    out = B.create_backup(db, tmp_path / "backups", PASS)
    before = db.read_bytes()
    with pytest.raises(B.BackupError, match="Wrong passphrase"):
        B.restore_backup(out, "a different passphrase", db)
    damaged = tmp_path / "damaged.keelbak"
    damaged.write_bytes(out.read_bytes()[:-40])
    with pytest.raises(B.BackupError):
        B.restore_backup(damaged, PASS, db)
    (tmp_path / "agent.db-journal").write_bytes(b"x")  # Keel is mid-write (or still running)
    with pytest.raises(B.BackupError, match="still be running"):
        B.restore_backup(out, PASS, db)
    assert db.read_bytes() == before and not list(tmp_path.glob("agent.before-restore-*"))


def _hostile(tmp_path, members: dict[str, bytes], manifest: bytes | None = None):
    raw = io.BytesIO()
    w = C.EncryptingWriter(raw, PASS)
    with tarfile.open(fileobj=w, mode="w|gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if manifest is not None:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest)
            tar.addfile(info, io.BytesIO(manifest))
    w.close()
    p = tmp_path / "hostile.keelbak"
    p.write_bytes(raw.getvalue())
    return p


def test_a_crafted_backup_cannot_write_outside_its_folders(tmp_path):
    good = (tmp_path / "good.db")
    _db(good)
    db = _db(tmp_path / "agent.db")
    notes = tmp_path / "notes"
    notes.mkdir()
    for name in ("../evil.py", "notes/../../evil.py", "/etc/evil", "notes/.ssh/authorized_keys", "C:/evil", "other/file"):
        p = _hostile(tmp_path, {"agent.db": good.read_bytes(), name: b"pwned"})
        with pytest.raises(B.BackupError, match="unexpected entry"):
            B.restore_backup(p, PASS, db, notes_dir=notes)
    p = _hostile(tmp_path, {"agent.db": good.read_bytes()}, manifest=b'{"format": 1, "files": []}')
    with pytest.raises(B.BackupError, match="don't match its manifest"):
        B.restore_backup(p, PASS, db, notes_dir=notes)
    assert not (tmp_path.parent / "evil.py").exists() and not any(notes.iterdir())


def test_only_the_newest_encrypted_backups_are_kept(tmp_path):
    db = _db(tmp_path / "agent.db")
    start = datetime(2026, 11, 1, tzinfo=UTC)
    for day in range(4):
        B.create_backup(db, tmp_path / "b", PASS, keep=2, now=start + timedelta(days=day))
    assert [b.name for b in B.list_backups(tmp_path / "b")] == ["agent-20261104-000000.keelbak", "agent-20261103-000000.keelbak"]


def _settings(tmp_path, **kw):
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'data' / 'agent.db').as_posix()}", anthropic_api_key=None, **kw)


def test_cli_creates_verifies_and_restores(tmp_path, monkeypatch, capsys):
    import scripts.backup as cli

    (tmp_path / "data").mkdir()
    _db(tmp_path / "data" / "agent.db")
    notes = _notes(tmp_path / "notes")
    s = _settings(tmp_path, backup_passphrase=PASS, notes_dir=str(notes))
    monkeypatch.setattr(cli, "get_settings", lambda: s)
    monkeypatch.setattr(cli, "known_revision", lambda rev: True)
    assert cli.main([]) == 0
    assert "encrypted" in capsys.readouterr().out
    out = next((tmp_path / "data" / "backups").glob("*.keelbak"))
    assert cli.main(["verify", str(out)]) == 0
    assert "2 note(s)" in capsys.readouterr().out
    (notes / "journal.md").unlink()
    assert cli.main(["restore", str(out)]) == 0
    text = capsys.readouterr().out
    assert "1 copied back" in text and "1 left as they are" in text and (notes / "journal.md").exists()
    assert PASS not in text

    monkeypatch.setattr(cli, "known_revision", lambda rev: False)
    with pytest.raises(SystemExit, match="newer Keel"):
        cli.main(["restore", str(out)])

    plain = _settings(tmp_path)  # no passphrase: the old plain copy, with a warning
    monkeypatch.setattr(cli, "get_settings", lambda: plain)
    assert cli.main([]) == 0 and "NOT encrypted" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="encrypted: set BACKUP_PASSPHRASE"):
        cli.main(["restore", str(out)])


def test_dashboard_backup_endpoints_never_return_the_passphrase(tmp_path):
    from app.api.main import create_app
    from tests.fakes import ScriptedLLM, local_client

    off = local_client(create_app(_settings(tmp_path), ScriptedLLM([])))
    assert off.get("/api/backups").json()["enabled"] is False
    assert off.post("/api/backups").status_code == 409
    rows = {r["id"]: r for r in off.get("/api/services").json()}
    assert rows["backups"]["status"] == "not_set_up"

    on = local_client(create_app(_settings(tmp_path, backup_passphrase=PASS, backup_keep=3), ScriptedLLM([])))
    made = on.post("/api/backups")
    assert made.status_code == 201 and made.json()["name"].endswith(".keelbak")
    listing = on.get("/api/backups")
    assert listing.json()["backups"][0]["encrypted"] is True
    for r in (made, listing, on.get("/api/services")):
        assert PASS not in r.text
    assert B.verify_backup(tmp_path / "data" / "backups" / made.json()["name"], PASS)["db_revision"]  # a real, migrated DB


def test_scheduler_daily_backup(tmp_path):
    from app.scheduler.runner import Scheduler

    (tmp_path / "data").mkdir()
    _db(tmp_path / "data" / "agent.db")
    sched = Scheduler.__new__(Scheduler)
    sched.settings = _settings(tmp_path, backup_passphrase=PASS)
    sched._safe_backup()
    assert len(list((tmp_path / "data" / "backups").glob("*.keelbak"))) == 1
    sched.settings = _settings(tmp_path)  # no passphrase: logged, never raised
    sched._safe_backup()
