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


@lru_cache
def get_settings() -> Settings:
    return Settings()
