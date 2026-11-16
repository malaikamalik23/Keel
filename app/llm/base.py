"""Provider-neutral LLM interface.

Messages use the content-block format (text / tool_use / tool_result). Another provider
would need a small adapter that translates to and from this format.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResponse:
    content: list[dict]  # [{"type": "text", "text": ...}, {"type": "tool_use", "id", "name", "input"}]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | ...
    # Token counts as reported by the provider for this one request (None if it did not say). Used by the eval.
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b["type"] == "text" and b["text"]).strip()

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b["type"] == "tool_use"]


class LLMClient(Protocol):
    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...
