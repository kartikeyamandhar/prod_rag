"""Snapshot pull of kubernetes/kubernetes closed kind/bug issues for the chosen SIGs.

Produces artifacts/tickets_raw_<date>.jsonl, one issue per line, enriched with the
closing PR found on the issue timeline. The search filter `linked:pr` implements the
closing-PR half of CLAUDE.md's resolution-linked definition; the accepted-answer half
has no API-native marker and is deliberately out of scope (flagged in PHASES.md).

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


def closing_pr(timeline_events: list[dict]) -> tuple[int | None, str | None]:
    """Best-effort closing PR from the issue timeline.

    Only UPSTREAM cross-references count: fork PRs (downstream backports like
    openshift/kubernetes) cross-reference these issues constantly and would corrupt
    the fix ground truth. Among upstream PR cross-references, the last closed one
    wins; if none is closed, the last one seen.
    """
    last_any: tuple[int | None, str | None] = (None, None)
    last_closed: tuple[int | None, str | None] = (None, None)
    for event in timeline_events:
        if event.get("event") != "cross-referenced":
            continue
        source = event.get("source", {}).get("issue", {})
        if "pull_request" not in source:
            continue
        if source.get("repository", {}).get("full_name") != REPO:
            continue
        ref = (source.get("number"), source.get("html_url"))
        last_any = ref
        if source.get("state") == "closed":
            last_closed = ref
    return last_closed if last_closed[0] is not None else last_any


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
    for number, record in by_number.items():
        pr_number, pr_url = closing_pr(client.issue_timeline(REPO, number))
        record["closing_pr_number"] = pr_number
        record["closing_pr_url"] = pr_url
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
