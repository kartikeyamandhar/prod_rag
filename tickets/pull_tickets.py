"""Snapshot pull of kubernetes/kubernetes closed kind/bug issues for the chosen SIGs.

Produces artifacts/tickets_raw_<date>.jsonl, one issue per line, enriched with the
closing PR via GraphQL closedByPullRequestsReferences (GitHub's own closing linkage;
REST timeline cross-references are suppressed under fine-grained tokens, verified
live). The search filter `linked:pr` implements the closing-PR half of CLAUDE.md's
resolution-linked definition; the accepted-answer half has no API-native marker and
is deliberately out of scope (flagged in PHASES.md).

Run: uv run --env-file .env python -m tickets.pull_tickets
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from tickets.github_client import GitHubClient

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = REPO_ROOT / "artifacts"

REPO = "kubernetes/kubernetes"
SIGS = ("sig/network", "sig/scheduling", "sig/storage")
CREATED_FROM = dt.date(2022, 1, 1)
SEARCH_RESULT_CAP = 1000


def _query(sig: str, start: dt.date, end: dt.date) -> str:
    return (
        f"repo:{REPO} is:issue state:closed label:kind/bug label:{sig} "
        f"linked:pr created:{start.isoformat()}..{end.isoformat()}"
    )


def pull_sig_window(client: GitHubClient, sig: str, start: dt.date, end: dt.date) -> list[dict]:
    """Pull one SIG's issues in [start, end], splitting the window under the 1000 cap."""
    total, items = client.search_issues_window(_query(sig, start, end))
    if total <= SEARCH_RESULT_CAP:
        logger.info("%s %s..%s: %d issues", sig, start, end, total)
        return items
    if start >= end:
        raise RuntimeError(f"single-day window {sig} {start} still exceeds the search cap")
    mid = start + (end - start) / 2
    logger.info("%s %s..%s: %d > cap, splitting at %s", sig, start, end, total, mid)
    return pull_sig_window(client, sig, start, mid) + pull_sig_window(
        client, sig, mid + dt.timedelta(days=1), end
    )


GRAPHQL_BATCH = 50

_CLOSED_BY_FRAGMENT = (
    "i{n}: issue(number: {n}) {{ closedByPullRequestsReferences("
    "first: 5, includeClosedPrs: true) {{ nodes {{ number url merged "
    "repository {{ nameWithOwner }} }} }} }}"
)


def pick_closing_pr(nodes: list[dict]) -> tuple[int | None, str | None]:
    """First merged upstream closing PR; falls back to the first upstream reference.

    closedByPullRequestsReferences is GitHub's own closing linkage (the same signal
    the linked:pr search qualifier uses), so no fork or heuristic filtering beyond
    the upstream check is needed. Follow-up PRs can also appear; the first merged
    one is taken as the primary fix.
    """
    upstream = [n for n in nodes if n.get("repository", {}).get("nameWithOwner") == REPO]
    for node in upstream:
        if node.get("merged"):
            return node.get("number"), node.get("url")
    if upstream:
        return upstream[0].get("number"), upstream[0].get("url")
    return None, None


def closing_prs_batch(
    client: GitHubClient, numbers: list[int]
) -> dict[int, tuple[int | None, str | None]]:
    """Resolve closing PRs for many issues via aliased GraphQL queries."""
    results: dict[int, tuple[int | None, str | None]] = {}
    for offset in range(0, len(numbers), GRAPHQL_BATCH):
        batch = numbers[offset : offset + GRAPHQL_BATCH]
        fields = " ".join(_CLOSED_BY_FRAGMENT.format(n=n) for n in batch)
        query = f'query {{ repository(owner: "kubernetes", name: "kubernetes") {{ {fields} }} }}'
        data = client.graphql(query)["repository"]
        for n in batch:
            nodes = (
                (data.get(f"i{n}") or {}).get("closedByPullRequestsReferences", {}).get("nodes", [])
            )
            results[n] = pick_closing_pr(nodes)
        logger.info("closing PRs resolved for %d/%d issues", len(results), len(numbers))
    return results


def _issue_record(item: dict, sig: str) -> dict:
    labels = [label["name"] for label in item.get("labels", [])]
    return {
        "number": item["number"],
        "title": item["title"],
        "body": item.get("body") or "",
        "labels": labels,
        "sigs": sorted(label for label in labels if label in SIGS),
        "matched_sig": sig,
        "created_at": item["created_at"],
        "closed_at": item.get("closed_at"),
        "url": item["html_url"],
    }


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    snapshot_date = dt.date.today().isoformat()
    client = GitHubClient(token)

    by_number: dict[int, dict] = {}
    per_sig_raw: dict[str, int] = {}
    today = dt.date.today()
    for sig in SIGS:
        year = CREATED_FROM.year
        sig_count = 0
        while year <= today.year:
            start = dt.date(year, 1, 1)
            end = min(dt.date(year, 12, 31), today)
            for item in pull_sig_window(client, sig, start, end):
                sig_count += 1
                record = _issue_record(item, sig)
                existing = by_number.get(record["number"])
                if existing is None:
                    by_number[record["number"]] = record
            year += 1
        per_sig_raw[sig] = sig_count

    logger.info("deduplicated to %d unique issues", len(by_number))
    resolved = closing_prs_batch(client, sorted(by_number))
    for number, record in by_number.items():
        record["closing_pr_number"], record["closing_pr_url"] = resolved[number]
    client.close()

    ARTIFACTS.mkdir(exist_ok=True)
    out_path = ARTIFACTS / f"tickets_raw_{snapshot_date}.jsonl"
    with out_path.open("w", encoding="utf-8") as fh:
        for record in sorted(by_number.values(), key=lambda r: r["number"]):
            fh.write(json.dumps(record) + "\n")

    print(f"SNAPSHOT DATE: {snapshot_date}")
    for sig, count in per_sig_raw.items():
        print(f"RAW SEARCH MATCHES {sig}: {count}")
    print(f"UNIQUE ISSUES: {len(by_number)}")
    print(f"RAW ARTIFACT: {out_path.name}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    main()
