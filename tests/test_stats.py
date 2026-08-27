"""probes.stats: correct percentile math (audit B12 regression tests)."""

from __future__ import annotations

import pytest

from probes.stats import percentile, report_percentiles


def test_median_interpolates() -> None:
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3], 50) == 2


def test_p95_at_n15_is_not_p87() -> None:
    # v1's int(n*.95)-1 on n=15 returned index 13 (~p87); correct p95 of
    # 1..15 is 14.3 (between the 14th and 15th values).
    values = list(range(1, 16))
    assert percentile(values, 95) == pytest.approx(14.3)


def test_extremes_and_bounds() -> None:
    assert percentile([7.0], 95) == 7.0
    assert percentile([1, 2, 3], 0) == 1
    assert percentile([1, 2, 3], 100) == 3
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_report_refuses_small_n_p95() -> None:
    report = report_percentiles([float(v) for v in range(15)])
    assert report["p95"] is None and "p95_note" in report
    report20 = report_percentiles([float(v) for v in range(20)])
    assert report20["p95"] is not None
    assert "p99" not in report20  # n < 100
