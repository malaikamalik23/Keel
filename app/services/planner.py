"""Deterministic daily planner. Code decides *what fits*; the LLM only decides how to talk about it.

Guarantees (all tested):
- the plan never exceeds the usable time (capacity minus a safety buffer),
- at most MAX_MAJOR major priorities per day,
- anything left out is returned with an explicit reason, never silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.db.models import Task

DEFAULT_ESTIMATE_MINUTES = 30
MAX_MAJOR = 3
MAJOR_MINUTES = 90  # a task this long (or any high-priority one) counts as a "major priority"
BUFFER = 0.15  # keep 15% of the day free: plans that use 100% of the time never survive contact with reality

_PRIORITY_POINTS = {"high": 30, "medium": 15, "low": 0}


@dataclass
class Candidate:
    task: Task
    minutes: int
    is_major: bool


@dataclass
class PlanResult:
    selected: list[Candidate] = field(default_factory=list)
    deferred: list[dict] = field(default_factory=list)
    usable_minutes: int = 0
    used_minutes: int = 0


def usable_capacity(capacity_minutes: int) -> int:
    return int(capacity_minutes * (1 - BUFFER))


def task_minutes(t: Task) -> int:
    return t.estimated_minutes or DEFAULT_ESTIMATE_MINUTES


def is_major(t: Task) -> bool:
    return t.priority == "high" or task_minutes(t) >= MAJOR_MINUTES


def priority_score(t: Task, today: date) -> int:
    """Higher = do sooner. Urgency dominates, then priority; things that keep slipping get a nudge upward."""
    if t.due_date is None:
        urgency = 0
    else:
        days = (t.due_date - today).days
        urgency = 100 if days < 0 else 90 if days == 0 else 70 if days <= 3 else 40 if days <= 7 else 10
    return urgency + _PRIORITY_POINTS.get(t.priority, 15) + 5 * min(t.postponement_count, 4)


def rank_tasks(tasks: list[Task], today: date) -> list[Task]:
    return sorted(tasks, key=lambda t: (-priority_score(t, today), t.id))


def build_plan(
    ordered_tasks: list[Task], capacity_minutes: int, *, already_used_minutes: int = 0, already_major: int = 0
) -> PlanResult:
    """Greedy fill in the given order. Smaller tasks later in the list may still fit after a big one is skipped."""
    res = PlanResult(usable_minutes=usable_capacity(capacity_minutes))
    used, majors = already_used_minutes, already_major
    for t in ordered_tasks:
        mins, major = task_minutes(t), is_major(t)
        if mins > res.usable_minutes:
            res.deferred.append({"task_id": t.id, "title": t.title, "reason": f"needs {mins} min, more than the whole usable day ({res.usable_minutes} min) -- split it into smaller tasks"})
        elif major and majors >= MAX_MAJOR:
            res.deferred.append({"task_id": t.id, "title": t.title, "reason": f"already {MAX_MAJOR} major priorities today"})
        elif used + mins > res.usable_minutes:
            res.deferred.append({"task_id": t.id, "title": t.title, "reason": f"needs {mins} min but only {max(res.usable_minutes - used, 0)} min left"})
        else:
            res.selected.append(Candidate(t, mins, major))
            used += mins
            majors += int(major)
    res.used_minutes = used
    return res
