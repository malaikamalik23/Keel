from app.tools import commitments, goals, memory, projects, system, tasks
from app.tools.base import ToolRegistry


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    system.register(registry)
    tasks.register(registry)
    goals.register(registry)
    projects.register(registry)
    commitments.register(registry)
    memory.register(registry)
    return registry
