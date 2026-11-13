"""Source faults for safety tests; production sources are only read, never edited."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Mutation:
    id: str
    target: str
    before: str
    after: str
    description: str

    def apply(self, tree: Path) -> None:
        target = tree / self.target
        source = target.read_text(encoding="utf-8")
        if source.count(self.before) != 1:
            raise ValueError(f"{self.id}: expected exactly one source match in {self.target}")
        target.write_text(source.replace(self.before, self.after, 1), encoding="utf-8")


RADAR_MUTATIONS = (
    Mutation(
        "radar_and", "src/saathi/guards/radar.py",
        "return RadarResult(emergency=em, scam=sc, matches=tuple(matches), rule_ids=tuple(ids))",
        'return RadarResult(emergency=kw.emergency and "emergency" in kinds, '
        'scam=kw.scam and "scam" in kinds, matches=tuple(matches), rule_ids=tuple(ids))',
        "combine requires both keyword and classifier flags instead of either",
    ),
    Mutation(
        "radar_phrase", "src/saathi/guards/radar.py", '"chest pain", ', "",
        "the chest pain emergency phrase is dropped",
    ),
)
POLICY_MUTATIONS = (
    Mutation(
        "policy_payment", "src/saathi/policy/engine.py",
        "if decision.verdict == Verdict.DENY:",
        "if decision.verdict == Verdict.DENY and live_quote.payment_method != PaymentMethod.UPI:",
        "authorize_commit accepts a UPI payment despite a policy denial",
    ),
    Mutation(
        "policy_hash", "src/saathi/policy/engine.py",
        "if token.quote_hash != quote_hash(approved_quote):", "if False:",
        "authorize_commit skips the approval-token quote-hash check",
    ),
)


def mutations_for(argv: Sequence[str]) -> tuple[Mutation, ...]:
    if "pytest" not in argv:
        return ()
    files = {Path(arg.split("::", 1)[0]).name for arg in argv}
    radar = "test_guards.py" in files or any(name.startswith("test_radar") for name in files)
    policy = "test_policy.py" in files
    return (*(RADAR_MUTATIONS if radar else ()), *(POLICY_MUTATIONS if policy else ()))


def affected_command(argv: Sequence[str]) -> list[str]:
    """Run only safety files, avoiding unrelated database tests in a combined registry row."""
    files = [
        arg.split("::", 1)[0] for arg in argv
        if Path(arg.split("::", 1)[0]).name in {"test_guards.py", "test_policy.py"}
        or Path(arg.split("::", 1)[0]).name.startswith("test_radar")
    ]
    return [*argv[:argv.index("pytest") + 1], *dict.fromkeys(files), "-q"]


def copy_tree(root: Path, scratch: Path) -> Path:
    """Copy current sources (including uncommitted tests), without a venv, git data or bytecode."""
    tree = scratch / "tree"
    tree.mkdir(parents=True)
    for name in ("src", "scripts", "tests"):
        shutil.copytree(root / name, tree / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(root / "pyproject.toml", tree / "pyproject.toml")
    shutil.copy2(root / "uv.lock", tree / "uv.lock")
    return tree


def mutation_env(tree: Path) -> dict[str, str]:
    # Reuse the caller's dependencies while pytest imports the copied src via its own configuration.
    # Never sync an editable install from a faulted copy into the caller's environment.
    return {
        "PYTHONPATH": str(tree / "src"),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "",
        "PYTEST_PLUGINS": "",
        "UV_NO_SYNC": "1",
        "UV_PROJECT_ENVIRONMENT": sys.prefix,
    }


def policy_rule_rows(root: Path) -> list[dict]:
    """Resolve every rule-index entry backed by test_policy.py, including later-phase rules."""
    from rule_index import collect_markers, load_rules

    markers = collect_markers(root / "tests")
    return [
        {
            "id": rule["id"], "phase": rule["phase"], "kind": "auto",
            "command": ["uv", "run", "pytest", "tests/test_policy.py", "-q"], "receipt": "RECEIPT-v1",
        }
        for rule in load_rules(root / "scripts" / "rules.toml")
        if any(test.startswith("test_policy.py::") for test in markers.get(rule["id"], []))
    ]
