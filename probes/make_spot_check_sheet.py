"""Deterministic 30-ticket sampling sheet for the human spot-check of the judge.

CLAUDE.md: no judged number is published before a 30-ticket human spot-check of
judge agreement. Sample is a stable hash order over held-out tickets, so the
sheet is reproducible from a clean clone.

Run: uv run --env-file .env python -m probes.make_spot_check_sheet
"""

from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_SIZE = 30
COLUMNS = [
    "ticket_number",
    "url",
    "tenant_id",
    "truth_sigs",
    "judge_grounding",
    "judge_cause_plausibility",
    "judge_actionability",
    "human_grounding",
    "human_cause_plausibility",
    "human_actionability",
    "human_agrees_with_judge",
    "notes",
]


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT number, url, tenant_id, sigs FROM tickets WHERE is_held_out")
        rows = cur.fetchall()
    sample = sorted(rows, key=lambda r: hashlib.sha256(f"spot:{r[0]}".encode()).hexdigest())[
        :SAMPLE_SIZE
    ]
    out = REPO_ROOT / "artifacts" / "spot_check_sampling_sheet.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for number, url, tenant_id, sigs in sorted(sample):
            writer.writerow([number, url, tenant_id, "+".join(sigs), *[""] * 8])
    print(f"SAMPLED {len(sample)} of {len(rows)} held-out tickets -> {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
