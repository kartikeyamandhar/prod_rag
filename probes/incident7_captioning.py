"""Incident 7 v2: image-blind baseline vs captioning, four arms, rank metrics.

v1 was quadruple-invalid (audit B7/A14): top-8 hit-rate had a ~85% random
baseline and a 1-ticket delta; two variables moved at once; the URL extractor
missed 13/32 image tickets that has_images counts; captions could evict the
body from the embed window; transient errors were cached forever; and the
mutated corpus leaked into later measurements. v2:

- FOUR arms: baseline / query-captions-only / corpus-captions-only / both,
  so each variable moves alone. Corpus arms are also measured with the tenant
  filter off (the filter blinds queries to most corpus rows; disclosed).
- Metrics: MRR@8 and median rank of the first same-SIG ticket and the first
  docs-domain page (rank-sensitive, no 85%-ceiling), plus top-8 hit counts
  with exact McNemar vs baseline for the honest binary read.
- URL extraction covers everything has_images matches (markdown images,
  <img src>, bare attachment URLs); effective n disclosed at each stage.
- Corpus captions use the budgeted partition (make_captioned_embed_text):
  body eviction is structurally impossible. Updated rows set caption_applied.
- Cache is written per-image immediately after each paid call; transient
  download/LLM failures are never cached (only permanent format/size and
  HTTP 4xx verdicts are).
- Restore: make reset-corpus (the loader reload clears caption_applied rows).

Run: uv run --env-file .env python -m probes.incident7_captioning
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from pathlib import Path

import httpx
import psycopg

from api.llm import BedrockLLM, LLMUnavailable
from probes.replay_harness import SIG_HOME_DIR
from probes.run_meta import run_meta
from retrieval.embedder import embed_query, get_query_embedder
from retrieval.search import hybrid_search
from tickets.corpus_rules import make_captioned_embed_text

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "artifacts" / "caption_cache.json"

# Everything has_images can match: markdown image, <img src>, bare attachment URLs.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")
_IMG_TAG = re.compile(r"<img\b[^>]*?src=[\"'](https?://[^\"']+)[\"']", re.IGNORECASE)
_BARE_URL = re.compile(
    r"https://(?:user-images\.githubusercontent\.com|github\.com/user-attachments/assets)"
    r"[^\s)\"'<>\]]+"
)
FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif", "image/webp": "webp"}
MAX_BYTES = 3_500_000
MAX_IMAGES_PER_TICKET = 3

CAPTION_SYSTEM = (
    "You caption screenshots and diagrams from Kubernetes bug reports so they can be"
    " indexed for search. Be concrete: name the components, resource kinds, error"
    " messages, and states visible. 2-3 sentences, no preamble."
)


def image_urls(body: str) -> list[str]:
    seen: list[str] = []
    for pattern in (_MD_IMAGE, _IMG_TAG, _BARE_URL):
        for url in pattern.findall(body):
            if url not in seen:
                seen.append(url)
    return seen[:MAX_IMAGES_PER_TICKET]


def caption_images(llm: BedrockLLM, tickets: list[dict]) -> tuple[dict, dict]:
    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    stats = {"captioned": 0, "cached_hits": 0, "permanent_fail": 0, "transient_fail": 0}
    with httpx.Client(timeout=15.0, follow_redirects=True) as web:
        for ticket in tickets:
            for url in image_urls(ticket["body"]):
                if url in cache:
                    stats["cached_hits"] += 1
                    continue
                try:
                    response = web.get(url)
                except httpx.HTTPError:
                    stats["transient_fail"] += 1  # never cached: retryable next run
                    continue
                if 400 <= response.status_code < 500:
                    cache[url] = {"error": f"http-{response.status_code}"}
                    stats["permanent_fail"] += 1
                    CACHE_PATH.write_text(json.dumps(cache, indent=1) + "\n")
                    continue
                if response.status_code != 200:
                    stats["transient_fail"] += 1
                    continue
                fmt = FORMATS.get(response.headers.get("content-type", "").split(";")[0])
                if fmt is None or len(response.content) > MAX_BYTES:
                    cache[url] = {"error": "unsupported-format-or-size"}
                    stats["permanent_fail"] += 1
                    CACHE_PATH.write_text(json.dumps(cache, indent=1) + "\n")
                    continue
                try:
                    caption = llm.converse(
                        CAPTION_SYSTEM,
                        "Caption this image from a Kubernetes bug report.",
                        max_tokens=150,
                        images=[{"format": fmt, "bytes": response.content}],
                    )
                except LLMUnavailable:
                    stats["transient_fail"] += 1  # never cached
                    continue
                cache[url] = {"caption": caption.strip(), "ticket": ticket["number"]}
                stats["captioned"] += 1
                CACHE_PATH.write_text(json.dumps(cache, indent=1) + "\n")  # per-image, paid
    logger.warning("caption pass: %s", stats)
    return cache, stats


def captions_for(ticket: dict, cache: dict) -> str:
    parts = [
        cache[u]["caption"]
        for u in image_urls(ticket["body"])
        if u in cache and "caption" in cache[u]
    ]
    return " ".join(parts)


def _first_rank(items: list, predicate) -> int | None:
    for rank, item in enumerate(items, start=1):
        if predicate(item):
            return rank
    return None


def measure(conn, embedder, tickets: list[dict], ticket_sigs: dict, cache, filter_on) -> dict:
    """One arm. cache=None -> image-blind queries; else captions augment qtext."""
    per_ticket = []
    for ticket in tickets:
        qtext = ticket["title"]
        if cache is not None:
            caps = captions_for(ticket, cache)
            if caps:
                qtext = f"{ticket['title']} {caps}"
        qvec = embed_query(embedder, qtext)
        items = [
            item
            for item in hybrid_search(
                conn, qvec, qtext, ticket["tenant_id"], tenant_filter_enabled=filter_on
            )
            if item.key != f"tickets:{ticket['number']}"
        ]
        sig_set = set(ticket["sigs"])
        sig_rank = _first_rank(
            items,
            lambda i, want=sig_set: (
                i.corpus == "tickets" and set(ticket_sigs.get(int(i.key.split(":")[1]), [])) & want
            ),
        )
        docs_rank = _first_rank(
            items,
            lambda i, want=sig_set: (
                i.corpus == "docs"
                and any(SIG_HOME_DIR[s] in (i.url or "") for s in want if s in SIG_HOME_DIR)
            ),
        )
        per_ticket.append(
            {"ticket": ticket["number"], "sig_rank": sig_rank, "docs_rank": docs_rank}
        )

    n = len(per_ticket)
    sig_ranks = [r["sig_rank"] for r in per_ticket if r["sig_rank"]]
    docs_ranks = [r["docs_rank"] for r in per_ticket if r["docs_rank"]]
    return {
        "n": n,
        "tenant_filter": filter_on,
        "same_sig_hits_top8": len(sig_ranks),
        "same_sig_mrr_at8": round(
            sum(1 / r["sig_rank"] for r in per_ticket if r["sig_rank"]) / n, 4
        ),
        "same_sig_median_rank": sorted(sig_ranks)[len(sig_ranks) // 2] if sig_ranks else None,
        "docs_domain_hits_top8": len(docs_ranks),
        "docs_domain_mrr_at8": round(
            sum(1 / r["docs_rank"] for r in per_ticket if r["docs_rank"]) / n, 4
        ),
        "per_ticket": per_ticket,
    }


def mcnemar_exact(baseline: dict, arm: dict, key: str) -> dict:
    """Exact two-sided McNemar on top-8 hit/miss vs baseline (b/c = discordant)."""
    base_hits = {r["ticket"]: r[key] is not None for r in baseline["per_ticket"]}
    arm_hits = {r["ticket"]: r[key] is not None for r in arm["per_ticket"]}
    b = sum(1 for t in base_hits if base_hits[t] and not arm_hits[t])
    c = sum(1 for t in base_hits if not base_hits[t] and arm_hits[t])
    if b + c == 0:
        return {"b": 0, "c": 0, "p": 1.0}
    p = min(
        1.0,
        2 * sum(math.comb(b + c, k) for k in range(min(b, c) + 1)) * 0.5 ** (b + c),
    )
    return {"b": b, "c": c, "p": round(p, 4)}


def apply_captions(conn, embedder, tickets: list[dict], cache: dict) -> int:
    conn.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS caption_applied BOOLEAN DEFAULT FALSE"
    )
    updated = 0
    with conn.cursor() as cur:
        for ticket in tickets:
            if ticket["is_held_out"]:
                continue  # held-out rows stay NULL-embedded; they are incoming, not corpus
            caps = captions_for(ticket, cache)
            if not caps:
                continue
            new_text = make_captioned_embed_text(ticket["title"], ticket["body"], caps)
            vector = embed_query(
                embedder, new_text
            )  # identical to passage embed: no prefix anywhere
            cur.execute(
                "UPDATE tickets SET embed_text = %s, embedding = %s, caption_applied = TRUE"
                " WHERE number = %s",
                (new_text, vector, ticket["number"]),
            )
            updated += 1
    conn.commit()
    return updated


def main() -> None:
    llm = BedrockLLM()
    embedder = get_query_embedder(os.environ["EMBED_MODEL_NAME"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT number, title, body, tenant_id, sigs, is_held_out FROM tickets"
                " WHERE has_images ORDER BY number"
            )
            cols = [c.name for c in cur.description]
            tickets = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            cur.execute("SELECT number, sigs FROM tickets")
            ticket_sigs = dict(cur.fetchall())

        cache, caption_stats = caption_images(llm, tickets)
        with_urls = [t for t in tickets if image_urls(t["body"])]
        with_captions = [t for t in tickets if captions_for(t, cache)]

        baseline = measure(conn, embedder, tickets, ticket_sigs, cache=None, filter_on=True)
        query_only = measure(conn, embedder, tickets, ticket_sigs, cache=cache, filter_on=True)
        updated = apply_captions(conn, embedder, tickets, cache)
        corpus_only = measure(conn, embedder, tickets, ticket_sigs, cache=None, filter_on=True)
        both = measure(conn, embedder, tickets, ticket_sigs, cache=cache, filter_on=True)
        corpus_only_no_filter = measure(
            conn, embedder, tickets, ticket_sigs, cache=None, filter_on=False
        )
        both_no_filter = measure(conn, embedder, tickets, ticket_sigs, cache=cache, filter_on=False)
        meta = run_meta(conn)

    arms = {
        "baseline_image_blind": baseline,
        "query_captions_only": query_only,
        "corpus_captions_only": corpus_only,
        "both": both,
        "corpus_captions_only_filter_off": corpus_only_no_filter,
        "both_filter_off": both_no_filter,
    }
    report = {
        "incident": 7,
        "effective_n": {
            "has_images_tickets": len(tickets),
            "with_extractable_urls": len(with_urls),
            "with_usable_caption": len(with_captions),
            "note": "query arms only differ from baseline on with_usable_caption tickets",
        },
        "caption_pass": caption_stats,
        "corpus_rows_recaptioned": updated,
        "caption_usage": llm.meter.snapshot(),
        "arms": arms,
        "mcnemar_vs_baseline_same_sig_top8": {
            name: mcnemar_exact(baseline, arm, "sig_rank")
            for name, arm in arms.items()
            if name != "baseline_image_blind" and arm["tenant_filter"]
        },
        "restore": "make reset-corpus",
        "run_meta": meta,
    }
    out = REPO_ROOT / "artifacts" / "incidents" / "incident7_captioning.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    concise = {
        name: {k: v for k, v in arm.items() if k != "per_ticket"} for name, arm in arms.items()
    }
    print(
        json.dumps(
            {
                **{k: report[k] for k in ("effective_n", "caption_pass")},
                "arms": concise,
                "mcnemar": report["mcnemar_vs_baseline_same_sig_top8"],
            },
            indent=1,
        )
    )
    print(f"-> {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
