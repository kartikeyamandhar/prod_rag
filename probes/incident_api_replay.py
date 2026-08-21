"""Replay held-out tickets against a live API and record outcome distribution.

Used by incidents 1 and 6: reports HTTP status, degraded flag, route, and latency
per ticket plus a summary. Tickets come from the LOCAL database; the API under
test is wherever API_URL points (the box, usually).

Run: API_URL=http://<ip>:8080 uv run --env-file .env \
     python -m probes.incident_api_replay --n 15 --tag incident6_before
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx
import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=15)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    api = os.environ.get("API_URL", "http://127.0.0.1:8080")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT number, title, body, tenant_id FROM tickets WHERE is_held_out"
            " ORDER BY number DESC LIMIT %s",
            (args.n,),
        )
        tickets = cur.fetchall()

    rows = []
    with httpx.Client(timeout=60.0) as client:
        for number, title, body, tenant_id in tickets:
            t0 = time.perf_counter()
            try:
                response = client.post(
                    f"{api}/tickets",
                    json={"title": title, "body": body[:4000], "tenant_id": tenant_id},
                )
                ms = round((time.perf_counter() - t0) * 1000)
                if response.status_code == 200:
                    data = response.json()
                    rows.append(
                        {
                            "ticket": number,
                            "status": 200,
                            "degraded": data["degraded"],
                            "route": data["route"]["route"],
                            "ms": ms,
                        }
                    )
                else:
                    rows.append({"ticket": number, "status": response.status_code, "ms": ms})
            except httpx.HTTPError as exc:
                rows.append(
                    {
                        "ticket": number,
                        "status": "transport-error",
                        "error": type(exc).__name__,
                        "ms": round((time.perf_counter() - t0) * 1000),
                    }
                )

    n = len(rows)
    ok = [r for r in rows if r.get("status") == 200]
    latencies = sorted(r["ms"] for r in rows)
    summary = {
        "tag": args.tag,
        "api": api,
        "n": n,
        "http_200": len(ok),
        "http_5xx_or_error": n - len(ok),
        "degraded": sum(1 for r in ok if r.get("degraded")),
        "routes": {
            route: sum(1 for r in ok if r.get("route") == route)
            for route in ("auto_attach", "escalate", "request_info")
        },
        "latency_ms": {
            "median": latencies[n // 2] if n else None,
            "p95": latencies[max(0, int(n * 0.95) - 1)] if n else None,
            "max": latencies[-1] if n else None,
        },
        "rows": rows,
    }
    out = REPO_ROOT / "artifacts" / "incidents" / f"{args.tag}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
