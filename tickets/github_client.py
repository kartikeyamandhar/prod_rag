"""Paced, retrying GitHub REST client for the ticket snapshot pull.

Read-only, public scope. Pacing is fixed to stay politely inside authenticated
limits: search API 30 requests/min, core API 5000 requests/hr. On a rate-limit
response the client sleeps to the advertised reset instead of hammering.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

API = "https://api.github.com"
SEARCH_SLEEP_S = 2.1
CORE_SLEEP_S = 0.75
MAX_ATTEMPTS = 5


class GitHubError(Exception):
    """Raised when the GitHub API fails after retries or returns an unexpected error."""


class GitHubClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise GitHubError("GITHUB_TOKEN is empty; the snapshot pull needs it")
        self._client = httpx.Client(
            base_url=API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "rag-incident-lab-corpus-pull",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, object], pace_s: float) -> httpx.Response:
        for attempt in range(MAX_ATTEMPTS):
            response = self._client.get(path, params=params)
            remaining = response.headers.get("x-ratelimit-remaining")
            if response.status_code in (403, 429) and remaining == "0":
                reset = float(response.headers.get("x-ratelimit-reset", "0"))
                wait = min(max(reset - time.time(), 1.0), 3600.0)
                logger.warning("rate limited on %s; sleeping %.0fs to reset", path, wait)
                time.sleep(wait)
                continue
            if response.status_code >= 500:
                backoff = 2.0**attempt
                logger.warning("%s -> %d; backing off %.0fs", path, response.status_code, backoff)
                time.sleep(backoff)
                continue
            if response.is_error:
                raise GitHubError(f"{path} -> {response.status_code}: {response.text[:200]}")
            time.sleep(pace_s)
            return response
        raise GitHubError(f"{path}: {MAX_ATTEMPTS} attempts exhausted")

    def search_issues_window(self, query: str) -> tuple[int, list[dict]]:
        """One search query paginated to the API's 1000-result cap.

        Returns (total_count, items). Callers must split their query window when
        total_count exceeds 1000, because results past 1000 are unreachable.
        """
        items: list[dict] = []
        page = 1
        total = 0
        while True:
            response = self._get(
                "/search/issues", {"q": query, "per_page": 100, "page": page}, SEARCH_SLEEP_S
            )
            data = response.json()
            total = data["total_count"]
            items.extend(data["items"])
            if len(data["items"]) < 100 or page >= 10:
                return total, items
            page += 1

    def issue_timeline(self, owner_repo: str, number: int) -> list[dict]:
        events: list[dict] = []
        page = 1
        while True:
            response = self._get(
                f"/repos/{owner_repo}/issues/{number}/timeline",
                {"per_page": 100, "page": page},
                CORE_SLEEP_S,
            )
            batch = response.json()
            events.extend(batch)
            if len(batch) < 100 or page >= 10:
                return events
            page += 1
