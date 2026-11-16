"""Back up Keel's database: a consistent copy, safe to take while the server is running.

    python -m scripts.backup                      # -> data/backups/agent-<UTC timestamp>.db, keeps the newest 14
    python -m scripts.backup --keep 30 --dir D:/KeelBackups

Uses SQLite's online backup API, so the copy is never half-written even if Keel is writing at the same moment (a
plain file copy can be). Only the database is copied: secrets live in `.env` and are deliberately not included.
Restore by stopping Keel and copying a backup over the database file (see SECURITY.md, "Your data").
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings


def sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///") or database_url in ("sqlite:///:memory:",):
        raise SystemExit(f"Only file-based SQLite databases can be backed up this way (got {database_url.split(':')[0]}).")
    return Path(database_url.removeprefix("sqlite:///"))


def backup(db_path: Path, out_dir: Path, keep: int = 14, now: datetime | None = None) -> Path:
    if not db_path.exists():
        raise SystemExit(f"No database at {db_path}.")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{db_path.stem}-{stamp}.db"
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)  # read-only: a backup must never change the source
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SystemExit(f"Backup {target} failed its integrity check.")
        finally:
            dst.close()
    finally:
        src.close()
    old = sorted(out_dir.glob(f"{db_path.stem}-*.db"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=None, help="where to put backups (default: data/backups next to the database)")
    ap.add_argument("--keep", type=int, default=14, help="how many backups to keep; older ones are deleted (0 = keep all)")
    args = ap.parse_args(argv)
    db_path = sqlite_path(get_settings().database_url)
    target = backup(db_path, args.dir or db_path.parent / "backups", keep=args.keep)
    print(f"Backed up {db_path} -> {target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
