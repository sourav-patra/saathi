"""Suites whose code under test belongs to a later work unit: registered, unavailable with a reason."""

from __future__ import annotations

from pathlib import Path

from evals.dataset import Dataset, EvalError
from evals.results import Results


class PendingSuite:
    """Never loads or passes. `unavailable` carries the registry reason (RQ-61: a skip needs a named reason)."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self._reason = reason

    def load(self, root: Path) -> Dataset:
        raise EvalError(f"suite {self.name} has no dataset yet")

    def run(self, ds: Dataset) -> Results:  # pragma: no cover - never reached
        raise EvalError(f"suite {self.name} cannot run")

    def unavailable(self) -> Results:
        return Results(suite=self.name, state="unavailable", reason=self._reason)
