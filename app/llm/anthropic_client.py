from __future__ import annotations

import anthropic

from app.errors import LLMError
from app.llm.base import LLMResponse


class AnthropicClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1500):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        try:
            resp = self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        blocks: list[dict] = []
        for b in resp.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        usage = getattr(resp, "usage", None)
        return LLMResponse(content=blocks, stop_reason=resp.stop_reason or "end_turn",
                           input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None))
