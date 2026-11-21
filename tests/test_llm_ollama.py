"""The Ollama (local model) adapter, against a fake Ollama server: no model or network needed."""
from __future__ import annotations

import json

import httpx
import pytest

from app.errors import LLMError
from app.llm.factory import build_llm
from app.llm.ollama_client import OllamaClient, check_base_url
from app.services import connections
from tests.conftest import make_settings

TOOLS = [{"name": "create_task", "description": "Create a task.", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}}}]


def _client(handler, **kw) -> tuple[OllamaClient, list[dict]]:
    seen: list[dict] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": json.loads(request.content)})
        return handler(request)

    return OllamaClient("http://127.0.0.1:11434", "qwen2.5:3b", 800, 0.2, num_ctx=16384,
                        client=httpx.Client(transport=httpx.MockTransport(record)), **kw), seen


def test_a_tool_call_round_trip_keeps_keels_message_format():
    def handler(_):
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "create_task", "arguments": {"title": "Write intro"}}}]},
            "done_reason": "stop", "prompt_eval_count": 900, "eval_count": 20})

    llm, seen = _client(handler)
    out = llm.complete(system="You are Keel.", messages=[{"role": "user", "content": "add: write intro"}], tools=TOOLS)
    assert out.stop_reason == "tool_use" and out.input_tokens == 900 and out.output_tokens == 20
    call = out.tool_uses[0]
    assert call["name"] == "create_task" and call["input"] == {"title": "Write intro"} and call["id"].startswith("call_")

    body = seen[0]["body"]
    assert seen[0]["url"] == "http://127.0.0.1:11434/api/chat" and body["stream"] is False
    assert body["options"] == {"num_ctx": 16384, "num_predict": 800, "temperature": 0.2}  # the context window is set, not left at the small default
    assert body["messages"][0] == {"role": "system", "content": "You are Keel."}
    assert body["tools"][0]["function"]["name"] == "create_task" and body["tools"][0]["function"]["parameters"]["type"] == "object"

    # The next turn sends the call and its result back the way Ollama expects.
    history = [
        {"role": "user", "content": "add: write intro"},
        {"role": "assistant", "content": [call]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": '{"task": {"id": 1}}'}]},
    ]
    llm.complete(system="s", messages=history, tools=TOOLS)
    sent = seen[1]["body"]["messages"]
    assert sent[2] == {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "create_task", "arguments": {"title": "Write intro"}}}]}
    assert sent[3] == {"role": "tool", "content": '{"task": {"id": 1}}', "tool_name": "create_task"}


def test_a_text_reply_and_string_arguments():
    replies = iter([
        {"message": {"content": "Done. Anything else?"}, "done_reason": "stop"},
        {"message": {"content": "", "tool_calls": [{"function": {"name": "create_task", "arguments": '{"title": "x"}'}},
                                                   {"function": {"name": "create_task", "arguments": "not json"}}]}},
        {"message": {"content": "cut off"}, "done_reason": "length"},
    ])
    llm, _ = _client(lambda _: httpx.Response(200, json=next(replies)))
    first = llm.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
    assert first.text == "Done. Anything else?" and first.stop_reason == "end_turn"
    second = llm.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=TOOLS)
    assert [c["input"] for c in second.tool_uses] == [{"title": "x"}, {}]  # bad JSON becomes {} and fails validation later
    assert len({c["id"] for c in second.tool_uses}) == 2
    assert llm.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[]).stop_reason == "max_tokens"


@pytest.mark.parametrize(("response", "message"), [
    (httpx.Response(404, json={"error": "model 'qwen2.5:3b' not found"}), "ollama pull qwen2.5:3b"),
    (httpx.Response(400, json={"error": "registry.ollama.ai/library/phi3:mini does not support tools"}), "can't call tools"),
    (httpx.Response(500, json={"error": "out of memory"}), "Ollama error (HTTP 500): out of memory"),
    (httpx.Response(200, text="<html>"), "isn't JSON"),
])
def test_errors_say_what_to_do(response, message):
    llm, _ = _client(lambda _: response)
    with pytest.raises(LLMError, match=message.replace("(", r"\(").replace(")", r"\)")):
        llm.complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=TOOLS)


def test_ollama_not_running_or_too_slow():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError, match="Is it running"):
        _client(down)[0].complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])
    with pytest.raises(LLMError, match="took too long"):
        _client(slow)[0].complete(system="s", messages=[{"role": "user", "content": "hi"}], tools=[])


def test_plain_http_only_to_this_computer():
    assert check_base_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert check_base_url("http://localhost:11434") == "http://localhost:11434"
    assert check_base_url("http://[::1]:11434") == "http://[::1]:11434"
    assert check_base_url("https://ollama.example.org") == "https://ollama.example.org"
    for bad in ("http://192.168.1.20:11434", "http://ollama.lan:11434", "ftp://127.0.0.1", "127.0.0.1:11434"):
        with pytest.raises(LLMError):
            check_base_url(bad)


def test_factory_and_services_page():
    s = make_settings(llm_provider="ollama", ollama_model="qwen2.5:3b", groq_api_key="gsk_unused")
    llm = build_llm(s)
    assert isinstance(llm, OllamaClient)  # chosen explicitly, even with a hosted key set
    assert not isinstance(build_llm(make_settings(groq_api_key="gsk_x")), OllamaClient)  # never picked by "auto"
    row = next(r for r in connections.describe(s) if r["id"] == "model")
    assert row["details"] == ["Ollama on 127.0.0.1 · qwen2.5:3b", "Local: chats stay on this computer"] and row["testable"]
    remote = make_settings(llm_provider="ollama", ollama_base_url="https://gpu.example.org")
    assert next(r for r in connections.describe(remote) if r["id"] == "model")["details"] == ["Ollama on gpu.example.org · qwen2.5:7b"]


def test_model_check_asks_ollama_without_sending_a_prompt(monkeypatch):
    s = make_settings(llm_provider="ollama", ollama_model="qwen2.5:3b")
    asked = []

    def fake_post(url, json, timeout):
        asked.append((url, json))
        caps = ["completion", "tools"] if json["model"] == "qwen2.5:3b" else ["completion"]
        return httpx.Response(200, json={"capabilities": caps}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert connections.run_check("model", s) == {"ok": True, "detail": "qwen2.5:3b is installed and can call tools"}
    assert asked == [("http://127.0.0.1:11434/api/show", {"model": "qwen2.5:3b"})]  # /api/show, never /api/chat
    out = connections.run_check("model", make_settings(llm_provider="ollama", ollama_model="phi3:mini"))
    assert out["ok"] is False and "can't call tools" in out["detail"]
    assert connections.run_check("model", make_settings(groq_api_key="gsk_x"))["ok"] is None  # no free check for hosted models
