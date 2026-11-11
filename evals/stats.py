"""Interval arithmetic and the rate format (RQ-60): every rate prints k, n, a bound and the n a target needs."""

from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k events in n trials. n must be positive."""
    if n <= 0 or k < 0 or k > n:
        raise ValueError("wilson needs 0 <= k <= n and n > 0")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rule_of_three(n: int) -> float:
    """Upper 95% bound on the rate when k = 0 events were seen in n trials."""
    if n <= 0:
        raise ValueError("rule of three needs n > 0")
    return min(1.0, 3 / n)


def n_needed(bound: float) -> int:
    """Smallest n at which zero events in n trials puts the 95% upper bound at or under `bound`."""
    if not 0 < bound <= 1:
        raise ValueError("bound must be in (0, 1]")
    return math.ceil(3 / bound)


def format_rate(k: int, n: int, target_n: int | None = None) -> str:
    """`k/n`, the bound, and the n a target would need. k counts events (misses, leaks, false alarms)."""
    if n <= 0:
        raise ValueError("a rate with n = 0 is not printed")
    if k == 0:
        bound = f"95% upper bound {rule_of_three(n):.3f} (3/n)"
    else:
        lo, hi = wilson(k, n)
        bound = f"95% interval [{lo:.3f}, {hi:.3f}] (Wilson)"
    need = "n needed: target not set" if target_n is None else f"n needed: {target_n} (have {n})"
    return f"{k}/{n} = {k / n:.3f}, {bound}, {need}"
