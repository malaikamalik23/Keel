from dataclasses import dataclass, field

import pytest

from app.errors import LLMError
from app.llm.factory import build_llm
from app.llm.groq_client import GroqClient, _messages_to_groq, _response_from_groq, _tools_to_groq
from tests.conftest import make_settings


# ---------- provider selection ----------
def test_auto_picks_anthropic_when_only_anthropic_key_set():
    from app.llm.anthropic_client import AnthropicClient

    llm = build_llm(make_settings(anthropic_api_key="sk-ant-x", groq_api_key=None))
    assert isinstance(llm, AnthropicClient)


def test_auto_picks_groq_when_only_groq_key_set():
    llm = build_llm(make_settings(anthropic_api_key=None, groq_api_key="gsk-x"))
    assert isinstance(llm, GroqClient)


def test_no_keys_defers_the_error_until_chat():
    from app.llm.factory import UnconfiguredLLM

    llm = build_llm(make_settings(anthropic_api_key=None, groq_api_key=None))
    assert isinstance(llm, UnconfiguredLLM)
    with pytest.raises(LLMError):
        llm.complete(system="s", messages=[], tools=[])


def test_explicit_provider_without_its_key_raises_clearly():
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        build_llm(make_settings(anthropic_api_key="sk-ant-x", groq_api_key=None, llm_provider="groq"))


# ---------- tool schema translation ----------
def test_tools_to_groq_wraps_as_openai_function_spec():
    anthropic_tools = [{"name": "list_tasks", "description": "List tasks.", "input_schema": {"type": "object", "properties": {}}}]
    out = _tools_to_groq(anthropic_tools)
    assert out == [
        {"type": "function", "function": {"name": "list_tasks", "description": "List tasks.", "parameters": {"type": "object", "properties": {}}}}
    ]


# ---------- outbound message translation ----------
def test_plain_user_and_assistant_text_messages():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ]
    out = _messages_to_groq("system prompt", messages)
    assert out == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_assistant_tool_use_becomes_tool_calls_and_result_becomes_tool_role_message():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "add a task"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Sure,"},
                {"type": "tool_use", "id": "tu_1", "name": "create_task", "input": {"title": "x"}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": '{"ok": true}', "is_error": False}]},
    ]
    out = _messages_to_groq("sys", messages)
    assert out[2] == {
        "role": "assistant",
        "content": "Sure,",
        "tool_calls": [{"id": "tu_1", "type": "function", "function": {"name": "create_task", "arguments": '{"title": "x"}'}}],
    }
    assert out[3] == {"role": "tool", "tool_call_id": "tu_1", "content": '{"ok": true}'}


def test_assistant_tool_use_with_no_text_has_null_content():
    messages = [{"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "list_tasks", "input": {}}]}]
    out = _messages_to_groq("sys", messages)
    assert out[1]["content"] is None


# ---------- inbound response translation ----------
@dataclass
class _FakeFunction:
    name: str
    arguments: str


@dataclass
class _FakeToolCall:
    id: str
    function: _FakeFunction


@dataclass
class _FakeMessage:
    content: str | None
    tool_calls: list = field(default_factory=list)


def test_response_with_text_only():
    resp = _response_from_groq(_FakeMessage(content="hello there"), "stop")
    assert resp.content == [{"type": "text", "text": "hello there"}]
    assert resp.stop_reason == "end_turn"
    assert resp.text == "hello there"


def test_response_with_tool_call_maps_finish_reason_and_parses_arguments():
    msg = _FakeMessage(content=None, tool_calls=[_FakeToolCall(id="c1", function=_FakeFunction("create_task", '{"title": "x"}'))])
    resp = _response_from_groq(msg, "tool_calls")
    assert resp.stop_reason == "tool_use"
    assert resp.tool_uses == [{"type": "tool_use", "id": "c1", "name": "create_task", "input": {"title": "x"}}]


def test_response_with_malformed_arguments_falls_back_to_empty_dict():
    msg = _FakeMessage(content=None, tool_calls=[_FakeToolCall(id="c1", function=_FakeFunction("create_task", "not json"))])
    resp = _response_from_groq(msg, "tool_calls")
    assert resp.tool_uses[0]["input"] == {}


def test_length_finish_reason_maps_to_max_tokens():
    resp = _response_from_groq(_FakeMessage(content="cut off"), "length")
    assert resp.stop_reason == "max_tokens"


def _groq_error(status: int, error: dict):
    import groq
    import httpx

    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    cls = groq.BadRequestError if status == 400 else groq.InternalServerError
    return cls("boom", response=httpx.Response(status, request=req), body={"error": error})


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _client_with(outcomes):
    from types import SimpleNamespace

    gc = GroqClient(api_key="gsk_test", model="m")
    fake = _FakeCompletions(outcomes)
    gc._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return gc, fake


def _ok_tool_call():
    from types import SimpleNamespace

    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="create_reminder", arguments='{"text": "x", "remind_at": "2099-01-01T09:00"}'))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]), finish_reason="tool_calls")])


REJECTED = {"code": "tool_use_failed", "message": "parameters for tool create_reminder did not match schema: `/remind_at` invalid"}


def test_rejected_tool_call_is_retried_once_with_the_reason():
    gc, fake = _client_with([_groq_error(400, REJECTED), _ok_tool_call()])
    resp = gc.complete(system="s", messages=[{"role": "user", "content": [{"type": "text", "text": "remind me"}]}], tools=[])
    assert resp.stop_reason == "tool_use" and resp.tool_uses[0]["name"] == "create_reminder"
    assert len(fake.calls) == 2
    note = fake.calls[1]["messages"][-1]
    assert note["role"] == "user" and "not written by the user" in note["content"] and "/remind_at" in note["content"]
    assert fake.calls[0]["messages"] == fake.calls[1]["messages"][:-1]  # the original request is untouched


def test_a_second_rejection_and_other_errors_are_not_retried_forever():
    gc, fake = _client_with([_groq_error(400, REJECTED), _groq_error(400, REJECTED)])
    with pytest.raises(LLMError):
        gc.complete(system="s", messages=[], tools=[])
    assert len(fake.calls) == 2
    for other in (_groq_error(400, {"code": "context_length_exceeded", "message": "too long"}), _groq_error(500, {"message": "down"})):
        gc, fake = _client_with([other])
        with pytest.raises(LLMError):
            gc.complete(system="s", messages=[], tools=[])
        assert len(fake.calls) == 1


def test_temperature_is_sent_when_set_and_left_to_the_provider_when_not():
    gc, fake = _client_with([_ok_tool_call(), _ok_tool_call()])
    gc._temperature = 0.2
    gc.complete(system="s", messages=[], tools=[])
    assert fake.calls[0]["temperature"] == 0.2
    gc._temperature = None
    gc.complete(system="s", messages=[], tools=[])
    assert "temperature" not in fake.calls[1]


def test_the_retry_after_a_rejected_tool_call_keeps_the_same_temperature():
    gc, fake = _client_with([_groq_error(400, REJECTED), _ok_tool_call()])
    gc._temperature = 0.2
    gc.complete(system="s", messages=[], tools=[])
    assert [c["temperature"] for c in fake.calls] == [0.2, 0.2]


def test_temperature_setting_reaches_both_providers_and_is_validated():
    from app.config import Settings
    from app.llm.anthropic_client import AnthropicClient
    from app.llm.factory import build_llm

    groq_llm = build_llm(Settings(_env_file=None, groq_api_key="gsk_test", llm_temperature=0.0))
    assert isinstance(groq_llm, GroqClient) and groq_llm._temperature == 0.0
    claude = build_llm(Settings(_env_file=None, anthropic_api_key="sk-test"))
    assert isinstance(claude, AnthropicClient) and claude._temperature == 0.2  # the default
    assert Settings(_env_file=None, llm_temperature="").llm_temperature is None  # LLM_TEMPERATURE= -> provider default
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError
        Settings(_env_file=None, llm_temperature=1.5)


def test_anthropic_client_sends_temperature_only_when_set():
    from types import SimpleNamespace

    from app.llm.anthropic_client import AnthropicClient

    calls = []
    reply = SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")], stop_reason="end_turn", usage=None)
    for temperature in (0.2, None):
        client = AnthropicClient("sk-test", "m", temperature=temperature)
        client._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw) or reply))
        client.complete(system="s", messages=[], tools=[])
    assert calls[0]["temperature"] == 0.2 and "temperature" not in calls[1]
