from pydantic import BaseModel

from app.tools.base import Permission, Tool, ToolContext, ToolRegistry


class NoInput(BaseModel):
    pass


def get_current_datetime(ctx: ToolContext, _: NoInput) -> dict:
    now = ctx.now()
    return {
        "iso": now.isoformat(timespec="minutes"),
        "date": now.date().isoformat(),
        "weekday": now.strftime("%A"),
        "timezone": str(ctx.tz),
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="get_current_datetime",
            description="Get the user's current local date, time and weekday. Use before reasoning about deadlines.",
            permission=Permission.READ,
            input_model=NoInput,
            handler=get_current_datetime,
        )
    )
