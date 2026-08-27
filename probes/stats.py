"""Percentiles done correctly (audit B12: int(n*.95)-1 reported ~p87 at n=15).

Linear interpolation between closest ranks (numpy's default method), no
dependency. Small-n honesty: report_percentiles refuses p95 under n=20 and
p99 under n=100 rather than returning a number that is really the max.
"""

from __future__ import annotations


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("percentile of empty list")
    if not 0 <= pct <= 100:
        raise ValueError(f"pct out of range: {pct}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def report_percentiles(values: list[float]) -> dict:
    """p50 always; p95 only when n >= 20; p99 only when n >= 100; max always."""
    report: dict = {"n": len(values), "p50": round(percentile(values, 50), 4)}
    if len(values) >= 20:
        report["p95"] = round(percentile(values, 95), 4)
    else:
        report["p95"] = None
        report["p95_note"] = f"n={len(values)} < 20: p95 would just be the max; see max"
    if len(values) >= 100:
        report["p99"] = round(percentile(values, 99), 4)
    report["max"] = round(max(values), 4)
    return report
