"""Ollama adapter: run Keel on a model on your own computer, so chats never leave it.

Uses Ollama's native /api/chat (not its OpenAI-compatible endpoint) because only the native API lets us set the
context window: Ollama's default is a few thousand tokens, smaller than Keel's system prompt plus tool list, and it
silently drops the start of the prompt when it overflows. Messages are translated from Keel's content-block format the
same way as for Groq; Ollama tool calls carry no id, so one is made up for each and matched back by position.

Small local models choose tools less reliably than hosted ones: run `python -m evals.run_routing` with
LLM_PROVIDER=ollama to see the score for yours. Every write still goes through the same permission checks.
"""
from __future__ import annotations

import ipaddress
import json
import uuid
from urllib.parse import urlparse

import httpx

from app.errors import LLMError
from app.llm.base import LLMResponse

_DONE = {"stop": "end_turn", "length": "max_tokens"}


def check_base_url(url: str) -> str:
    """Plain http only to this computer: a chat sent to another machine unencrypted could be read on the way."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise LLMError("OLLAMA_BASE_URL must look like http://127.0.0.1:11434")
    if parsed.scheme == "http":
        host = parsed.hostname
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise LLMError("OLLAMA_BASE_URL must be https unless Ollama runs on this computer (127.0.0.1 or localhost).")
    return url.strip().rstrip("/")


def _text(blocks: list[dict]) -> str:
    return "\n".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text"))


def _messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    names: dict[str, str] = {}  # tool_use id -> tool name, so each result can say which tool it answers
    for msg in messages:
        content = msg["content"]
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        if msg["role"] == "assistant":
            calls = [b for b in blocks if b["type"] == "tool_use"]
            entry: dict = {"role": "assistant", "content": _text(blocks)}
            if calls:
                names.update({c["id"]: c["name"] for c in calls})
                entry["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["input"]}} for c in calls]
            out.append(entry)
            continue
        results = [b for b in blocks if b["type"] == "tool_result"]
        if results:
            for r in results:
                out.append({"role": "tool", "content": r["content"] if isinstance(r["content"], str) else json.dumps(r["content"]),
                            "tool_name": names.get(r["tool_use_id"], "")})
        else:
            out.append({"role": "user", "content": _text(blocks)})
    return out


def _response(data: dict) -> LLMResponse:
    msg = data.get("message") or {}
    blocks: list[dict] = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):  # some models return the arguments as a JSON string
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        blocks.append({"type": "tool_use", "id": f"call_{uuid.uuid4().hex[:12]}", "name": str(fn.get("name", "")),
                       "input": args if isinstance(args, dict) else {}})
    reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else _DONE.get(data.get("done_reason") or "stop", "end_turn")
    return LLMResponse(content=blocks, stop_reason=reason,
                       input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"))


class OllamaClient:
    def __init__(self, base_url: str, model: str, max_tokens: int = 1500, temperature: float | None = None,
                 num_ctx: int = 16384, timeout: float = 300, client: httpx.Client | None = None):
        self._url = check_base_url(base_url)
        self._model = model
        self._options: dict = {"num_ctx": num_ctx, "num_predict": max_tokens}
        if temperature is not None:
            self._options["temperature"] = temperature
        self._client = client or httpx.Client(timeout=timeout)

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        body: dict = {"model": self._model, "messages": _messages(system, messages), "stream": False, "options": self._options}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                               "parameters": t["input_schema"]}} for t in tools]
        try:
            resp = self._client.post(f"{self._url}/api/chat", json=body)
        except httpx.TimeoutException:
            raise LLMError(f"The local model took too long to answer. A smaller model than {self._model} may help.") from None
        except httpx.HTTPError:
            raise LLMError(f"Can't reach Ollama at {self._url}. Is it running? Start the Ollama app or run `ollama serve`.") from None
        if resp.status_code != 200:
            raise LLMError(self._explain(resp))
        try:
            data = resp.json()
        except ValueError:
            raise LLMError("Ollama returned something that isn't JSON.") from None
        return _response(data)

    def _explain(self, resp: httpx.Response) -> str:
        try:
            detail = str(resp.json().get("error", ""))[:300]
        except ValueError:
            detail = ""
        if resp.status_code == 404 or "not found" in detail:
            return f"The model {self._model} isn't on this computer. Run `ollama pull {self._model}`."
        if "does not support tools" in detail:
            return (f"{self._model} can't call tools, which Keel needs. Pick one that can, such as qwen2.5:7b or llama3.1:8b "
                    "(`ollama show <model>` lists 'tools' under Capabilities).")
        return f"Ollama error (HTTP {resp.status_code}): {detail or 'no details'}"
