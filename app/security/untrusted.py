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


# Tools whose result URLs were chosen by a service you configured (the search engine), not by the author of a page.
URL_OFFERING_TOOLS = {"web_search"}
_FENCE = re.compile(rf'^<{TAG} source="([^"]*)">\n(.*)\n</{TAG}>$', re.DOTALL)


def offered_urls(result: dict) -> set[str]:
    """The result URLs of a search, i.e. the links the model may open next without asking.

    Deliberately narrow. URLs that merely appear *inside* external text (a fetched page, a snippet, a README, a note)
    are not included: a page listing ten links would otherwise let an injected model signal a digit to the page's
    author by which one it opens. Only the `url` field of each search result counts; the fence's `source` attribute
    is written by code from the tool's name, so page content cannot claim to be a search result.
    """
    data = result.get("untrusted_data") if isinstance(result, dict) else None
    match = _FENCE.match(data) if isinstance(data, str) else None
    if not match or match.group(1) not in URL_OFFERING_TOOLS:
        return set()
    try:
        results = json.loads(match.group(2)).get("results", [])
    except (ValueError, AttributeError):
        return set()
    return {normalize_url(r["url"]) for r in results if isinstance(r, dict) and isinstance(r.get("url"), str)}


def extract_urls(messages: list[dict]) -> set[str]:
    """URLs the model may fetch freely even when the session is tainted: ones the *user typed*, and the result URLs
    of a search (see `offered_urls`). Links inside other external text are not included.

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
            elif block.get("type") == "tool_result":
                try:
                    found |= offered_urls(json.loads(block.get("content", "")))
                except (ValueError, TypeError):
                    pass
    return found
