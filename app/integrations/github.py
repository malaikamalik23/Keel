"""Read-only GitHub access. Fixed host (api.github.com), validated owner/repo names, token only in a header."""
from __future__ import annotations

import re
from typing import Any

import httpx

BASE = "https://api.github.com"
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class GitHubError(Exception):
    pass


def validate_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not _OWNER.match(owner) or not _NAME.match(name) or name in (".", "..") or "/" in name:
        raise GitHubError("Repository must look like 'owner/name'.")
    return owner, name


def validate_owner(owner: str) -> str:
    if not _OWNER.match(owner):
        raise GitHubError("Invalid GitHub username.")
    return owner


class GitHubClient:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None):
        self._token = token
        self._client = client or httpx.Client(timeout=10)

    def _get(self, path: str, params: dict | None = None, accept: str = "application/vnd.github+json") -> httpx.Response:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Keel-agent"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = self._client.get(BASE + path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed ({type(exc).__name__}).") from None
        if resp.status_code == 404:
            raise GitHubError("Not found (or private and no token is configured).")
        if resp.status_code in (403, 429):
            raise GitHubError("GitHub refused the request (rate limit or missing permission).")
        if resp.status_code == 401:
            raise GitHubError("GitHub rejected the token.")
        if resp.status_code != 200:
            raise GitHubError(f"GitHub error (HTTP {resp.status_code}).")
        return resp

    def list_repos(self, owner: str, limit: int = 20) -> list[dict]:
        owner = validate_owner(owner)
        rows: Any = self._get(f"/users/{owner}/repos", {"per_page": min(limit, 50), "sort": "pushed"}).json()
        return [
            {"name": r["full_name"], "description": (r.get("description") or "")[:200], "language": r.get("language"),
             "stars": r.get("stargazers_count"), "pushed_at": r.get("pushed_at"), "fork": r.get("fork")}
            for r in rows
        ]

    def get_readme(self, repo: str, max_chars: int = 8000) -> str:
        owner, name = validate_repo(repo)
        return self._get(f"/repos/{owner}/{name}/readme", accept="application/vnd.github.raw+json").text[:max_chars]

    def list_issues(self, repo: str, state: str = "open", limit: int = 15) -> list[dict]:
        owner, name = validate_repo(repo)
        rows: Any = self._get(f"/repos/{owner}/{name}/issues", {"state": state, "per_page": min(limit, 50)}).json()
        return [
            {"number": r["number"], "title": r["title"][:200], "state": r["state"], "is_pull_request": "pull_request" in r,
             "author": (r.get("user") or {}).get("login"), "labels": [lab["name"] for lab in r.get("labels", [])][:5],
             "updated_at": r.get("updated_at")}
            for r in rows
        ]

    def recent_commits(self, repo: str, limit: int = 10) -> list[dict]:
        owner, name = validate_repo(repo)
        rows: Any = self._get(f"/repos/{owner}/{name}/commits", {"per_page": min(limit, 50)}).json()
        return [
            {"sha": r["sha"][:7], "message": r["commit"]["message"].splitlines()[0][:200] if r["commit"]["message"] else "",
             "author": (r["commit"].get("author") or {}).get("name"), "date": (r["commit"].get("author") or {}).get("date")}
            for r in rows
        ]
