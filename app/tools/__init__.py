from collections.abc import Callable

from app.services.notifications import parse_hhmm
from app.tools import (
    applications,
    calendar,
    commitments,
    dates,
    goals,
    groups,
    memory,
    notes,
    planning,
    projects,
    reminders,
    system,
    tasks,
    workflows,
)
from app.tools import browser as browser_tools
from app.tools import gcal as gcal_tools
from app.tools import github as github_tools
from app.tools import gmail as gmail_tools
from app.tools import web as web_tools
from app.tools.base import ToolRegistry


def build_default_registry(
    calendar_source: Callable[[], str] | None = None, *, fetcher=None, search=None, github=None, github_user: str | None = None,
    notes_enabled: bool = False, workday: tuple[str, str] = ("09:00", "21:00"), workflow_engine: dict | None = None,
    gmail=None, gmail_send: bool = True, gmail_max_sends: int = 20, notes_dir: str | None = None, google_calendar=None,
    browser=None,
) -> ToolRegistry:
    """Tools that need configuration (calendar, web, notes, GitHub) are only advertised to the model when configured."""
    registry = ToolRegistry()
    system.register(registry)
    tasks.register(registry, calendar_source)
    goals.register(registry)
    projects.register(registry)
    commitments.register(registry)
    memory.register(registry)
    planning.register(registry, calendar_source, (parse_hhmm(workday[0]), parse_hhmm(workday[1])))
    reminders.register(registry)
    dates.register(registry)
    applications.register(registry)
    if calendar_source is not None:  # only advertised when a calendar is configured
        calendar.register(registry, calendar_source)
    web_tools.register(registry, fetcher=fetcher, search=search)
    if github is not None:
        github_tools.register(registry, github, github_user)
    if notes_enabled:
        notes.register(registry)
    if gmail is not None:
        gmail_tools.register(registry, gmail, send_enabled=gmail_send, notes_dir=notes_dir if notes_enabled else None,
                             max_sends_per_day=gmail_max_sends)
    if browser is not None:
        browser_tools.register(registry, browser, notes_dir=notes_dir if notes_enabled else None)
    if google_calendar is not None:
        gcal_tools.register(registry, google_calendar)
    workflows.register(registry, workflow_engine if workflow_engine is not None else {})  # after the tools its steps may use
    groups.register_load_tools(registry)
    return registry
