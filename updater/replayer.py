"""KB replayer: applies kubernetes/website commits after the pinned SHA, in order.

One tick = one upstream commit touching the concepts subtree, applied atomically:
changed pages re-chunked and re-embedded, deleted pages removed, renames recorded,
one ledger row per page change, replay_state advanced, all in a single transaction.
The working checkout never moves off the pinned SHA; file content at each commit
is read via git show. The ticket corpus is a fixed snapshot and is never touched.

Run: uv run --env-file .env python -m updater.replayer --ticks 5
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass

import numpy as np
import psycopg
from fastembed import TextEmbedding
from git import Repo
from pgvector.psycopg import register_vector

from ingest.chunker import chunk_page
from ingest.clone_docs import CORPUS_DIR, SPARSE_PATH
from retrieval.embedder import get_query_embedder

logger = logging.getLogger(__name__)

SCHEMA = (CORPUS_DIR.parent.parent / "updater" / "schema.sql").read_text(encoding="utf-8")


@dataclass
class FileChange:
    change_type: str  # added | modified | deleted | renamed
    path: str
    old_path: str | None = None


def parse_name_status(diff_text: str, subtree: str = SPARSE_PATH) -> list[FileChange]:
    """Parse `git diff --name-status -M` output, keeping subtree markdown only."""
    changes: list[FileChange] = []
    for line in diff_text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) == 3:
            old, new = parts[1], parts[2]
            if new.startswith(subtree) and new.endswith(".md"):
                changes.append(FileChange("renamed", new, old))
            elif old.startswith(subtree) and old.endswith(".md"):
                changes.append(FileChange("deleted", old))
        elif status in ("A", "M", "D"):
            path = parts[1]
            if not (path.startswith(subtree) and path.endswith(".md")):
                continue
            kind = {"A": "added", "M": "modified", "D": "deleted"}[status]
            changes.append(FileChange(kind, path))
    return changes


def _chunk_count(cur: psycopg.Cursor, path: str) -> int:
    cur.execute(
        "SELECT count(*) FROM chunks c JOIN pages p ON p.id = c.page_id WHERE p.path = %s",
        (path,),
    )
    row = cur.fetchone()
    return row[0] if row else 0


def _delete_page(cur: psycopg.Cursor, path: str) -> int:
    before = _chunk_count(cur, path)
    cur.execute("DELETE FROM pages WHERE path = %s", (path,))
    return before


def _upsert_page(
    cur: psycopg.Cursor, repo: Repo, model: TextEmbedding, sha: str, path: str
) -> tuple[int, int]:
    before = _chunk_count(cur, path)
    raw = repo.git.show(f"{sha}:{path}")
    page = chunk_page(path, raw)
    texts = [chunk.text for chunk in page.chunks]
    vectors = np.array(list(model.embed(texts, batch_size=64)), dtype=np.float32) if texts else None
    if vectors is not None and vectors.shape != (len(texts), 384):
        raise RuntimeError(f"embedding shape {vectors.shape} for {path}")

    cur.execute(
        "INSERT INTO pages (path, title, corpus_sha, has_images, n_chunks)"
        " VALUES (%s, %s, %s, %s, %s)"
        " ON CONFLICT (path) DO UPDATE SET title = EXCLUDED.title,"
        " corpus_sha = EXCLUDED.corpus_sha, has_images = EXCLUDED.has_images,"
        " n_chunks = EXCLUDED.n_chunks RETURNING id",
        (path, page.title, sha, bool(page.image_refs), len(page.chunks)),
    )
    fetched = cur.fetchone()
    assert fetched is not None
    page_id = fetched[0]
    cur.execute("DELETE FROM chunks WHERE page_id = %s", (page_id,))
    if texts:
        cur.executemany(
            "INSERT INTO chunks (page_id, chunk_index, section, text, n_chars, embedding)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (page_id, c.index, c.section, c.text, len(c.text), vectors[i].tolist())
                for i, c in enumerate(page.chunks)
            ],
        )
    return before, len(page.chunks)


def apply_commit(conn: psycopg.Connection, repo: Repo, model: TextEmbedding, sha: str) -> int:
    """Apply one upstream commit atomically. Returns number of page changes."""
    commit = repo.commit(sha)
    diff_text = repo.git.diff("--name-status", "-M", f"{sha}^", sha, "--", SPARSE_PATH)
    changes = parse_name_status(diff_text)
    with conn.transaction():
        with conn.cursor() as cur:
            for change in changes:
                if change.change_type == "deleted":
                    before = _delete_page(cur, change.path)
                    after = 0
                elif change.change_type == "renamed":
                    assert change.old_path is not None
                    # chunks_before = chunks under the OLD path (audit A12: the
                    # delete count was discarded and renames logged before=0).
                    before = _delete_page(cur, change.old_path)
                    _, after = _upsert_page(cur, repo, model, sha, change.path)
                else:
                    before, after = _upsert_page(cur, repo, model, sha, change.path)
                cur.execute(
                    "INSERT INTO replay_ledger (commit_sha, commit_date, page_path,"
                    " change_type, old_path, chunks_before, chunks_after)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        sha,
                        commit.committed_datetime,
                        change.path,
                        change.change_type,
                        change.old_path,
                        before,
                        after,
                    ),
                )
                logger.info(
                    "applied page change",
                    extra={"sha": sha[:7], "path": change.path, "type": change.change_type},
                )
            cur.execute(
                "UPDATE replay_state SET current_sha = %s, updated_at = now() WHERE id = 1",
                (sha,),
            )
    return len(changes)


def load_state(conn: psycopg.Connection, start_sha: str) -> str:
    """Seed-or-read replay state, leaving the connection OUTSIDE a transaction.
    The final commit ends the implicit transaction the SELECT opened; without
    it, apply_commit's conn.transaction() degrades to a SAVEPOINT inside one
    giant transaction and a crash on tick N rolls back ticks 1..N-1 (audit
    A11; observed live: 143 applied commits invisible to other connections
    until the process exited)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO replay_state (id, current_sha) VALUES (1, %s)"
            " ON CONFLICT (id) DO NOTHING",
            (start_sha,),
        )
        conn.commit()
        cur.execute("SELECT current_sha FROM replay_state WHERE id = 1")
        row = cur.fetchone()
        assert row is not None
        current = row[0]
    conn.commit()
    return current


def tick(database_url: str, model_name: str, start_sha: str, n_ticks: int) -> None:
    repo = Repo(CORPUS_DIR)
    model = get_query_embedder(model_name)
    with psycopg.connect(database_url) as conn:
        conn.execute(SCHEMA)
        conn.commit()
        register_vector(conn)
        current = load_state(conn, start_sha)

        pending = repo.git.rev_list(
            "--reverse", f"{current}..origin/main", "--", SPARSE_PATH
        ).splitlines()
        print(f"REPLAY STATE: {current[:9]}; pending commits touching subtree: {len(pending)}")
        for sha in pending[:n_ticks]:
            n_changes = apply_commit(conn, repo, model, sha)
            date = repo.commit(sha).committed_datetime.isoformat()
            print(f"APPLIED {sha[:9]} {date} page_changes={n_changes}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=1)
    args = parser.parse_args()
    manifest_sha = os.environ.get("DOCS_START_SHA", "")
    if not manifest_sha:
        from ingest.manifest import load_manifest

        manifest_sha = load_manifest(
            CORPUS_DIR.parent.parent / "corpus_manifest.toml"
        ).docs_corpus.start_sha
    tick(
        os.environ["DATABASE_URL"],
        os.environ["EMBED_MODEL_NAME"],
        manifest_sha,
        args.ticks,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    main()
