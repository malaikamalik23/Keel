"""Back up and restore Keel: a consistent copy, safe to take while the server is running.

    python -m scripts.backup                       # -> data/backups/agent-<UTC timestamp>.keelbak (encrypted) or .db
    python -m scripts.backup --keep 30 --dir D:/KeelBackups
    python -m scripts.backup list
    python -m scripts.backup verify data/backups/agent-20261123-031500.keelbak
    python -m scripts.backup restore data/backups/agent-20261123-031500.keelbak     # stop Keel first

With BACKUP_PASSPHRASE in .env (or --ask to type it), a backup is one encrypted file (AES-256-GCM, key from scrypt)
holding the database and, unless BACKUP_INCLUDE_NOTES=false, your notes folder. Without one it is a plain copy of the
database (the old format; `--plain` forces it). Secrets (`.env`, the Google token) are never included: keep `.env` in
your password manager. Uses SQLite's online backup API, so the copy is never half-written even if Keel is writing.

Restore keeps the current database beside it (`agent.before-restore-<stamp>.db`) and copies back only notes that are
missing; it never overwrites a note. On a new device: install Keel, copy `.env`, restore, start Keel, reconnect Google.
"""
from __future__ import annotations

import argparse
import getpass
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.services import backups as B
from app.services.backup_crypto import is_encrypted


def sqlite_path(database_url: str) -> Path:
    try:
        return B.sqlite_path(database_url)
    except B.BackupError as e:
        raise SystemExit(str(e)) from None


def backup(db_path: Path, out_dir: Path, keep: int = 14, now: datetime | None = None) -> Path:
    """A plain (unencrypted) copy of the database: the format from before encrypted backups."""
    if not db_path.exists():
        raise SystemExit(f"No database at {db_path}.")
    if not out_dir.exists():
        out_dir.mkdir(parents=True, mode=0o700)  # a new backup folder is private to you (no effect on Windows)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{db_path.stem}-{stamp}.db"
    try:
        B.consistent_copy(db_path, target)
    except B.BackupError as e:
        target.unlink(missing_ok=True)
        raise SystemExit(str(e)) from None
    old = sorted(out_dir.glob(f"{db_path.stem}-*.db"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    return target


def _passphrase(args, settings, confirm: bool) -> str | None:
    if getattr(args, "ask", False):
        first = getpass.getpass("Backup passphrase: ")
        if confirm and getpass.getpass("Again: ") != first:
            raise SystemExit("The two passphrases differ.")
        return first
    return settings.backup_passphrase


def known_revision(rev: str | None) -> bool:
    """Whether this Keel's migrations know the backup's database version (a newer Keel's backup would not start)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db.session import PROJECT_ROOT

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    try:
        return rev is None or ScriptDirectory.from_config(cfg).get_revision(rev) is not None
    except Exception:  # noqa: BLE001 - an unknown revision id raises
        return False


def _kb(n: int) -> str:
    return f"{max(1, n // 1024):,} KB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="create", choices=["create", "list", "verify", "restore"])
    ap.add_argument("file", nargs="?", type=Path, help="the backup to verify or restore")
    ap.add_argument("--dir", type=Path, default=None, help="where backups go (default: BACKUP_DIR, else data/backups)")
    ap.add_argument("--keep", type=int, default=None, help="how many backups to keep; older ones are deleted (0 = keep all)")
    ap.add_argument("--plain", action="store_true", help="an unencrypted copy of the database only")
    ap.add_argument("--ask", action="store_true", help="type the passphrase instead of reading BACKUP_PASSPHRASE")
    ap.add_argument("--no-notes", action="store_true", help="leave the notes folder out (backup) or don't copy it back (restore)")
    args = ap.parse_args(argv)
    s = get_settings()
    db_path = sqlite_path(s.database_url)
    out_dir = args.dir or B.backup_dir(s)
    keep = s.backup_keep if args.keep is None else args.keep
    try:
        if args.command == "create":
            passphrase = None if args.plain else _passphrase(args, s, confirm=True)
            if not passphrase:
                target = backup(db_path, out_dir, keep=keep)
                print(f"Backed up {db_path} -> {target} ({_kb(target.stat().st_size)}). NOT encrypted: set BACKUP_PASSPHRASE "
                      "for encrypted backups that include your notes.")
                return 0
            notes = Path(s.notes_dir) if s.notes_dir and s.backup_include_notes and not args.no_notes else None
            target = B.create_backup(db_path, out_dir, passphrase, notes_dir=notes, keep=keep)
            print(f"Backed up {db_path}{' and ' + str(notes) if notes else ''} -> {target} ({_kb(target.stat().st_size)}, encrypted).")
            return 0
        if args.command == "list":
            rows = B.list_backups(out_dir)
            for b in rows:
                print(f"{b.name}  {_kb(b.size)}  {'encrypted' if b.encrypted else 'NOT encrypted'}")
            print(f"{len(rows)} backup(s) in {out_dir}")
            return 0
        if not args.file:
            raise SystemExit(f"Which backup? e.g. python -m scripts.backup {args.command} {out_dir / 'agent-<stamp>.keelbak'}")
        if not args.file.is_file():
            raise SystemExit(f"No file at {args.file}.")
        if not is_encrypted(args.file):
            if args.command == "verify":
                raise SystemExit("That's a plain database copy, not an encrypted backup; open it with sqlite3 to check it.")
            kept = None
            if db_path.exists():
                kept = db_path.with_name(f"{db_path.stem}.before-restore-{datetime.now(UTC):%Y%m%d-%H%M%S}{db_path.suffix}")
                shutil.copy2(db_path, kept)
            shutil.copyfile(args.file, db_path)
            print(f"Restored {args.file} -> {db_path}" + (f" (the previous database is kept as {kept.name})" if kept else ""))
            return 0
        passphrase = _passphrase(args, s, confirm=False)
        if not passphrase:
            raise SystemExit("This backup is encrypted: set BACKUP_PASSPHRASE or pass --ask.")
        if args.command == "verify":
            r = B.verify_backup(args.file, passphrase)
            print(f"OK: {args.file.name} from {r['created']}: database {_kb(r['db_bytes'])}, {r['notes']} note(s)"
                  f"{' (' + _kb(r['notes_bytes']) + ')' if r['notes'] else ''}; every file matches its checksum and the database passes its integrity check.")
            if not known_revision(r["db_revision"]):
                print("Note: it was made by a newer Keel than this one; update Keel before restoring it.")
            return 0
        if not known_revision(B.verify_backup(args.file, passphrase)["db_revision"]):
            raise SystemExit("This backup was made by a newer Keel than this one (unknown database version). Update Keel first.")
        notes = Path(s.notes_dir) if s.notes_dir and not args.no_notes else None
        print("Make sure Keel is stopped (the API, the scheduler and the CLI chat).")
        r = B.restore_backup(args.file, passphrase, db_path, notes_dir=notes)
        print(f"Restored the database from {r['created']} -> {db_path}"
              + (f"; the previous one is kept as {r['previous_db']}" if r["previous_db"] else "") + ".")
        if r["notes"]:
            if notes:
                skipped = len(r["notes_skipped_existing"])
                print(f"Notes: {r['notes_restored']} copied back into {notes}"
                      + (f", {skipped} left as they are (a file already exists there)" if skipped else "")
                      + (f", {len(r['notes_failed'])} couldn't be written (is the folder read-only?)" if r["notes_failed"] else "") + ".")
            else:
                print(f"The backup holds {r['notes']} note(s); none were copied (NOTES_DIR isn't set, or --no-notes).")
        print("Start Keel again. Secrets aren't in backups: check .env, and on a new device reconnect Google "
              "(python -m scripts.gmail_connect).")
        return 0
    except B.BackupError as e:
        raise SystemExit(str(e)) from None


if __name__ == "__main__":
    sys.exit(main())
