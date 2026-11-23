"""Release checks: what must work before a version is tagged, run against THIS machine and YOUR services.

    python -m scripts.release_check                  # local checks + live read-only checks (nothing is sent or changed)
    python -m scripts.release_check --local          # local checks only: no network, no keys used
    python -m scripts.release_check --interactive    # + guided checks that send or change something, each after you type yes

Local checks work on throwaway copies, never on your data: the database upgrade, an encrypted backup round trip, a
restart while an approval and a workflow step wait, two schedulers on one database, and what happens with no internet
(every request is pointed at a closed port on this computer).

Live checks make one read-only request per configured service (the same as the Services tab), one tiny real chat with
each configured model (a few tokens), and one request with a deliberately wrong key to show the error is clear.

Interactive checks run Keel's real tools on a scratch copy of the database, so approvals work exactly as in the app:
a Gmail draft to yourself, one approved email to yourself, a Google Calendar test event added, moved and deleted, the
browser reading a page you choose (never submitting), and one test email. Each says exactly what it
will do and waits for "yes"; anything else skips it.

Results are printed and saved (no secrets) to data/release-checks/<UTC time>.json. Exit code 1 if anything failed.
See docs/RELEASE_CHECKLIST.md for the full checklist, including the parts only a person can check.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
Row = tuple[str, str, str, str]  # (section, name, status, detail)


class Skipped(Exception):
    pass


class StubLLM:
    """For local checks: never called over the network."""

    def complete(self, **_):
        from app.llm.base import LLMResponse

        return LLMResponse([{"type": "text", "text": "ok"}], "end_turn")


def _scrub(text: str, s: Settings) -> str:
    from app.services.connections import scrub

    return scrub(text, s)


def _settings_for(path: Path, s: Settings, **kw) -> Settings:
    """The user's settings, pointed at a scratch database (and with the scheduler off)."""
    data = {k: v for k, v in s.model_dump().items() if k not in kw}
    data.update(database_url=f"sqlite:///{path.as_posix()}", scheduler_enabled=False, **kw)
    return Settings(_env_file=None, **data)


def _real_db(s: Settings) -> Path | None:
    from app.services.backups import BackupError, sqlite_path

    try:
        p = sqlite_path(s.database_url)
    except BackupError:
        return None
    return p if p.exists() else None


def _copy_db(src: Path, dst: Path) -> None:
    a = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)  # read-only: the real database is never changed
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()


def _counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                            "AND name NOT LIKE '%fts%' AND name != 'alembic_version'")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}  # noqa: S608 # nosec B608 - names from sqlite_master
    finally:
        con.close()


# ------------------------------------------------------------------ local checks (no network)
def local_db_upgrade(s: Settings, tmp: Path) -> str:
    from app.db.session import init_db, make_engine

    src = _real_db(s)
    if src is None:
        raise Skipped("no database yet (it is created on first start)")
    copy = tmp / "upgrade.db"
    _copy_db(src, copy)
    before = _counts(copy)
    rev = sqlite3.connect(copy).execute("SELECT version_num FROM alembic_version").fetchone()
    engine = make_engine(f"sqlite:///{copy.as_posix()}")
    init_db(engine)
    engine.dispose()
    after = _counts(copy)
    con = sqlite3.connect(copy)
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    broken = con.execute("PRAGMA foreign_key_check").fetchall()
    new_rev = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    con.close()
    changed = [t for t, n in before.items() if after.get(t) != n]
    if integrity != "ok" or broken or changed:
        raise RuntimeError(f"integrity {integrity}, {len(broken)} broken reference(s), rows changed in {changed}")
    return f"a copy of your database upgrades {rev[0] if rev else 'new'} -> {new_rev}; no rows changed; integrity ok"


def local_backup_roundtrip(s: Settings, tmp: Path) -> str:
    import secrets

    from app.services import backups

    src = _real_db(s) or _fresh_db(tmp)
    copy = tmp / "backup-src.db"
    _copy_db(src, copy)
    passphrase = secrets.token_urlsafe(24)  # throwaway: never printed or saved
    target = backups.create_backup(copy, tmp / "backups", passphrase, keep=0)
    summary = backups.verify_backup(target, passphrase)
    restored = tmp / "restored" / "agent.db"
    backups.restore_backup(target, passphrase, restored)
    if _counts(restored) != _counts(copy):
        raise RuntimeError("the restored database differs from the original")
    try:
        backups.verify_backup(target, passphrase + "x")
        raise RuntimeError("a wrong passphrase was accepted")
    except backups.BackupError:
        pass
    return f"encrypted backup ({target.stat().st_size // 1024} KB) verifies, restores identical, refuses a wrong passphrase; {summary['db_bytes'] // 1024} KB database"


def _fresh_db(tmp: Path) -> Path:
    from app.db.session import init_db, make_engine

    path = tmp / "fresh.db"
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    init_db(engine)
    engine.dispose()
    return path


def local_restart(s: Settings, tmp: Path) -> str:
    from app.container import build_container
    from app.db.models import PendingAction, Task, Workflow, WorkflowRun
    from app.tools.base import ToolContext

    path = tmp / "restart.db"
    cfg = _settings_for(path, Settings(_env_file=None), agent_timezone=s.agent_timezone)
    tz = ZoneInfo(s.agent_timezone)
    first = build_container(cfg, llm=StubLLM())
    with first.session_factory() as db:
        out = first.executor.run(ToolContext(db=db, tz=tz, tainted=True), "create_task", {"title": "release check"})
        db.commit()
        w = Workflow(name="release check", trigger={"type": "manual"}, steps=[{"tool": "create_task", "args": {"title": "after restart"}, "approve": True}])
        db.add(w)
        db.commit()
        run = first.workflows.start(db, w, "manual:release", {"type": "manual"})
        first.workflows.advance(db, run, datetime.now(tz))
        pid, rid = out.pending_action_id, run.id
    first.engine.dispose()
    second = build_container(cfg, llm=StubLLM())
    with second.session_factory() as db:
        if db.get(PendingAction, pid).status != "pending":
            raise RuntimeError("the approval was lost in the restart")
        if second.executor.confirm(ToolContext(db=db, tz=tz), pid).status != "ok":
            raise RuntimeError("the approval couldn't be answered after the restart")
        db.commit()
        second.workflows.approve(db, db.get(WorkflowRun, rid), datetime.now(tz))
        titles = sorted(t.title for t in db.query(Task))
    second.engine.dispose()
    if titles != ["after restart", "release check"]:
        raise RuntimeError(f"unexpected tasks after restart: {titles}")
    return "an approval and a workflow step waiting before a restart are both answerable after it, and run once"


def local_two_schedulers(s: Settings, tmp: Path) -> str:
    from app.container import build_container

    cfg = _settings_for(tmp / "lease.db", Settings(_env_file=None), agent_timezone=s.agent_timezone)
    a, b = build_container(cfg, llm=StubLLM()), build_container(cfg, llm=StubLLM())
    now = datetime.now(ZoneInfo(s.agent_timezone))
    first, second = a.scheduler.tick(now), b.scheduler.tick(now)
    a.scheduler.stop()
    third = b.scheduler.tick(now)
    for c in (a, b):
        c.engine.dispose()
    if "skipped" in first or second.get("skipped") != "another_scheduler" or "skipped" in third:
        raise RuntimeError(f"lease not respected: {first}, {second}, {third}")
    return "a second scheduler on the same database stands aside, and takes over when the first stops"


@contextmanager
def _no_internet():
    """Every HTTP(S) request goes to a closed port on this computer, as if the network were down."""
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys[:6]:
        os.environ[k] = "http://127.0.0.1:9"
    os.environ.pop("NO_PROXY", None)
    os.environ.pop("no_proxy", None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def local_no_internet_model(s: Settings, tmp: Path) -> str:
    from app.errors import LLMError
    from app.llm.factory import _build, resolve_provider

    provider = resolve_provider(s)
    if provider is None:
        raise Skipped("no model configured")
    if provider == "ollama":
        raise Skipped("Ollama runs on this computer and doesn't need the internet")
    with _no_internet():
        llm = _build(s, provider)
        started = time.monotonic()
        try:
            llm.complete(system="Reply with OK.", messages=[{"role": "user", "content": [{"type": "text", "text": "OK?"}]}], tools=[])
        except LLMError as exc:
            took = time.monotonic() - started
            return f"{provider}: a clear error after {took:.0f} s ({_scrub(str(exc), s)[:100]}), marked retryable={exc.retryable}"
    raise RuntimeError("a request succeeded with the network blocked")


def local_no_internet_google(s: Settings, tmp: Path) -> str:
    from app.services.connections import gmail_connected

    if not s.gmail_client_id or not gmail_connected(s):
        raise Skipped("Google isn't connected")
    from app.integrations.gmail import GmailClient, GmailError
    from app.integrations.google_auth import GoogleError

    with _no_internet():
        client = GmailClient(s.gmail_client_id, s.gmail_client_secret, Path(s.gmail_token_path))
        try:
            client.profile()
        except (GmailError, GoogleError) as exc:
            return f"Gmail: {_scrub(str(exc), s)}"
    raise RuntimeError("Gmail answered with the network blocked")


# ------------------------------------------------------------------ live read-only checks
def live_services(s: Settings) -> list[tuple[str, str, str]]:
    from app.services.connections import CHECKS, run_check

    rows = []
    for name in CHECKS:
        r = run_check(name, s)
        rows.append((name, PASS if r["ok"] else SKIP if r["ok"] is None else FAIL, r["detail"]))
    return rows


def _chat_once(llm) -> tuple[str, float]:
    started = time.monotonic()
    resp = llm.complete(system="You are a test. Reply with exactly: OK", tools=[],
                        messages=[{"role": "user", "content": [{"type": "text", "text": "Reply with OK."}]}])
    return resp.text, time.monotonic() - started


def live_models(s: Settings) -> list[tuple[str, str, str]]:
    from app.errors import LLMError
    from app.llm.factory import _build, resolve_provider

    providers = [p for p in dict.fromkeys([resolve_provider(s), s.llm_fallback]) if p and p != "none"]
    rows = []
    for p in providers:
        try:
            reply, took = _chat_once(_build(s, p))
            rows.append((f"model {p}", PASS if reply else FAIL, f"answered {reply[:20]!r} in {took:.1f} s"))
        except LLMError as exc:
            status = SKIP if exc.retryable else FAIL
            rows.append((f"model {p}", status, ("busy or rate-limited: " if exc.retryable else "") + _scrub(str(exc), s)[:160]))
    if providers and providers[0] != "ollama":
        p = providers[0]
        bad = Settings(_env_file=None, **{**s.model_dump(), f"{p}_api_key": "keel-release-check-not-a-real-key"})
        try:
            _chat_once(_build(bad, p))
            rows.append((f"wrong {p} key", FAIL, "a made-up key was accepted"))
        except LLMError as exc:
            ok = not exc.retryable
            rows.append((f"wrong {p} key", PASS if ok else FAIL, f"refused with a clear error: {str(exc)[:120]}"))
    return rows


# ------------------------------------------------------------------ interactive checks (send or change something)
def _ask(question: str) -> bool:
    try:
        return input(f"\n{question}\nType yes to do it, anything else to skip: ").strip().lower() == "yes"
    except EOFError:
        return False


def interactive(s: Settings, tmp: Path) -> list[tuple[str, str, str]]:
    from app.container import build_container
    from app.db.models import PendingAction
    from app.tools.base import ToolContext

    src = _real_db(s)
    scratch = tmp / "interactive.db"
    if src:
        _copy_db(src, scratch)
    c = build_container(_settings_for(scratch, s))
    tz = ZoneInfo(s.agent_timezone)
    rows: list[tuple[str, str, str]] = []
    names = {t.name for t in c.registry.all()}

    def run(name: str, args: dict, label: str) -> tuple[str, dict]:
        with c.session_factory() as db:
            out = c.executor.run(ToolContext(db=db, tz=tz), name, args)
            db.commit()
            if out.status == "pending_confirmation":
                summary = db.get(PendingAction, out.pending_action_id).summary
                if not _ask(f"{label}. Keel's approval shows:\n---\n{summary}\n---\nApprove it?"):
                    db.get(PendingAction, out.pending_action_id).status = "rejected"
                    db.commit()
                    return SKIP, {"error": "you didn't approve it"}
                out = c.executor.confirm(ToolContext(db=db, tz=tz), out.pending_action_id)
                db.commit()
            return (PASS if out.status == "ok" else FAIL), out.result

    stamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    if "gmail_draft" in names:
        from app.integrations.gmail import GmailClient

        address = GmailClient(s.gmail_client_id or "", s.gmail_client_secret, Path(s.gmail_token_path)).me()
        mail = {"to": address, "subject": f"Keel release check {stamp}", "body": "This is a test from python -m scripts.release_check."}
        if _ask(f"Gmail: save a draft to yourself ({address}), subject 'Keel release check {stamp}'. Nothing is sent."):
            st, res = run("gmail_draft", mail, "Gmail draft")
            rows.append(("gmail draft to yourself", st, "saved; check your Drafts folder" if st == PASS else str(res)[:160]))
        if "gmail_send" in names and _ask(f"Gmail: send ONE email to yourself ({address}) through Keel's approval flow?"):
            st, res = run("gmail_send", mail, "Gmail send")
            rows.append(("gmail approved send to yourself", st, "sent; check your inbox" if st == PASS else str(res)[:160]))
    else:
        rows.append(("gmail", SKIP, "Gmail isn't connected"))

    if "gcal_create_event" in names:
        day = (datetime.now(tz) + timedelta(days=1)).date().isoformat()
        if _ask(f"Google Calendar: add a 15-minute event 'Keel release check' on {day} at 23:00, then move it to 23:30, "
                "then delete it. Each step shows its approval."):
            st, res = run("gcal_create_event", {"title": "Keel release check", "start": f"{day}T23:00", "duration_minutes": 15}, "Add event")
            rows.append(("calendar add", st, str(res)[:160] if st != PASS else "added"))
            eid = (res.get("created") or {}).get("event_id") if st == PASS else None
            if eid:
                st, res = run("gcal_update_event", {"event_id": eid, "start": f"{day}T23:30"}, "Move event")
                rows.append(("calendar move", st, str(res)[:160] if st != PASS else "moved"))
                st, res = run("gcal_delete_event", {"event_id": eid}, "Delete event")
                rows.append(("calendar delete", st, str(res)[:160] if st != PASS else "deleted"))
    else:
        rows.append(("google calendar", SKIP, "Google Calendar isn't enabled or connected"))

    if "browser_open" in names:
        try:
            url = input("\nBrowser: a page to read (an application portal, say), or Enter to skip: ").strip()
        except EOFError:
            url = ""
        if url:
            st, res = run("browser_open", {"url": url}, "Open page")
            detail = "read the page; nothing was typed or submitted" if st == PASS else str(res)[:160]
            rows.append(("browser reads a real page", st, detail))
            run("browser_close", {}, "Close")
    else:
        rows.append(("browser", SKIP, "BROWSER_ENABLED is false"))

    from app.integrations.push import CombinedNotifier, PushNotifier

    parts = c.notifier.parts if isinstance(c.notifier, CombinedNotifier) else [c.notifier]
    usable = [p for p in parts if p.channel != "log" and not (isinstance(p, PushNotifier) and p.devices() == 0)]
    for p in usable:  # only channels that can actually deliver: email if set up, push if a device signed up
        if _ask(f"Messages: send one test message by {p.channel} to you?"):
            try:
                p.send(f"Keel release check {stamp}: if you can read this, messages work.")
                rows.append((f"{p.channel} message", PASS, "sent; check that it arrived"))
            except Exception as exc:  # noqa: BLE001
                rows.append((f"{p.channel} message", FAIL, _scrub(str(exc), s)[:160]))
    c.engine.dispose()
    return rows


# ------------------------------------------------------------------ running
LOCAL: list[tuple[str, Callable[[Settings, Path], str]]] = [
    ("database upgrade on a copy", local_db_upgrade),
    ("encrypted backup round trip", local_backup_roundtrip),
    ("restart with approvals waiting", local_restart),
    ("two schedulers, one database", local_two_schedulers),
    ("no internet: model", local_no_internet_model),
    ("no internet: Google", local_no_internet_google),
]


def run_all(s: Settings, *, live: bool, interactive_steps: bool) -> list[Row]:
    rows: list[Row] = []
    with tempfile.TemporaryDirectory(prefix="keel-release-") as tmpdir:
        tmp = Path(tmpdir)
        for name, check in LOCAL:
            try:
                rows.append(("local", name, PASS, check(s, tmp)))
            except Skipped as why:
                rows.append(("local", name, SKIP, str(why)))
            except Exception as exc:  # noqa: BLE001 - report every failure, keep going
                rows.append(("local", name, FAIL, _scrub(f"{type(exc).__name__}: {exc}", s)[:200]))
        if live:
            for section, fn in (("live", live_services), ("live", live_models)):
                try:
                    rows += [(section, *r) for r in fn(s)]
                except Exception as exc:  # noqa: BLE001
                    rows.append((section, fn.__name__, FAIL, _scrub(f"{type(exc).__name__}: {exc}", s)[:200]))
        if interactive_steps:
            rows += [("you", *r) for r in interactive(s, tmp)]
    return rows


def save(rows: list[Row], s: Settings) -> Path | None:
    from app.services.backups import BackupError, sqlite_path

    try:
        folder = sqlite_path(s.database_url).parent / "release-checks"
    except BackupError:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    from app.llm.factory import model_name

    path.write_text(json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), "model": model_name(s),
                                "results": [dict(zip(("section", "check", "status", "detail"), r, strict=True)) for r in rows]},
                               indent=1), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local", action="store_true", help="local checks only (no network, no keys)")
    ap.add_argument("--interactive", action="store_true", help="also the guided checks that send or change something")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to a legacy code page
    s = get_settings()
    rows = run_all(s, live=not args.local, interactive_steps=args.interactive and not args.local)
    width = max(len(r[1]) for r in rows)
    for section, name, status, detail in rows:
        print(f"{section:<6} {name:<{width}}  {status:<5} {detail}")
    saved = save(rows, s)
    failed = sum(r[2] == FAIL for r in rows)
    print(f"\n{sum(r[2] == PASS for r in rows)} passed, {failed} failed, {sum(r[2] == SKIP for r in rows)} skipped"
          + (f". Saved to {saved}" if saved else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
