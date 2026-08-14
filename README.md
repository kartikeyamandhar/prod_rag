# rag-incident-lab

A deliberately small support-agent layer for a hypothetical managed-Kubernetes vendor, deployed on real AWS and subjected to seven scripted production failures, each documented as a measured incident: detection metric, root cause, fix, before and after. Customers file failure tickets; the system attaches a drafted first response (probable cause, suggested fix, span citations, clarifying questions) behind a confidence gate that routes attach, escalate, or request-info.

## Disclosures (binding on every published artifact from this repo)

1. The vendor scenario is hypothetical. "A managed-Kubernetes platform company with about 50 customers" does not exist; it is a framing device.
2. All data is public Kubernetes project data: kubernetes/website documentation as the knowledge base, kubernetes/kubernetes GitHub issues as the tickets, with SIG and kind labels as triage ground truth and linked resolutions as fix ground truth.
3. Only proxy metrics are claimed: triage accuracy against real labels, fix-hit-rate against real resolutions, gate calibration, staleness and leakage measures. Business outcomes (first-response time, fix time, deflection rate) are never claimed because no humans are in the loop.

## Relationship to airflow_sec_rag

This project complements airflow_sec_rag rather than repeating it. airflow_sec_rag proves offline correctness: span citations, refusal behavior, and a fail-closed golden gate. rag-incident-lab proves runtime operations: burst load, knowledge-base freshness, deletion, write contention, tenant isolation, and provider failure, measured on a live deployment. Together they cover a RAG-backed system offline and in production.

## Quickstart

```sh
make setup          # uv sync: Python 3.12, pinned dependencies
cp .env.example .env
make db-up          # pgvector Postgres on 127.0.0.1:5433
make test
```

## Status

Phase 0 (scaffolding) executed; campaign plan and gates in [PHASES.md](PHASES.md).

## Attribution

Kubernetes documentation content comes from [kubernetes/website](https://github.com/kubernetes/website), by The Kubernetes Authors, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Any published artifact from this repo that reproduces documentation content, including screenshots showing document text, carries this attribution.

GitHub issue content: every referenced ticket links to its source issue on kubernetes/kubernetes; quotes are minimal; full issues or comment threads are never reproduced; contributor quotes carry the issue link.
