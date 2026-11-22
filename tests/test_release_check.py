"""The release check script: its local checks pass on a fresh install, never touch the real database, and skip (not
fail) what isn't set up. The live and interactive parts need real accounts and are run by hand (docs/RELEASE_CHECKLIST.md)."""
from __future__ import annotations

import json

from app.config import Settings
from app.db.session import init_db, make_engine
from scripts import release_check as rc


def _settings(tmp_path, **kw):
    db = tmp_path / "data" / "agent.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    init_db(engine)
    engine.dispose()
    return Settings(_env_file=None, database_url=f"sqlite:///{db.as_posix()}", anthropic_api_key=None, agent_timezone="Asia/Karachi", **kw), db


def test_local_checks_pass_on_a_fresh_install_and_leave_the_database_alone(tmp_path):
    s, db = _settings(tmp_path)
    before = db.read_bytes()
    rows = rc.run_all(s, live=False, interactive_steps=False)
    status = {name: st for _, name, st, _ in rows}
    assert status == {"database upgrade on a copy": "PASS", "encrypted backup round trip": "PASS",
                      "restart with approvals waiting": "PASS", "two schedulers, one database": "PASS",
                      "no internet: model": "SKIP", "no internet: Google": "SKIP"}, rows
    assert db.read_bytes() == before


def test_interactive_steps_skip_what_isnt_set_up_and_ask_before_anything_else(tmp_path, monkeypatch):
    s, _ = _settings(tmp_path)
    asked = []
    monkeypatch.setattr(rc, "_ask", lambda q: asked.append(q) or False)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    rows = rc.run_all(s, live=False, interactive_steps=True)
    you = [(name, st) for section, name, st, _ in rows if section == "you"]
    assert you == [("gmail", "SKIP"), ("google calendar", "SKIP"), ("browser", "SKIP")]
    assert asked == []  # nothing is configured, so there was nothing to send or change


def test_results_are_saved_without_secrets(tmp_path):
    s, _ = _settings(tmp_path, groq_api_key="gsk_live_SECRET_value_123", llm_provider="groq")
    rows = [("live", "model groq", "FAIL", rc._scrub("401 for key gsk_live_SECRET_value_123", s))]
    path = rc.save(rows, s)
    text = path.read_text(encoding="utf-8")
    assert "SECRET" not in text and json.loads(text)["results"][0]["status"] == "FAIL"
