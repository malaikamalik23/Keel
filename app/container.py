"""Wires the app together. Used by both the API and the CLI so they behave identically."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent.loop import Agent
from app.config import Settings, csv_list, get_settings
from app.db.session import init_db, make_engine, make_session_factory
from app.integrations.calendar import fetch_ics
from app.integrations.github import GitHubClient
from app.integrations.notifier import Notifier, build_notifier
from app.integrations.safe_http import SafeFetcher
from app.integrations.websearch import BraveSearch
from app.llm.base import LLMClient
from app.llm.factory import build_llm
from app.scheduler.runner import Scheduler
from app.security.permissions import PermissionPolicy
from app.services.notifications import NotificationService
from app.tools import build_default_registry
from app.tools.base import ToolRegistry
from app.tools.executor import ToolExecutor


@dataclass
class Container:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    registry: ToolRegistry
    executor: ToolExecutor
    agent: Agent
    notifier: Notifier
    notifications: NotificationService
    scheduler: Scheduler

    def index_notes(self) -> dict:
        """Incrementally (re)index NOTES_DIR. Cheap when nothing changed."""
        from pathlib import Path

        from app.services.documents import NotesUnavailable, index_directory

        if not self.settings.notes_dir:
            raise NotesUnavailable("NOTES_DIR is not set.")
        with self.session_factory() as db:
            return index_directory(db, Path(self.settings.notes_dir).expanduser()).as_dict()


def build_container(
    settings: Settings | None = None, llm: LLMClient | None = None, notifier: Notifier | None = None
) -> Container:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    calendar_url = settings.calendar_ics_url
    registry = build_default_registry(
        calendar_source=(lambda: fetch_ics(calendar_url)) if calendar_url else None,
        fetcher=SafeFetcher(allowed_domains=csv_list(settings.web_allowed_domains), blocked_domains=csv_list(settings.web_blocked_domains))
        if settings.web_fetch_enabled else None,
        search=BraveSearch(settings.brave_api_key) if settings.brave_api_key else None,
        github=GitHubClient(settings.github_token) if settings.github_enabled else None,
        github_user=settings.github_user,
        notes_enabled=bool(settings.notes_dir) and settings.database_url.startswith("sqlite"),
    )
    executor = ToolExecutor(
        registry,
        PermissionPolicy(confirm_writes=settings.confirm_writes),
        pending_action_ttl_hours=settings.pending_action_ttl_hours,
    )
    agent = Agent(llm or build_llm(settings), executor, settings)
    session_factory = make_session_factory(engine)
    notifier = notifier or build_notifier(settings)
    notifications = NotificationService(notifier, settings)
    scheduler = Scheduler(
        settings=settings, session_factory=session_factory, agent=agent, notifications=notifications, notifier=notifier
    )
    return Container(settings, engine, session_factory, registry, executor, agent, notifier, notifications, scheduler)
