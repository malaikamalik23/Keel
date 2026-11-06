from typing import Callable

from app.tools import calendar, commitments, goals, memory, planning, projects, reminders, system, tasks
from app.tools.base import ToolRegistry


def build_default_registry(calendar_source: Callable[[], str] | None = None) -> ToolRegistry:
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
    return registry
