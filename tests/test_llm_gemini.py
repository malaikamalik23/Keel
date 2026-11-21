"""Gemini (Google AI Studio key) and the fallback that uses it while Groq is busy, against fakes (no network)."""
from __future__ import annotations

import json

import httpx
import pytest

from app.errors import LLMError
from app.llm.base import LLMResponse
from app.llm.factory import build_llm, model_name
from app.llm.fallback import FallbackLLM, _wait_seconds
from app.llm.gemini_client import GeminiClient, list_models
from app.services import connections
from tests.conftest import make_settings
from tests.fakes import text

TOOLS = [{"name": "create_task", "description": "Create a task.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}}}]
KEY = "AIza-SECRET-gemini-key"


def _gemini(handler) -> tuple[GeminiClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(req):
        seen.append(req)
        return handler(req)

    return GeminiClient(KEY, "gemini-3.6-flash", 900, 0.2, client=httpx.Client(transport=httpx.MockTransport(record))), seen


def test_tool_call_round_trip(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "create_task", "arguments": '{"title": "Write intro"}'}},
            {"type": "function", "function": {"name": "create_task", "arguments": "not json"}}]}}],
            "usage": {"prompt_tokens": 3000, "completion_tokens": 25}})

    llm, seen = _gemini(handler)
    history = [{"role": "user", "content": "add a task"},
               {"role": "assistant", "content": [{"type": "tool_use", "id": "g1", "name": "create_task", "input": {"title": "x"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "g1", "content": "{\"ok\": true}"}]}]
    out = llm.complete(system="You are Keel.", messages=history, tools=TOOLS)
    assert out.stop_reason == "tool_use" and out.input_tokens == 3000
    assert [c["input"] for c in out.tool_uses] == [{"title": "Write intro"}, {}] and out.tool_uses[1]["id"].startswith("call_")
    req = seen[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == "gemini-3.6-flash" and body["max_tokens"] == 900 and body["tools"][0]["function"]["name"] == "create_task"
    assert body["messages"][2]["content"] == "" and body["messages"][3] == {"role": "tool", "tool_call_id": "g1", "content": "{\"ok\": true}"}


@pytest.mark.parametrize(("status", "body", "message", "retryable"), [
    (429, {"error": {"message": "Resource exhausted"}}, "free-tier limit", True),
    (503, {"error": {"message": "overloaded"}}, "HTTP 503", True),
    (400, [{"error": {"message": f"API key {KEY} not valid"}}], "HTTP 400", False),
    (403, {"error": {"message": "denied"}}, "rejected the API key", False),
    (404, {"error": {"message": "not found"}}, "no model called gemini-3.6-flash", False),
])
def test_errors_are_clear_never_leak_the_key_and_say_if_busy(status, body, message, retryable):
    llm, _ = _gemini(lambda r: httpx.Response(status, json=body))
    with pytest.raises(LLMError, match=message) as info:
        llm.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
    assert info.value.retryable is retryable and KEY not in str(info.value)


def test_listing_models_sends_no_prompt():
    seen = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200, json={"data": [{"id": "models/gemini-3.6-flash"}, {"id": "gemini-3.8-flash"}]})))
    assert list_models(KEY, http) == ["gemini-3.6-flash", "gemini-3.8-flash"]
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/v1beta/openai/models")]


class Fake:
    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def complete(self, **kw) -> LLMResponse:
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_fallback_answers_while_the_main_model_is_busy_then_hands_back():
    now = [0.0]
    busy = LLMError("Rate limit reached ... tokens per day (TPD) ... Please try again in 1m13.872s.", retryable=True)
    groq, gemini = Fake(busy, text("groq again")), Fake(text("gemini 1"), text("gemini 2"))
    llm = FallbackLLM(groq, gemini, "groq", "gemini", clock=lambda: now[0])
    args = dict(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
    assert llm.complete(**args).text == "gemini 1" and llm.last_used == "gemini"
    now[0] = 30
    assert llm.complete(**args).text == "gemini 2" and groq.calls == 1  # still waiting: Groq isn't asked again
    now[0] = 75
    assert llm.complete(**args).text == "groq again" and llm.last_used == "groq"


def test_a_real_error_is_not_hidden_by_the_fallback():
    gemini = Fake(text("unused"))
    llm = FallbackLLM(Fake(LLMError("Invalid API key")), gemini, "groq", "gemini")
    with pytest.raises(LLMError, match="Invalid API key"):
        llm.complete(system="s", messages=[], tools=[])
    assert gemini.calls == 0
    both = FallbackLLM(Fake(LLMError("busy", retryable=True)), Fake(LLMError("also busy", retryable=True)), "groq", "gemini")
    with pytest.raises(LLMError, match="groq is busy and gemini failed too: also busy"):
        both.complete(system="s", messages=[], tools=[])


def test_wait_parsing():
    assert _wait_seconds("try again in 1m13.872s") == pytest.approx(73.872)
    assert _wait_seconds("Please try again in 12.5s.") == 12.5
    assert _wait_seconds("try again in 2h") == 7200
    assert _wait_seconds("slow down") == 120


def test_factory_wiring_and_labels():
    s = make_settings(groq_api_key="gsk_x", gemini_api_key=KEY, llm_fallback="gemini")
    llm = build_llm(s)
    assert isinstance(llm, FallbackLLM) and isinstance(llm.fallback, GeminiClient) and llm.names == ("groq", "gemini")
    assert model_name(s) == s.agent_model
    only = make_settings(gemini_api_key=KEY)
    assert isinstance(build_llm(only), GeminiClient) and model_name(only) == "gemini/gemini-3.6-flash"  # auto picks it when it's the only key
    assert not isinstance(build_llm(make_settings(groq_api_key="gsk_x", llm_fallback="groq")), FallbackLLM)  # same provider: no fallback
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        build_llm(make_settings(groq_api_key="gsk_x", llm_fallback="gemini"))


def test_services_page_states_googles_data_use_and_checks_for_free(monkeypatch):
    s = make_settings(groq_api_key="gsk_x", gemini_api_key=KEY, llm_fallback="gemini")
    row = next(r for r in connections.describe(s) if r["id"] == "model")
    assert row["details"][1] == "When busy, falls back to Google Gemini · gemini-3.6-flash"
    assert "used to improve its products" in row["details"][2] and row["testable"] and KEY not in json.dumps(row)
    monkeypatch.setattr("app.llm.gemini_client.list_models", lambda key: ["gemini-3.6-flash", "gemini-3.8-flash"])
    assert connections.run_check("model", s)["ok"] is True
    monkeypatch.setattr("app.llm.gemini_client.list_models", lambda key: ["gemini-3.8-flash"])
    out = connections.run_check("model", s)
    assert out["ok"] is False and "gemini-3.8-flash" in out["detail"]
    assert "used to improve" not in json.dumps(connections.describe(make_settings(groq_api_key="gsk_x")))


def test_thought_signatures_go_back_to_gemini_and_never_to_anthropic():
    replies = iter([
        {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "create_task", "arguments": "{}"}, "extra_content": {"google": {"thought_signature": "SIG"}}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}]},
    ])
    llm, seen = _gemini(lambda r: httpx.Response(200, json=next(replies)))
    first = llm.complete(system="s", messages=[{"role": "user", "content": "add"}], tools=TOOLS)
    call = first.tool_uses[0]
    assert call["meta"] == {"gemini_extra": {"google": {"thought_signature": "SIG"}}}
    history = [{"role": "user", "content": "add"},
               {"role": "assistant", "content": [call, {"type": "tool_use", "id": "groq_9", "name": "list_tasks", "input": {}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"},
                                            {"type": "tool_result", "tool_use_id": "groq_9", "content": "{}"}]}]
    llm.complete(system="s", messages=history, tools=TOOLS)
    sent = json.loads(seen[1].content)["messages"][2]["tool_calls"]
    assert sent[0]["extra_content"] == {"google": {"thought_signature": "SIG"}}  # Gemini's own call: its signature
    assert sent[1]["extra_content"] == {"google": {"thought_signature": "skip_thought_signature_validator"}}  # made by another model

    from app.llm.anthropic_client import _without_meta

    clean = _without_meta(history)
    assert "meta" not in clean[1]["content"][0] and "meta" in history[1]["content"][0]  # stripped for Anthropic, history untouched
