"""Small, dependency-free statistics used for credible comparisons.

* ``wilson_interval`` — confidence interval for a pass rate (well-behaved at small n and near 0/1).
* ``mcnemar_exact`` — paired test for two systems run on the *same* cases.
* ``paired_bootstrap_delta`` — CI for the difference in pass rate between paired runs.
* ``cohen_kappa`` — judge-vs-human agreement beyond chance.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (same convention as numpy's default). ``q`` in [0, 100]."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return float(data[0])
    pos = (len(data) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return float(data[lo])
    return float(data[lo] + (data[hi] - data[lo]) * (pos - lo))


def mean(values: Sequence[float]) -> float | None:
    data = [v for v in values if v is not None]
    return sum(data) / len(data) if data else None


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value.

    ``b`` = cases system A passed and B failed; ``c`` = cases A failed and B passed.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def paired_bootstrap_delta(
    baseline: Sequence[float],
    candidate: Sequence[float],
    iterations: int = 2000,
    seed: int = 0,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Mean(candidate - baseline) with a percentile bootstrap CI over paired cases."""
    if len(baseline) != len(candidate):
        raise ValueError("paired samples must have equal length")
    n = len(baseline)
    if n == 0:
        return 0.0, 0.0, 0.0
    diffs = [c - b for b, c in zip(baseline, candidate, strict=True)]
    point = sum(diffs) / n
    rng = random.Random(seed)
    boots = []
    for _ in range(iterations):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        boots.append(s / n)
    alpha = (1 - confidence) / 2 * 100
    return point, percentile(boots, alpha) or 0.0, percentile(boots, 100 - alpha) or 0.0


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("label sequences must be non-empty and equal length")
    labels = sorted(set(a) | set(b))
    n = len(a)
    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    expected = sum((list(a).count(lbl) / n) * (list(b).count(lbl) / n) for lbl in labels)
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def relative_change(base: float | None, new: float | None) -> float | None:
    if base is None or new is None:
        return None
    if base == 0:
        return 0.0 if new == 0 else math.inf
    return (new - base) / abs(base)
