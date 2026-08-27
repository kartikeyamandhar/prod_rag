"""Fetch closing-PR title/body: the judge's real ground truth (audit B4/A2 fix).

One REST call per linked held-out ticket via the existing paced GitHubClient.
Results land in a committed cache (never re-paid) and in two ticket columns the
loader re-hydrates after every corpus reset.

Run: uv run --env-file .env python -m tickets.fetch_pr_content
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import psycopg

from tickets.github_client import GitHubClient, GitHubError

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "artifacts" / "pr_content_cache.json"
DDL = (
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS closing_pr_title TEXT;"
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS closing_pr_body TEXT;"
)


def hydrate(conn: psycopg.Connection, cache: dict) -> int:
    conn.execute(DDL)
    updated = 0
    with conn.cursor() as cur:
        for pr_number, content in cache.items():
            if "title" not in content:
                continue
            cur.execute(
                "UPDATE tickets SET closing_pr_title = %s, closing_pr_body = %s"
                " WHERE closing_pr_number = %s",
                (content["title"], content["body"], int(pr_number)),
            )
            updated += cur.rowcount
    conn.commit()
    return updated


def main() -> None:
    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT closing_pr_number FROM tickets"
                " WHERE is_held_out AND closing_pr_number IS NOT NULL"
            )
            pr_numbers = [row[0] for row in cur.fetchall()]

        missing = [n for n in pr_numbers if str(n) not in cache]
        if missing:
            client = GitHubClient(os.environ["GITHUB_TOKEN"])
            for pr_number in missing:
                try:
                    response = client._get(
                        f"/repos/kubernetes/kubernetes/pulls/{pr_number}", {}, 0.75
                    )
                    data = response.json()
                    cache[str(pr_number)] = {
                        "title": data.get("title") or "",
                        "body": (data.get("body") or "")[:4000],
                    }
                except GitHubError as exc:
                    logger.warning("PR %s fetch failed: %s", pr_number, exc)
                    cache[str(pr_number)] = {"error": str(exc)[:200]}
                CACHE_PATH.write_text(json.dumps(cache, indent=1) + "\n")
            client.close()

        updated = hydrate(conn, cache)
    ok = sum(1 for content in cache.values() if "title" in content)
    print(f"PR CONTENT: {ok}/{len(pr_numbers)} linked held-out PRs cached; {updated} rows hydrated")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
