"""Replay held-out tickets against a live API and record outcome distribution.

Used by incidents 1 and 6. v2 (audit B12): 5xx and transport errors are
separate classes (v1's "10 of 15 got 5xx" was really 10 5xx + 5 connection
drops); latency is reported per outcome class and named for what it measures
(time_to_complete for 200s, time_to_error for failures; the two are different
quantities and are never mixed); percentiles come from probes.stats (v1's
index math reported ~p87 as p95 at n=15); --synthetic-injection labels runs
whose failures were injected by code, not by a real upstream event.

Run: API_URL=http://<ip>:8080 uv run --env-file .env \
     python -m probes.incident_api_replay --n 15 --tag incident6_before \
     --synthetic-injection yes
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx
import psycopg

from probes.run_meta import run_meta
from probes.stats import report_percentiles

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=15)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--synthetic-injection",
        choices=["yes", "no"],
        required=True,
        help="yes = failures in this run are injected by code (FAULT_INJECT_THROTTLE"
        " or scripted), not a real upstream event",
    )
    args = parser.parse_args()
    api = os.environ.get("API_URL", "http://127.0.0.1:8080")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT number, title, body, tenant_id FROM tickets WHERE is_held_out"
            " ORDER BY number DESC LIMIT %s",
            (args.n,),
        )
        tickets = cur.fetchall()
        meta = run_meta(conn)

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
                            "outcome": "completed",
                            "degraded": data["degraded"],
                            "degrade_reasons": data.get("degrade_reasons", []),
                            "route": data["route"]["route"],
                            "time_to_complete_ms": ms,
                        }
                    )
                else:
                    rows.append(
                        {
                            "ticket": number,
                            "outcome": "http_5xx" if response.status_code >= 500 else "http_4xx",
                            "status": response.status_code,
                            "time_to_error_ms": ms,
                        }
                    )
            except httpx.HTTPError as exc:
                rows.append(
                    {
                        "ticket": number,
                        "outcome": "transport_error",
                        "error": type(exc).__name__,
                        "time_to_error_ms": round((time.perf_counter() - t0) * 1000),
                    }
                )

    completed = [r for r in rows if r["outcome"] == "completed"]
    errored = [r for r in rows if r["outcome"] != "completed"]
    summary = {
        "tag": args.tag,
        "api": api,
        "synthetic_injection": args.synthetic_injection == "yes",
        "n": len(rows),
        "outcomes": {
            "completed": len(completed),
            "completed_degraded": sum(1 for r in completed if r["degraded"]),
            "http_5xx": sum(1 for r in rows if r["outcome"] == "http_5xx"),
            "http_4xx": sum(1 for r in rows if r["outcome"] == "http_4xx"),
            "transport_error": sum(1 for r in rows if r["outcome"] == "transport_error"),
        },
        "routes": {
            route: sum(1 for r in completed if r["route"] == route)
            for route in ("auto_attach", "escalate", "request_info")
        },
        "time_to_complete_ms": report_percentiles([r["time_to_complete_ms"] for r in completed])
        if completed
        else None,
        "time_to_error_ms": report_percentiles([r["time_to_error_ms"] for r in errored])
        if errored
        else None,
        "rows": rows,
        "run_meta": meta,
    }
    out = REPO_ROOT / "artifacts" / "incidents" / f"{args.tag}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("rows", "run_meta")}, indent=1))


if __name__ == "__main__":
    main()
