from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy.orm import Session


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    HIGH_IMPACT = "high_impact"


class ToolError(Exception):
    """An expected, user-explainable failure (e.g. 'no task with id 7'). Message goes back to the model."""


@dataclass
class ToolContext:
    db: Session
    tz: ZoneInfo
    conversation_id: int | None = None
    tainted: bool = False  # set once untrusted external content has entered this turn
    seen_urls: set[str] = field(default_factory=set)  # URLs already present in the context (see taint_gate)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()


def _strip_titles(node: Any) -> Any:
    """Remove pydantic's noisy string 'title' annotations from a JSON schema (keeps properties named 'title')."""
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    permission: Permission
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict]
    # Human-readable description of what a queued call would do (shown when asking for confirmation).
    summarize: Callable[[ToolContext, Any], str] | None = None
    # True for tools that fetch external content (web, files, email...). Output is fenced and taints the turn.
    returns_untrusted: bool = False
    # Exfiltration guard for tools that reach the outside world. While the session is tainted, this is called with
    # the validated params; returning False forces user confirmation. (Used by web_fetch: once untrusted text is in
    # context, only URLs that already appeared verbatim may be fetched freely, never ones the model assembled.)
    taint_gate: Callable[[ToolContext, Any], bool] | None = None

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": _strip_titles(self.input_model.model_json_schema()),
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]
