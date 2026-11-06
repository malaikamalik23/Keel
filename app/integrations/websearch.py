"""Web search through the Brave Search API (needs BRAVE_API_KEY). Results are external text -> untrusted."""
from __future__ import annotations

import re

import httpx

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_TAGS = re.compile(r"<[^>]+>")


class SearchError(Exception):
    pass


class BraveSearch:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._key = api_key
        self._client = client or httpx.Client(timeout=10)

    def search(self, query: str, count: int = 5) -> list[dict]:
        try:
            resp = self._client.get(
                ENDPOINT, params={"q": query, "count": max(1, min(count, 10))},
                headers={"Accept": "application/json", "X-Subscription-Token": self._key},
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"Search request failed ({type(exc).__name__}).") from None  # no URL/headers in the message
        if resp.status_code in (401, 403):
            raise SearchError("The search provider rejected the API key.")
        if resp.status_code == 429:
            raise SearchError("Search rate limit reached; try again later.")
        if resp.status_code != 200:
            raise SearchError(f"Search provider error (HTTP {resp.status_code}).")
        try:
            raw = resp.json().get("web", {}).get("results", [])
        except ValueError:
            raise SearchError("Search provider returned invalid data.") from None
        out = []
        for r in raw:
            url = str(r.get("url", ""))
            if not url.startswith(("http://", "https://")):
                continue
            out.append({
                "title": _TAGS.sub("", str(r.get("title", "")))[:200],
                "url": url[:500],
                "snippet": _TAGS.sub("", str(r.get("description", "")))[:400],
            })
        return out[:count]
