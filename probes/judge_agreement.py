"""Judge-human agreement: Cohen's kappa per dimension, 3-bin collapse, bootstrap CI.

Raw agreement is never reported (kappa deflation of 30-40 points is documented
in the LLM-judge literature); n=30 on a 5-point scale is too sparse, so scores
collapse to LOW (1-2) / MID (3) / HIGH (4-5). Only rows where BOTH judge and
human scored (non-degraded LLM drafts with filled human columns) count.

Run: uv run python -m probes.judge_agreement
"""

from __future__ import annotations

import csv
import random
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET = REPO_ROOT / "artifacts" / "spot_check_sampling_sheet.csv"
DIMENSIONS = ("grounding", "cause_plausibility", "actionability")


def bin3(score: int) -> str:
    return "LOW" if score <= 2 else ("MID" if score == 3 else "HIGH")


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    a_counts = Counter(a for a, _ in pairs)
    b_counts = Counter(b for _, b in pairs)
    expected = sum(a_counts[c] * b_counts[c] for c in set(a_counts) | set(b_counts)) / (n * n)
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def bootstrap_ci(pairs: list[tuple[str, str]], iterations: int = 2000) -> tuple[float, float]:
    rng = random.Random(42)  # deterministic: this is an analysis, not a simulation
    stats = sorted(
        cohens_kappa([pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))])
        for _ in range(iterations)
    )
    return round(stats[int(0.025 * iterations)], 3), round(stats[int(0.975 * iterations)], 3)


def main() -> None:
    with SHEET.open() as fh:
        rows = list(csv.DictReader(fh))

    for dim in DIMENSIONS:
        pairs = []
        for row in rows:
            judge_raw, human_raw = row.get(f"judge_{dim}"), row.get(f"human_{dim}")
            if row.get("draft_source") == "llm" and judge_raw and human_raw:
                pairs.append((bin3(int(judge_raw)), bin3(int(human_raw))))
        if len(pairs) < 10:
            print(f"{dim}: only {len(pairs)} scored pairs; need >= 10 for a usable kappa")
            continue
        kappa = cohens_kappa(pairs)
        low, high = bootstrap_ci(pairs)
        raw_agreement = sum(1 for a, b in pairs if a == b) / len(pairs)
        print(
            f"{dim}: kappa={kappa:.3f} (95% CI [{low}, {high}]), n={len(pairs)},"
            f" raw agreement {raw_agreement:.0%} (reported only to show deflation)"
        )
    print("Production bar from the literature: kappa > 0.6 acceptable, > 0.8 strong.")


if __name__ == "__main__":
    main()
