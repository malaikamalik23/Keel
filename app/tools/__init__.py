from collections.abc import Callable

from app.tools import calendar, commitments, goals, groups, memory, notes, planning, projects, reminders, system, tasks
from app.tools import github as github_tools
from app.tools import web as web_tools
from app.tools.base import ToolRegistry


def build_default_registry(
    calendar_source: Callable[[], str] | None = None, *, fetcher=None, search=None, github=None, github_user: str | None = None,
    notes_enabled: bool = False,
) -> ToolRegistry:
    """Tools that need configuration (calendar, web, notes, GitHub) are only advertised to the model when configured."""
    registry = ToolRegistry()
    system.register(registry)
    tasks.register(registry)
    goals.register(registry)
    projects.register(registry)
    commitments.register(registry)
    memory.register(registry)
    planning.register(registry)
    reminders.register(registry)
    if calendar_source is not None:  # only advertised when a calendar is configured
        calendar.register(registry, calendar_source)
    web_tools.register(registry, fetcher=fetcher, search=search)
    if github is not None:
        github_tools.register(registry, github, github_user)
    if notes_enabled:
        notes.register(registry)
    groups.register_load_tools(registry)
    return registry
