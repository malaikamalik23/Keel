"""Groq adapter for the provider-neutral LLMClient protocol.

Groq's API is OpenAI-compatible: messages are role-based strings, tool calls live in a
`tool_calls` list on the assistant message, and a tool's answer is its own `role: "tool"`
message. The rest of the app (agent loop, persistence, prompts) only knows the Anthropic-style
content-block format defined in app.llm.base, so this file is where the two shapes are translated,
in both directions, for a single request.
"""
from __future__ import annotations

import json

import groq

from app.errors import LLMError
from app.llm.base import LLMResponse

_FINISH_REASON_MAP = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}


def _tools_to_groq(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools
    ]


def _blocks_to_text(blocks: list[dict]) -> str:
    return "\n".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text"))


def _messages_to_groq(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        content = msg["content"]
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]

        if msg["role"] == "assistant":
            tool_calls = [b for b in blocks if b["type"] == "tool_use"]
            entry: dict = {"role": "assistant", "content": _blocks_to_text(blocks) or None}
            if tool_calls:
                entry["tool_calls"] = [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])}}
                    for tc in tool_calls
                ]
            out.append(entry)
            continue

        tool_results = [b for b in blocks if b["type"] == "tool_result"]
        if tool_results:
            # Anthropic bundles tool results into one "user" message; Groq/OpenAI wants one
            # standalone "tool" message per result, each tagged with the call it answers.
            for tr in tool_results:
                out.append({"role": "tool", "tool_call_id": tr["tool_use_id"], "content": tr["content"]})
        else:
            out.append({"role": "user", "content": _blocks_to_text(blocks)})
    return out


def _response_from_groq(message, finish_reason: str) -> LLMResponse:
    blocks: list[dict] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for tc in message.tool_calls or []:
        try:
            tool_input = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            tool_input = {}
        blocks.append({"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": tool_input})
    return LLMResponse(content=blocks, stop_reason=_FINISH_REASON_MAP.get(finish_reason, finish_reason))


_RETRY_NOTE = (
    "[Automatic note from Keel, not written by the user] Your last tool call was rejected before it ran: {reason} "
    "Call the tool again with arguments that match its schema, or answer in text if you cannot."
)


def _rejected_tool_call(exc: Exception) -> str | None:
    """The validator's message if Groq refused the request because a generated tool call did not match its schema."""
    body = getattr(exc, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    if getattr(exc, "status_code", None) != 400 or not isinstance(err, dict) or err.get("code") != "tool_use_failed":
        return None
    return str(err.get("message") or "the arguments did not match the tool's schema.")[:300]


def _busy(exc: Exception) -> bool:
    """Rate limit (incl. the free tier's daily tokens), overload, timeout or no connection: worth asking another model."""
    status = getattr(exc, "status_code", None)
    return isinstance(exc, (groq.APIConnectionError, groq.APITimeoutError)) or status == 429 or (status is not None and status >= 500)


class GroqClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1500, temperature: float | None = None):
        self._client = groq.Groq(api_key=api_key, max_retries=4)  # honours Retry-After on 429s (free tier is token-per-minute limited)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs: dict = {
            "model": self._model,
            "max_completion_tokens": self._max_tokens,
            "messages": _messages_to_groq(system, messages),
        }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if tools:
            kwargs["tools"] = _tools_to_groq(tools)
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except groq.APIError as exc:
            reason = _rejected_tool_call(exc)
            if reason is None:
                raise LLMError(f"LLM request failed: {exc}", retryable=_busy(exc)) from exc
            # Groq validates tool arguments against the schema server-side and fails the whole request when the model
            # gets one wrong. Give the model one chance to correct itself instead of ending the user's turn with an error.
            retry = {**kwargs, "messages": [*kwargs["messages"], {"role": "user", "content": _RETRY_NOTE.format(reason=reason)}]}
            try:
                resp = self._client.chat.completions.create(**retry)
            except groq.APIError as exc2:
                raise LLMError(f"LLM request failed: {exc2}", retryable=_busy(exc2)) from exc2

        choice = resp.choices[0]
        out = _response_from_groq(choice.message, choice.finish_reason)
        usage = getattr(resp, "usage", None)
        out.input_tokens, out.output_tokens = getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
        return out
