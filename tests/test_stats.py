import math

import pytest

from agentbench.metrics.stats import (
    cohen_kappa,
    mcnemar_exact,
    paired_bootstrap_delta,
    percentile,
    relative_change,
    wilson_interval,
)


def test_percentile_matches_linear_interpolation():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(data, 50) == pytest.approx(5.5)
    assert percentile(data, 95) == pytest.approx(9.55)
    assert percentile(data, 0) == 1
    assert percentile(data, 100) == 10
    assert percentile([], 50) is None
    assert percentile([7.0], 99) == 7.0


def test_wilson_interval_is_sane_at_the_edges():
    lo, hi = wilson_interval(0, 20)
    assert lo == 0 and 0.1 < hi < 0.2
    lo, hi = wilson_interval(20, 20)
    assert hi == 1 and 0.8 < lo < 0.9
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0
    # 0 vs 10 discordant pairs: p = 2 * 0.5^10
    assert mcnemar_exact(0, 10) == pytest.approx(2 / 1024)
    assert mcnemar_exact(10, 0) == mcnemar_exact(0, 10)
    assert mcnemar_exact(3, 9) > 0.05  # not significant


def test_paired_bootstrap_delta_brackets_the_point_estimate():
    base = [1.0] * 80 + [0.0] * 20
    cand = [1.0] * 70 + [0.0] * 30
    point, lo, hi = paired_bootstrap_delta(base, cand, iterations=500, seed=1)
    assert point == pytest.approx(-0.10)
    assert lo <= point <= hi
    with pytest.raises(ValueError):
        paired_bootstrap_delta([1.0], [1.0, 0.0])


def test_cohen_kappa():
    assert cohen_kappa(["pass", "fail", "pass"], ["pass", "fail", "pass"]) == 1.0
    k = cohen_kappa(["pass", "pass", "fail", "fail"], ["pass", "fail", "pass", "fail"])
    assert k == pytest.approx(0.0)


def test_relative_change():
    assert relative_change(100, 125) == pytest.approx(0.25)
    assert relative_change(0, 0) == 0
    assert math.isinf(relative_change(0, 5))
    assert relative_change(None, 5) is None
