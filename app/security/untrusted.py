"""Helpers for treating external content (web pages, files, emails, ...) as data, never as instructions."""
import json

TAG = "untrusted_content"


def wrap_untrusted(content: str, source: str) -> str:
    """Fence external text so the model can tell data from instructions.

    The closing tag is neutralised so the content cannot break out of its fence.
    """
    safe = content.replace(f"</{TAG}>", f"[/{TAG}]")
    src = "".join(c for c in source if c.isalnum() or c in "._:/-")
    return f'<{TAG} source="{src}">\n{safe}\n</{TAG}>'


def contains_untrusted(messages: list[dict]) -> bool:
    """True if any message in the LLM context carries fenced external content."""
    return f"<{TAG}" in json.dumps(messages, default=str)
