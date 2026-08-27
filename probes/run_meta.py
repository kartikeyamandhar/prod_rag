"""Provenance stamp embedded in every measurement artifact (audit B5/B8 fix).

Two incidents were confounded by unrecorded state: incident 1's after-arm ran
under an env nobody recorded, and the spot-check sheet was judged against a
corpus incident 7 had mutated. run_meta() makes both classes impossible to
miss again: git SHA, the env vars that change behavior, a DB fingerprint, and
a monotonically increasing run ordinal all travel inside the artifact.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
ORDINAL_PATH = REPO_ROOT / "artifacts" / ".run_ordinal"

# Every env var that changes pipeline behavior; absent means the code default.
BEHAVIOR_ENV = (
    "LLM_MAX_CONCURRENCY",
    "LLM_ACQUIRE_TIMEOUT_S",
    "DEGRADE_DISABLED",
    "FAULT_INJECT_THROTTLE",
    "PROBE_PACING_S",
    "EMBED_MODEL_NAME",
    "BEDROCK_MODEL_ID",
)


def db_fingerprint(conn: psycopg.Connection) -> dict:
    """Row counts + content hash per corpus: cheap, catches mutation and drift."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), coalesce(md5(string_agg(md5(text), '' ORDER BY id)), '') FROM chunks"
        )
        chunk_count, chunk_hash = cur.fetchone()  # type: ignore[misc]
        cur.execute(
            "SELECT count(*), coalesce(md5(string_agg(md5(coalesce(embed_text, '')), ''"
            " ORDER BY number)), '') FROM tickets"
        )
        ticket_count, ticket_hash = cur.fetchone()  # type: ignore[misc]
    return {
        "chunks": chunk_count,
        "chunks_md5": chunk_hash[:12],
        "tickets": ticket_count,
        "tickets_md5": ticket_hash[:12],
    }


def _next_ordinal() -> int:
    current = int(ORDINAL_PATH.read_text()) if ORDINAL_PATH.exists() else 0
    ORDINAL_PATH.write_text(str(current + 1) + "\n")
    return current + 1


def run_meta(conn: psycopg.Connection | None = None) -> dict:
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    meta = {
        "git_sha": git_sha,
        "git_dirty": dirty,
        "env": {name: os.environ.get(name) for name in BEHAVIOR_ENV},
        "run_ordinal": _next_ordinal(),
    }
    if conn is not None:
        meta["db"] = db_fingerprint(conn)
    return meta
