"""Phase 0 suites. Each one implements the `Suite` protocol: `load(root)` then `run(ds)`."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from evals.dataset import Dataset
from evals.results import Results


class Suite(Protocol):
    name: str

    def load(self, root: Path) -> Dataset: ...  # reads datasets/<name>/manifest.json

    def run(self, ds: Dataset) -> Results: ...  # code-graded only in Phase 0
