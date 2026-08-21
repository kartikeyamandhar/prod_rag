"""Incident 7: image-blind baseline vs captioning at ingest, measured.

Diagram-dependent tickets carry screenshots the text pipeline cannot see. The fix
captions every ticket image once via Haiku vision (cache committed, never re-paid),
folds captions into embed text on the corpus side, and augments incoming queries
with their own captions. Before/after measured over ALL diagram tickets (n=32,
self excluded from results): same-SIG ticket in top 8, docs-domain page in top 8.

Run: uv run --env-file .env python -m probes.incident7_captioning
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import httpx
import psycopg

from api.llm import BedrockLLM, LLMUnavailable
from probes.replay_harness import SIG_HOME_DIR
from retrieval.embedder import embed_query, get_query_embedder
from tickets.corpus_rules import make_embed_text

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "artifacts" / "caption_cache.json"

URL_PATTERN = re.compile(
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


def caption_images(llm: BedrockLLM, tickets: list[dict]) -> dict:
    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    stats = {"downloaded": 0, "captioned": 0, "cached_hits": 0, "failed": 0}
    with httpx.Client(timeout=15.0, follow_redirects=True) as web:
        for ticket in tickets:
            urls = URL_PATTERN.findall(ticket["body"])[:MAX_IMAGES_PER_TICKET]
            for url in urls:
                if url in cache:
                    stats["cached_hits"] += 1
                    continue
                try:
                    response = web.get(url)
                    response.raise_for_status()
                    fmt = FORMATS.get(response.headers.get("content-type", "").split(";")[0])
                    if fmt is None or len(response.content) > MAX_BYTES:
                        cache[url] = {"error": "unsupported-format-or-size"}
                        stats["failed"] += 1
                        continue
                    stats["downloaded"] += 1
                    caption = llm.converse(
                        CAPTION_SYSTEM,
                        "Caption this image from a Kubernetes bug report.",
                        max_tokens=150,
                        images=[{"format": fmt, "bytes": response.content}],
                    )
                    cache[url] = {"caption": caption.strip(), "ticket": ticket["number"]}
                    stats["captioned"] += 1
                except (httpx.HTTPError, LLMUnavailable) as exc:
                    cache[url] = {"error": f"{type(exc).__name__}"}
                    stats["failed"] += 1
    CACHE_PATH.write_text(json.dumps(cache, indent=1) + "\n")
    logger.warning("caption pass: %s", stats)
    return cache


def captions_for(ticket: dict, cache: dict) -> str:
    urls = URL_PATTERN.findall(ticket["body"])[:MAX_IMAGES_PER_TICKET]
    parts = [cache[u]["caption"] for u in urls if u in cache and "caption" in cache[u]]
    return " ".join(parts)


def measure(conn, embedder, tickets: list[dict], ticket_sigs: dict, cache: dict | None) -> dict:
    from retrieval.search import hybrid_search

    same_sig = docs_domain = 0
    for ticket in tickets:
        qtext = ticket["title"]
        if cache is not None:
            caps = captions_for(ticket, cache)
            if caps:
                qtext = f"{ticket['title']} {caps}"
        qvec = embed_query(embedder, qtext)
        items = [
            item
            for item in hybrid_search(conn, qvec, qtext, ticket["tenant_id"])
            if item.key != f"tickets:{ticket['number']}"
        ]
        if any(
            item.corpus == "tickets"
            and set(ticket_sigs.get(int(item.key.split(":")[1]), [])) & set(ticket["sigs"])
            for item in items
        ):
            same_sig += 1
        if any(
            item.corpus == "docs"
            and any(
                SIG_HOME_DIR[s] in (item.url or "") for s in ticket["sigs"] if s in SIG_HOME_DIR
            )
            for item in items
        ):
            docs_domain += 1
    n = len(tickets)
    return {
        "n": n,
        "same_sig_ticket_in_top8": same_sig,
        "same_sig_rate": round(same_sig / n, 3),
        "docs_domain_in_top8": docs_domain,
        "docs_domain_rate": round(docs_domain / n, 3),
    }


def apply_captions(conn, embedder, tickets: list[dict], cache: dict) -> int:
    updated = 0
    with conn.cursor() as cur:
        for ticket in tickets:
            if ticket["is_held_out"]:
                continue  # held-out rows stay NULL-embedded; they are incoming, not corpus
            caps = captions_for(ticket, cache)
            if not caps:
                continue
            new_text = make_embed_text(f"{ticket['title']}\nImage content: {caps}", ticket["body"])
            vector = embed_query(embedder, new_text)  # same model; prefix harmless for docs-side
            cur.execute(
                "UPDATE tickets SET embed_text = %s, embedding = %s WHERE number = %s",
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

        cache = caption_images(llm, tickets)
        before = measure(conn, embedder, tickets, ticket_sigs, cache=None)
        updated = apply_captions(conn, embedder, tickets, cache)
        after = measure(conn, embedder, tickets, ticket_sigs, cache=cache)

    report = {
        "incident": 7,
        "diagram_tickets": len(tickets),
        "corpus_rows_recaptioned": updated,
        "caption_usage": llm.meter.snapshot(),
        "before_image_blind": before,
        "after_captioning": after,
    }
    out = REPO_ROOT / "artifacts" / "incidents" / "incident7_captioning.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
