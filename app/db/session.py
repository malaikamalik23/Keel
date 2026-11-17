from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # one shared in-memory DB (tests)
        else:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_fks(dbapi_conn, _):  # SQLite ignores foreign keys unless asked
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def init_db(engine: Engine) -> None:
    """Bring the schema up to the latest Alembic revision, creating it from scratch if needed.

    Runs on the engine's own connection (not a fresh one opened from the URL string), so this also
    works for `sqlite://` in-memory test databases, whose single connection is held open by StaticPool.
    """
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # SQLite can't ALTER most things, so a migration rebuilds the table: copy, DROP the old one, rename. With
            # foreign keys on, that DROP fails as soon as other rows point at the table (messages -> conversations).
            # SQLite's documented procedure is to switch them off for the rebuild and check them all afterwards.
            # The pragma only takes effect outside a transaction, hence before anything else on this connection.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            _drop_leftover_rebuild_tables(connection)
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        if sqlite:
            connection.commit()
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            if broken:
                raise RuntimeError(f"Database migration left {len(broken)} broken reference(s), e.g. {broken[0]}. "
                                   "Restore a backup made with `python -m scripts.backup` and report this.")


def _drop_leftover_rebuild_tables(connection) -> None:
    """Remove `_alembic_tmp_<table>` copies left by a rebuild that failed part-way, so the next attempt can run.

    Only when the original table still exists: then the copy is redundant (the rebuild failed before replacing
    anything). If the original were gone, the copy would be the only one holding the data, so stop instead.
    """
    names = {r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    for tmp in sorted(n for n in names if n.startswith("_alembic_tmp_")):
        original = tmp.removeprefix("_alembic_tmp_")
        if original not in names:
            raise RuntimeError(f"Found {tmp} but no {original} table: an earlier upgrade stopped half-way. "
                               "Nothing was changed; restore a backup made with `python -m scripts.backup`.")
        connection.exec_driver_sql(f'DROP TABLE "{tmp}"')
    connection.commit()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
