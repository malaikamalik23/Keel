from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    # "auto" picks Anthropic if ANTHROPIC_API_KEY is set, else Groq if GROQ_API_KEY is set.
    llm_provider: Literal["auto", "anthropic", "groq"] = "auto"
    agent_model: str = "claude-sonnet-5"
    agent_timezone: str = "UTC"
    database_url: str = "sqlite:///./data/agent.db"

    max_agent_steps: int = 8  # max LLM calls per user message (guards against loops)
    history_message_limit: int = 40  # messages replayed to the LLM each turn
    max_output_tokens: int = 1500
    confirm_writes: bool = False  # require confirmation for WRITE tools as well

    # Messages older than the replayed window are folded into Conversation.summary instead of being
    # silently dropped (see app/agent/summarize.py). None disables summarization (old behavior: truncate).
    summarize_dropped_history: bool = True

    # Pending high-impact actions older than this expire automatically (status -> "expired") instead of
    # staying approvable forever. None / 0 disables expiry.
    pending_action_ttl_hours: float | None = 24.0

    # If set, every /api/* request (except /health) must send this value as the X-API-Key header.
    # Unset by default: fine for localhost-only use, but set it before ever exposing the API.
    api_key: str | None = None

    # lean: each request carries only the core tools + the groups the message needs (about 3k tokens of overhead instead of
    # 7k; required on Groq's free tier). full: every tool in every request (best routing, most tokens).
    tool_profile: Literal["lean", "full"] = "lean"

    # ---- Phase 4: scheduler, check-ins, notifications ----
    scheduler_enabled: bool = False  # opt-in: starts the in-process scheduler with the API
    scheduler_tick_seconds: int = 60
    morning_checkin_time: str = "08:00"  # local HH:MM
    evening_checkin_time: str = "20:00"
    weekly_checkin_weekday: int = 6  # 0=Monday .. 6=Sunday; sent at the evening time
    checkin_grace_hours: float = 3.0  # a check-in more than this late is skipped, not sent stale
    notification_max_per_day: int = 5  # budget for everything except reminders you explicitly asked for
    checkin_reserve: int = 2  # budget slots held back from nudges so morning/evening check-ins always fit
    notification_min_priority: Literal["low", "normal", "high"] = "normal"
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None  # the ONLY chat the bot talks to / listens to
    telegram_inbound: bool = True  # also let you chat with the agent through the bot
    calendar_ics_url: str | None = None  # secret iCal address of your calendar (read-only)

    # ---- Phase 5: research tools ----
    web_fetch_enabled: bool = True
    web_allowed_domains: str = ""  # comma-separated; if set, web_fetch only reaches these (and subdomains)
    web_blocked_domains: str = ""
    brave_api_key: str | None = None  # enables web_search
    notes_dir: str | None = None  # folder of notes/papers to index for notes_search
    github_enabled: bool = True
    github_token: str | None = None  # optional: private repos and higher rate limits (read-only scope is enough)
    github_user: str | None = None  # default owner for github_list_repos

    rate_limit_per_minute: int = 30  # per client, for endpoints that call the LLM (/api/chat, /api/scheduler/tick). 0 = off

    # Built dashboard (frontend/dist). Served at / when the directory exists; blank/None disables.
    frontend_dist: str | None = "frontend/dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def csv_list(value: str) -> list[str]:
    return [v.strip().lower() for v in value.split(",") if v.strip()]
