"""Helpers for treating external content (web pages, files, emails, ...) as data, never as instructions."""
import json
import re
import unicodedata

TAG = "untrusted_content"

# Invisible characters are a known way to hide instructions from humans while models still read them
# (e.g. the Unicode "tag" block U+E0000-E007F, zero-width characters, bidi overrides). Strip them all.
_STRIP_CATEGORIES = {"Cf", "Co", "Cn", "Cs"}
_KEEP_CONTROL = {"\n", "\t"}


def sanitize_text(text: str) -> str:
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cc":
            if ch in _KEEP_CONTROL:
                out.append(ch)
        elif cat not in _STRIP_CATEGORIES:
            out.append(ch)
    return "".join(out)


def wrap_untrusted(content: str, source: str) -> str:
    """Fence external text so the model can tell data from instructions.

    Invisible characters are removed, and the closing tag is neutralised so the content cannot break out of its fence.
    """
    safe = sanitize_text(content)
    safe = re.sub(rf"</\s*{TAG}\s*>", f"[/{TAG}]", safe, flags=re.IGNORECASE)
    src = "".join(c for c in source if c.isalnum() or c in "._:/-")
    return f'<{TAG} source="{src}">\n{safe}\n</{TAG}>'


def contains_untrusted(messages: list[dict]) -> bool:
    """True if any message in the LLM context carries fenced external content."""
    return f"<{TAG}" in json.dumps(messages, default=str)


_URL = re.compile(r"https?://[^\s\"'<>\\\])}]+", re.IGNORECASE)


def normalize_url(url: str) -> str:
    return url.rstrip(".,;:!?)")


def urls_in_text(text: str) -> set[str]:
    return {normalize_url(u) for u in _URL.findall(text)}


def extract_urls(messages: list[dict]) -> set[str]:
    """URLs the model may fetch freely even when the session is tainted: only ones the *user typed*. Links inside
    external text are not included: a page listing ten links would otherwise let an injected model signal a digit to
    the page's author by which one it opens.

    Deliberately NOT included: anything the model itself wrote (tool_use inputs, its replies) and the text of
    pending-confirmation/error results. Otherwise a URL the model assembled -- e.g. with private data in the query
    string -- would count as 'seen' the moment its first, gated attempt was recorded, and a retry would slip through.
    """
    found: set[str] = set()
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m["content"]
        if isinstance(content, str):
            found |= urls_in_text(content)
            continue
        for block in content:
            if block.get("type") == "text":
                found |= urls_in_text(block.get("text", ""))
    return found
