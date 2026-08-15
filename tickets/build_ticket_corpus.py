"""Build the ticket corpus artifact from the raw snapshot jsonl.

Applies the deterministic corpus rules (tenant, held-out, image detection), embeds
TRAIN tickets only (held-out tickets stay NULL so they can never retrieve), writes
the parquet artifact, and prints the Phase 1b gate evidence.

Run: uv run python -m tickets.build_ticket_corpus artifacts/tickets_raw_<date>.jsonl
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from fastembed import TextEmbedding

from ingest.manifest import load_manifest
from tickets.corpus_rules import has_images, is_held_out, make_embed_text, tenant_of
from tickets.pull_tickets import SIGS

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = REPO_ROOT / "artifacts"
MIN_HELD_OUT = 15


def main(raw_path: Path) -> None:
    manifest = load_manifest(REPO_ROOT / "corpus_manifest.toml")
    docs = manifest.docs_corpus
    snapshot_date = raw_path.stem.removeprefix("tickets_raw_")

    records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    logger.info("loaded %d raw tickets from %s", len(records), raw_path.name)

    for record in records:
        record["tenant_id"] = tenant_of(record["number"])
        record["is_held_out"] = is_held_out(record["number"])
        record["has_images"] = has_images(record["body"])
        record["embed_text"] = make_embed_text(record["title"], record["body"])

    train = [r for r in records if not r["is_held_out"]]
    held_out = [r for r in records if r["is_held_out"]]
    if len(held_out) < MIN_HELD_OUT:
        raise SystemExit(
            f"held-out slice has {len(held_out)} tickets, below the floor of {MIN_HELD_OUT}"
        )

    model = TextEmbedding(model_name=docs.embed_model)
    vectors = np.array(
        list(model.embed([r["embed_text"] for r in train], batch_size=64)), dtype=np.float32
    )
    if vectors.shape != (len(train), docs.embed_dim):
        raise RuntimeError(f"embedding shape {vectors.shape} != ({len(train)}, {docs.embed_dim})")
    train_vectors = {r["number"]: vectors[i].tolist() for i, r in enumerate(train)}

    table = pa.table(
        {
            "number": [r["number"] for r in records],
            "title": [r["title"] for r in records],
            "body": [r["body"] for r in records],
            "labels": [r["labels"] for r in records],
            "sigs": [r["sigs"] for r in records],
            "created_at": [r["created_at"] for r in records],
            "closed_at": [r["closed_at"] for r in records],
            "url": [r["url"] for r in records],
            "closing_pr_number": [r["closing_pr_number"] for r in records],
            "closing_pr_url": [r["closing_pr_url"] for r in records],
            "tenant_id": [r["tenant_id"] for r in records],
            "is_held_out": [r["is_held_out"] for r in records],
            "has_images": [r["has_images"] for r in records],
            "snapshot_date": [snapshot_date] * len(records),
            "embed_text": [r["embed_text"] for r in records],
            "embedding": pa.array(
                [train_vectors.get(r["number"]) for r in records],
                type=pa.list_(pa.float32(), docs.embed_dim),
            ),
        }
    )
    out_path = ARTIFACTS / f"tickets_{snapshot_date}.parquet"
    pq.write_table(table, out_path)

    sig_counts = Counter(sig for r in records for sig in r["sigs"])
    tenant_counts = Counter(r["tenant_id"] for r in records)
    per_tenant = [tenant_counts.get(t, 0) for t in range(50)]
    diagram = [r for r in records if r["has_images"]]
    linked = sum(1 for r in records if r["closing_pr_number"] is not None)

    print(f"ARTIFACT: {out_path.name} rows={table.num_rows}")
    print(f"SNAPSHOT DATE: {snapshot_date}")
    for sig in SIGS:
        print(f"TICKETS {sig}: {sig_counts.get(sig, 0)}")
    print(f"SPLIT train={len(train)} held_out={len(held_out)}")
    print(
        f"TENANTS n={len(tenant_counts)} min={min(per_tenant)} max={max(per_tenant)} "
        f"mean={statistics.mean(per_tenant):.1f}"
    )
    print(f"CLOSING-PR LINKED: {linked} of {len(records)}")
    print(f"DIAGRAM-DEPENDENT TICKETS: {len(diagram)} of {len(records)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m tickets.build_ticket_corpus <tickets_raw_*.jsonl>")
    main(Path(sys.argv[1]))
