"""Prometheus metric families for the serving path (the skipped W3 deliverable).

Five families, all low-cardinality by construction: routes are a closed enum,
stages are three fixed names, degrade reasons are exception CLASS names (never
messages), directions are input/output. The incident dashboards read these.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

TICKETS_TOTAL = Counter(
    "rag_tickets_total",
    "Tickets processed, by gate route and degrade status",
    ["route", "degraded"],
)

STAGE_SECONDS = Histogram(
    "rag_stage_seconds",
    "Pipeline stage latency",
    ["stage"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

DEGRADE_TOTAL = Counter(
    "rag_degrade_total",
    "Stage degradations, by stage and exception class",
    ["stage", "reason"],
)

ADMISSION_REJECTS_TOTAL = Counter(
    "rag_admission_rejects_total",
    "LLM calls rejected by admission control (semaphore acquire timeout)",
)

BEDROCK_TOKENS_TOTAL = Counter(
    "rag_bedrock_tokens_total",
    "Bedrock tokens consumed, by direction",
    ["direction"],
)


def observe_ticket(route: str, degraded: bool, timings_ms: dict[str, float]) -> None:
    TICKETS_TOTAL.labels(route=route, degraded=str(degraded).lower()).inc()
    for stage, ms in timings_ms.items():
        STAGE_SECONDS.labels(stage=stage).observe(ms / 1000)


def observe_degrade(stage: str, exc: BaseException) -> None:
    DEGRADE_TOTAL.labels(stage=stage, reason=type(exc).__name__).inc()


def observe_tokens(usage: dict) -> None:
    BEDROCK_TOKENS_TOTAL.labels(direction="input").inc(usage.get("inputTokens", 0) or 0)
    BEDROCK_TOKENS_TOTAL.labels(direction="output").inc(usage.get("outputTokens", 0) or 0)
