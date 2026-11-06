from app.llm.base import LLMResponse


def text(t: str) -> LLMResponse:
    return LLMResponse([{"type": "text", "text": t}], "end_turn")


def tool_call(name: str, input: dict, id: str = "tu_1", preamble: str | None = None) -> LLMResponse:
    content = [{"type": "text", "text": preamble}] if preamble else []
    content.append({"type": "tool_use", "id": id, "name": name, "input": input})
    return LLMResponse(content, "tool_use")


class ScriptedLLM:
    """Returns pre-written responses in order and records what it was called with."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        return self.responses.pop(0)


class LoopingLLM:
    """Always asks for another tool call (to test the step limit)."""

    def complete(self, *, system, messages, tools):
        return tool_call("get_current_datetime", {})
