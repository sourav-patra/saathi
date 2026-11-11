"""Suite registry (RQ-58): load registry.toml and check it against the suites the harness knows."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.toml"
REQUIRED = ("name", "grader_order", "dataset", "size", "split", "gate", "owner", "runner", "phase")
RUNNERS = ("harness", "pytest", "private", "pending")


@dataclass(frozen=True)
class Row:
    name: str
    grader_order: str
    dataset: str
    size: str
    split: str
    gate: str
    owner: str
    runner: str
    phase: int
    reason: str = ""
    target: str = ""
    pending_until: str = ""


def load_registry(path: Path = REGISTRY_PATH) -> list[Row]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = []
    for raw in data.get("suite", []):
        rows.append(
            Row(
                **{k: raw[k] for k in REQUIRED if k in raw},
                reason=raw.get("reason", ""),
                target=raw.get("target", ""),
                pending_until=raw.get("pending_until", ""),
            )
        )
    return rows


def check_registry(path: Path = REGISTRY_PATH, harness_suites: tuple[str, ...] = ()) -> list[str]:
    """Problems: a missing field, a duplicate name, a bad runner, a harness suite with no row, a row with no reason."""
    problems: list[str] = []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for raw in data.get("suite", []):
        name = str(raw.get("name", "?"))
        for key in REQUIRED:
            if raw.get(key) in (None, ""):
                problems.append(f"{name}: field {key} is missing")
        if name in seen:
            problems.append(f"{name}: duplicate row")
        seen.add(name)
        runner = raw.get("runner")
        if runner not in RUNNERS:
            problems.append(f"{name}: runner {runner!r} is not one of {RUNNERS}")
        if runner in ("private", "pending") and not raw.get("reason"):
            problems.append(f"{name}: a {runner} suite needs a named reason")
        if runner == "pytest" and not raw.get("target"):
            problems.append(f"{name}: a pytest suite needs a target")
        if raw.get("owner") != "owner":
            problems.append(f"{name}: owner must read owner")
    for suite in harness_suites:
        if suite not in seen:
            problems.append(f"{suite}: harness suite has no registry row")
    return problems
