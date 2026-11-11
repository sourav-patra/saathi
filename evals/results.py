"""Result types shared by suites and the report."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

State = Literal["verified", "invalid", "unavailable"]


@dataclass(frozen=True)
class Rate:
    """An event rate (misses, leaks, false alarms): k in n. `max_k` is the gate; None means report only."""

    name: str
    k: int
    n: int
    target_n: int | None = None
    max_k: int | None = None

    @property
    def gate_ok(self) -> bool:
        return self.max_k is None or self.k <= self.max_k


@dataclass
class Results:
    suite: str
    state: State
    rates: list[Rate] = field(default_factory=list)
    abstain: int = 0  # its own class (RQ-59): cases the grader could not decide
    errors: int = 0
    reason: str = ""  # required whenever state is unavailable (RQ-61)
    dataset_sha256: str = ""
    n: int = 0
    author_role: str = ""
    grader: str = "code"
    failures: list[str] = field(default_factory=list)  # case ids only, never content
