from __future__ import annotations

import anthropic

from app.errors import LLMError
from app.llm.base import LLMResponse


def _without_meta(messages: list[dict]) -> list[dict]:
    """Drop other providers' bookkeeping (e.g. Gemini's tool-call signatures) that Anthropic's API would reject."""
    out = []
    for m in messages:
        if isinstance(m.get("content"), list) and any(isinstance(b, dict) and "meta" in b for b in m["content"]):
            m = {**m, "content": [{k: v for k, v in b.items() if k != "meta"} if isinstance(b, dict) else b for b in m["content"]]}
        out.append(m)
    return out


class AnthropicClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1500, temperature: float | None = None):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": _without_meta(messages),
        }
        if tools:
            kwargs["tools"] = tools
        # No temperature: current Claude models reject sampling parameters (a 400), and the API is removing them everywhere.
        try:
            resp = self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            status = getattr(exc, "status_code", None)
            busy = isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)) or status in (429, 529) or (status or 0) >= 500
            raise LLMError(f"LLM request failed: {exc}", retryable=busy) from exc

        blocks: list[dict] = []
        for b in resp.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        usage = getattr(resp, "usage", None)
        return LLMResponse(content=blocks, stop_reason=resp.stop_reason or "end_turn",
                           input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None))
