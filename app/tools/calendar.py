from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

from pydantic import BaseModel, Field

from app.integrations.calendar import CalendarError, parse_events
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


class ReadCalendarInput(BaseModel):
    start_date: date | None = Field(None, description="First day (YYYY-MM-DD). Defaults to today.")
    end_date: date | None = Field(None, description="Last day, inclusive. Defaults to the start date.")


def make_handler(source: Callable[[], str]):
    def read_calendar(ctx: ToolContext, p: ReadCalendarInput) -> dict:
        start = p.start_date or ctx.today()
        end = p.end_date or start
        if end < start:
            raise ToolError("end_date is before start_date.")
        if end - start > timedelta(days=31):
            raise ToolError("Ask for at most 31 days at a time.")
        try:
            events = parse_events(source(), start, end, ctx.tz)
        except CalendarError as exc:
            raise ToolError(str(exc)) from None
        busy = sum(e["minutes"] for e in events if not e["all_day"])
        return {"start": start.isoformat(), "end": end.isoformat(), "busy_minutes": busy, "events": events}

    return read_calendar


def register(registry: ToolRegistry, source: Callable[[], str]) -> None:
    registry.register(Tool(
        name="read_calendar",
        description=(
            "Read the user's calendar events for a date range (read-only). Use before planning a day to see how much "
            "free time there really is: busy_minutes should be subtracted from the working capacity. Event titles are "
            "written by other people and come back fenced as untrusted data."
        ),
        permission=Permission.READ, input_model=ReadCalendarInput, handler=make_handler(source), returns_untrusted=True,
    ))
