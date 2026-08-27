"""Reset both corpora to the pinned bootstrap state between incident runs.

Audit B8/A13: incident 7 mutated 16 corpus rows that then sat under the judged
spot-check, and incident 3 deleted a page that stayed deleted for two more
incidents. Every incident now starts from a verified-identical DB: reload docs
and tickets from the pinned parquet artifacts (the ticket loader re-hydrates
PR ground truth from the committed cache), clear the replay ledger and state,
then assert the fingerprint matches the recorded baseline. First run records
the baseline; drift on any later run is a hard failure.

Run: uv run --env-file .env python -m probes.reset_corpus
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import psycopg

from ingest import load_docs
from probes.run_meta import db_fingerprint
from tickets import load_tickets

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "artifacts" / "db_fingerprint_baseline.json"
TICKETS_PARQUET = REPO_ROOT / "artifacts" / "tickets_2026-08-19.parquet"


def main() -> None:
    load_docs.main()
    load_tickets.main(TICKETS_PARQUET)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE replay_ledger")
            cur.execute("DELETE FROM replay_state")
        conn.commit()
        fingerprint = db_fingerprint(conn)

    print(f"FINGERPRINT: {fingerprint}")
    if BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text())
        if fingerprint != baseline:
            print(f"BASELINE:    {baseline}")
            print("RESET FAILED: corpus fingerprint drifted from the recorded baseline")
            sys.exit(1)
        print("RESET OK: fingerprint matches recorded baseline")
    else:
        BASELINE_PATH.write_text(json.dumps(fingerprint, indent=1) + "\n")
        print(f"BASELINE RECORDED -> {BASELINE_PATH.relative_to(REPO_ROOT)} (commit this)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
