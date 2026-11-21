"""Use a second model when the first is busy: e.g. Groq's free tier runs out of daily tokens, so Gemini answers until
Groq is available again.

Only a *retryable* failure (rate limit, overload, timeout, unreachable) switches over; a real error such as a bad
key still shows. After a rate limit the main model is skipped for a while (the wait it asked for, capped), so every
message doesn't first spend a failing call. Both models see the same conversation, tools and rules, so the safety
checks don't depend on which one answered.
"""
from __future__ import annotations

import logging
import re
import time

from app.errors import LLMError
from app.llm.base import LLMClient, LLMResponse

logger = logging.getLogger(__name__)
MAX_COOLDOWN = 15 * 60


def _wait_seconds(message: str) -> float:
    """'Please try again in 1m13.872s' -> 73.9; '...in 12.5s' -> 12.5. Default two minutes."""
    m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", message)
    if not m or not any(m.groups()):
        return 120.0
    h, mins, secs = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mins * 60 + secs


class FallbackLLM:
    def __init__(self, primary: LLMClient, fallback: LLMClient, primary_name: str, fallback_name: str, clock=time.monotonic):
        self.primary, self.fallback = primary, fallback
        self.names = (primary_name, fallback_name)
        self._clock = clock
        self._skip_until = 0.0
        self.last_used: str | None = None

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        if self._clock() >= self._skip_until:
            try:
                out = self.primary.complete(system=system, messages=messages, tools=tools)
                self.last_used = self.names[0]
                return out
            except LLMError as exc:
                if not exc.retryable:
                    raise
                wait = min(_wait_seconds(str(exc)), MAX_COOLDOWN)
                self._skip_until = self._clock() + wait
                logger.warning("%s is busy (%s); using %s for about %.0f s", self.names[0], str(exc)[:120], self.names[1], wait)
        try:
            out = self.fallback.complete(system=system, messages=messages, tools=tools)
        except LLMError as exc:
            raise LLMError(f"{self.names[0]} is busy and {self.names[1]} failed too: {exc}", retryable=exc.retryable) from exc
        self.last_used = self.names[1]
        return out
