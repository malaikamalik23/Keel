"""Google Gemini (a Google AI Studio API key) through Gemini's OpenAI-compatible endpoint, over plain HTTP.

Messages are translated with the same helpers as Groq (both speak the OpenAI chat format). Gemini's compatibility
layer is still in beta at Google, so this adapter is defensive: tool calls without an id get one, and arguments that
aren't valid JSON become {} (the tool's own validation then rejects the call with a clear error).

Data use: on the free tier, Google says the content you send is used to improve its products. Everything Keel sends
the model (your messages, tasks, memories, and anything it just read, such as emails) goes there. The paid tier
doesn't do this. The Services tab and the docs say so; by default Gemini is only a fallback for when Groq is busy.
"""
from __future__ import annotations

import json
import uuid

import httpx

from app.errors import LLMError
from app.llm.base import LLMResponse
from app.llm.groq_client import _messages_to_groq, _tools_to_groq

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# Google's documented placeholder for tool calls that Gemini didn't make itself (e.g. Groq made them before a fallback).
FOREIGN_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}
_FINISH = {"tool_calls": "tool_use", "length": "max_tokens", "stop": "end_turn"}


def _response(data: dict) -> LLMResponse:
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        raise LLMError("Gemini returned no answer.") from None
    msg = choice.get("message") or {}
    blocks: list[dict] = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {}
        block = {"type": "tool_use", "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}", "name": str(fn.get("name", "")),
                 "input": args if isinstance(args, dict) else {}}
        if isinstance(tc.get("extra_content"), dict):
            # Gemini's thinking models sign each tool call and refuse the next request unless the signature comes back.
            # Kept on the saved block under "meta" (other providers' adapters ignore it; Anthropic's strips it).
            block["meta"] = {"gemini_extra": tc["extra_content"]}
        blocks.append(block)
    reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else _FINISH.get(choice.get("finish_reason") or "stop", "end_turn")
    usage = data.get("usage") or {}
    return LLMResponse(content=blocks, stop_reason=reason, input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, max_tokens: int = 1500, temperature: float | None = None,
                 client: httpx.Client | None = None, timeout: float = 120):
        self._key = api_key
        self.model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._http = client or httpx.Client(timeout=timeout)

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        msgs = _messages_to_groq(system, messages)
        signed = {b["id"]: b["meta"]["gemini_extra"] for m in messages if m["role"] == "assistant" and isinstance(m["content"], list)
                  for b in m["content"] if b.get("type") == "tool_use" and isinstance(b.get("meta"), dict) and "gemini_extra" in b["meta"]}
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            if m.get("content") is None:  # Gemini's compatibility layer wants a string, not null, next to tool calls
                m["content"] = ""
            for tc in m.get("tool_calls") or []:
                tc["extra_content"] = signed.get(tc["id"], FOREIGN_SIGNATURE)
        body: dict = {"model": self.model, "messages": msgs, "max_tokens": self._max_tokens}
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if tools:
            body["tools"] = _tools_to_groq(tools)
        try:
            resp = self._http.post(f"{BASE_URL}/chat/completions", json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException:
            raise LLMError("Gemini took too long to answer.", retryable=True) from None
        except httpx.HTTPError as exc:
            raise LLMError(f"Couldn't reach Gemini ({type(exc).__name__}).", retryable=True) from None
        if resp.status_code != 200:
            raise LLMError(self._explain(resp), retryable=resp.status_code == 429 or resp.status_code >= 500)
        try:
            return _response(resp.json())
        except ValueError:
            raise LLMError("Gemini returned something that isn't JSON.") from None

    def _explain(self, resp: httpx.Response) -> str:
        try:
            body = resp.json()
            err = body[0] if isinstance(body, list) else body
            detail = str((err.get("error") or {}).get("message", ""))[:300]
        except (ValueError, AttributeError, IndexError):
            detail = ""
        detail = detail.replace(self._key, "***") if self._key else detail
        if resp.status_code in (401, 403):
            return "Gemini rejected the API key (check GEMINI_API_KEY in .env)."
        if resp.status_code == 404:
            return f"Gemini has no model called {self.model}. Set GEMINI_MODEL to one listed in Google AI Studio."
        if resp.status_code == 429:
            return "Gemini's free-tier limit was reached; try again later."
        return f"Gemini error (HTTP {resp.status_code}): {detail or 'no details'}"


def list_models(api_key: str, client: httpx.Client | None = None) -> list[str]:
    """Model ids this key can use. Listing sends no prompt and costs nothing."""
    try:
        resp = (client or httpx.Client(timeout=15)).get(f"{BASE_URL}/models", headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise LLMError(f"Couldn't reach Gemini ({type(exc).__name__}).") from None
    if resp.status_code in (401, 403):
        raise LLMError("Gemini rejected the API key (check GEMINI_API_KEY in .env).")
    if resp.status_code != 200:
        raise LLMError(f"Gemini error (HTTP {resp.status_code}).")
    return [str(m.get("id", "")).removeprefix("models/") for m in resp.json().get("data", [])]
