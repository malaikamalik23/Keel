"""Encrypted backups: one file holding a consistent copy of the database and (optionally) your notes folder.

    create_backup(...)  -> data/backups/agent-<UTC timestamp>.keelbak, encrypted with your passphrase, newest N kept
    verify_backup(...)  -> decrypts and checks everything (hashes, database integrity) without restoring anything
    restore_backup(...) -> puts the database back (the current one is kept beside it) and copies back notes that are
                           missing; never overwrites a note that exists

Inside the encryption is a gzipped tar: `agent.db`, `notes/<relative path>` for each note, and `manifest.json` last
(sizes and SHA-256 of every file, the database's schema revision). Secrets are never included: `.env` and the Google
token stay out, so after a restore on a new device you copy `.env` from your password manager and reconnect Google.
Hidden files and folders in the notes folder (`.keel-backups/`, `.git/`, ...) and symlinks are skipped.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.services.backup_crypto import BackupCryptoError, DecryptingReader, EncryptingWriter, check_passphrase

SUFFIX = ".keelbak"
FORMAT = 1


class BackupError(Exception):
    """Something the user can fix: no database, a bad path, a damaged backup, Keel still running."""


@dataclass
class BackupInfo:
    name: str
    size: int
    created: str
    encrypted: bool


def consistent_copy(db_path: Path, target: Path) -> None:
    """SQLite's online backup API: never half-written even if Keel writes at the same moment; the source is opened
    read-only. The target is created readable by its owner only before any data goes in."""
    if not db_path.exists():
        raise BackupError(f"No database at {db_path}.")
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError(f"The copy of {db_path.name} failed its integrity check.")
        finally:
            dst.close()
    finally:
        src.close()


def _revision(db: Path) -> str | None:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def note_files(notes_dir: Path, exclude: Path | None = None) -> list[tuple[str, Path]]:
    """(relative posix path, absolute path) of every regular, non-hidden file, without following symlinks."""
    root = notes_dir.resolve()
    out = []
    for dirpath, dirnames, filenames in os.walk(root):  # os.walk doesn't descend into symlinked folders by default
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if name.startswith(".") or p.is_symlink() or not p.is_file():
                continue
            if exclude is not None and exclude.resolve() in p.resolve().parents:
                continue
            if not _safe_member(f"notes/{p.relative_to(root).as_posix()}"):  # e.g. a ':' in a name: couldn't restore on Windows
                continue
            out.append((p.relative_to(root).as_posix(), p))
    return out


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")


def create_backup(db_path: Path, out_dir: Path, passphrase: str, *, notes_dir: Path | None = None, keep: int = 14,
                  now: datetime | None = None) -> Path:
    check_passphrase(passphrase)
    if not out_dir.exists():
        out_dir.mkdir(parents=True, mode=0o700)  # private to you (no effect on Windows)
    target = out_dir / f"{db_path.stem}-{_stamp(now)}{SUFFIX}"
    if target.exists():
        raise BackupError(f"{target.name} already exists.")
    partial = target.with_name(target.name + ".partial")
    files: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="keel-backup-") as tmp:
        db_copy = Path(tmp) / "agent.db"
        consistent_copy(db_path, db_copy)
        fd = os.open(partial, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as raw:
                enc = EncryptingWriter(raw, passphrase)
                with tarfile.open(fileobj=enc, mode="w|gz") as tar:
                    def add(arcname: str, path: Path) -> None:
                        files.append({"name": arcname, "size": path.stat().st_size, "sha256": _sha256(path)})
                        info = tarfile.TarInfo(arcname)
                        info.size, info.mtime, info.mode = files[-1]["size"], int(path.stat().st_mtime), 0o600
                        with open(path, "rb") as f:
                            tar.addfile(info, f)

                    add("agent.db", db_copy)
                    if notes_dir is not None and notes_dir.is_dir():
                        for rel, path in note_files(notes_dir, exclude=out_dir):
                            add(f"notes/{rel}", path)
                    manifest = json.dumps({"format": FORMAT, "created": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
                                           "db_revision": _revision(db_copy), "files": files}, indent=1).encode()
                    info = tarfile.TarInfo("manifest.json")
                    info.size, info.mode = len(manifest), 0o600
                    tar.addfile(info, io.BytesIO(manifest))
                enc.close()
            os.replace(partial, target)  # a crash part-way leaves only a .partial, never a file that looks complete
        finally:
            if partial.exists():
                partial.unlink()
    for old in (sorted(out_dir.glob(f"{db_path.stem}-*{SUFFIX}"))[:-keep] if keep > 0 else []):
        old.unlink()
    return target


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or "\\" in name or ":" in name or any(part in ("", ".", "..") or part.startswith(".") for part in p.parts):
        return False
    return name in ("agent.db", "manifest.json") or (p.parts[0] == "notes" and len(p.parts) >= 2)


def _unpack(path: Path, passphrase: str, staging: Path) -> dict:
    """Decrypt and unpack into `staging`, checking every file against the manifest. Returns the manifest."""
    seen: dict[str, dict] = {}
    manifest = None
    try:
        with open(path, "rb") as raw:
            reader = DecryptingReader(raw, passphrase)
            with tarfile.open(fileobj=reader, mode="r|gz") as tar:
                for m in tar:
                    if not m.isreg() or not _safe_member(m.name) or m.name in seen:
                        raise BackupError(f"The backup holds an unexpected entry ({m.name!r}); refusing to restore it.")
                    src = tar.extractfile(m)
                    if m.name == "manifest.json":
                        manifest = json.loads(src.read(1 << 26))
                        seen[m.name] = {}
                        continue
                    dest = staging / m.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    h, size = hashlib.sha256(), 0
                    with open(dest, "xb") as out:
                        for block in iter(lambda src=src: src.read(1 << 20), b""):
                            h.update(block)
                            size += len(block)
                            out.write(block)
                    seen[m.name] = {"name": m.name, "size": size, "sha256": h.hexdigest()}
    except BackupCryptoError as e:
        raise BackupError(str(e)) from None
    except (tarfile.TarError, OSError, EOFError, json.JSONDecodeError) as e:
        raise BackupError(f"The backup is damaged ({type(e).__name__}).") from None
    if not manifest or manifest.get("format") != FORMAT:
        raise BackupError("The backup has no manifest, or one from a newer Keel.")
    listed = {f["name"]: f for f in manifest.get("files", [])}
    got = {k: v for k, v in seen.items() if k != "manifest.json"}
    if listed != got:
        raise BackupError("The backup's contents don't match its manifest.")
    if "agent.db" not in got:
        raise BackupError("The backup has no database in it.")
    con = sqlite3.connect(staging / "agent.db")
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("The database in the backup failed its integrity check.")
    finally:
        con.close()
    return manifest


def _summary(manifest: dict) -> dict:
    notes = [f for f in manifest["files"] if f["name"].startswith("notes/")]
    db = next(f for f in manifest["files"] if f["name"] == "agent.db")
    return {"created": manifest.get("created"), "db_revision": manifest.get("db_revision"), "db_bytes": db["size"],
            "notes": len(notes), "notes_bytes": sum(f["size"] for f in notes)}


def verify_backup(path: Path, passphrase: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="keel-verify-") as tmp:
        return _summary(_unpack(path, passphrase, Path(tmp)))


def restore_backup(path: Path, passphrase: str, db_path: Path, *, notes_dir: Path | None = None,
                   now: datetime | None = None) -> dict:
    """Stop Keel first. The current database is renamed `<name>.before-restore-<stamp>.db`, not deleted. Notes are
    copied back only where no file exists at that path."""
    for side in (db_path.with_name(db_path.name + "-journal"), db_path.with_name(db_path.name + "-wal")):
        if side.exists() and side.stat().st_size > 0:
            raise BackupError(f"{side.name} exists: Keel may still be running or crashed mid-write. Stop Keel (and open it "
                              "once so it can finish) before restoring.")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".keel-restore-", dir=db_path.parent))  # same disk, so the swap is atomic
    try:
        manifest = _unpack(path, passphrase, staging)
        kept = None
        if db_path.exists():
            kept = db_path.with_name(f"{db_path.stem}.before-restore-{_stamp(now)}{db_path.suffix}")
            os.replace(db_path, kept)
        os.replace(staging / "agent.db", db_path)
        restored, skipped, failed = [], [], []
        if notes_dir is not None and (staging / "notes").is_dir():
            root = notes_dir.resolve()
            for src in sorted((staging / "notes").rglob("*")):
                if not src.is_file():
                    continue
                rel = src.relative_to(staging / "notes")
                dest = root / rel
                if root not in dest.resolve().parents:
                    raise BackupError(f"Refusing to write outside the notes folder ({rel.as_posix()}).")
                if dest.exists():
                    skipped.append(rel.as_posix())
                    continue
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    restored.append(rel.as_posix())
                except OSError:  # e.g. a notes folder mounted read-only: the database is already back, report the rest
                    failed.append(rel.as_posix())
        return {**_summary(manifest), "previous_db": kept.name if kept else None,
                "notes_restored": len(restored), "notes_skipped_existing": skipped, "notes_failed": failed}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def list_backups(out_dir: Path) -> list[BackupInfo]:
    if not out_dir.is_dir():
        return []
    out = []
    for p in sorted(out_dir.iterdir(), reverse=True):
        if p.is_file() and (p.suffix == SUFFIX or p.suffix == ".db"):
            st = p.stat()
            out.append(BackupInfo(p.name, st.st_size, datetime.fromtimestamp(st.st_mtime, UTC).isoformat(timespec="seconds"),
                                  p.suffix == SUFFIX))
    return out


def sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///") or database_url == "sqlite:///:memory:":
        raise BackupError(f"Only file-based SQLite databases can be backed up this way (got {database_url.split(':')[0]}).")
    return Path(database_url.removeprefix("sqlite:///"))


def backup_dir(settings) -> Path:
    return Path(settings.backup_dir) if settings.backup_dir else sqlite_path(settings.database_url).parent / "backups"


def backup_now(settings, now: datetime | None = None) -> Path:
    """An encrypted backup with the configured passphrase, folder, notes and retention (dashboard, scheduler, CLI)."""
    if not settings.backup_passphrase:
        raise BackupError("Set BACKUP_PASSPHRASE in .env to turn on encrypted backups.")
    notes = Path(settings.notes_dir) if settings.backup_include_notes and settings.notes_dir else None
    return create_backup(sqlite_path(settings.database_url), backup_dir(settings), settings.backup_passphrase,
                         notes_dir=notes, keep=settings.backup_keep, now=now)
