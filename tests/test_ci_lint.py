"""LT-38: ci_lint fails on a `uses:` line without a 40 hex digest (RQ-72a, TQ-61)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ci_lint"
_spec = importlib.util.spec_from_file_location("ci_lint", ROOT / "scripts" / "ci_lint.py")
assert _spec and _spec.loader
ci_lint = importlib.util.module_from_spec(_spec)
sys.modules["ci_lint"] = ci_lint
_spec.loader.exec_module(ci_lint)


def test_unpinned_action_fails() -> None:
    findings = ci_lint.check_actions_pinned(FIXTURES / "unpinned.yml")
    assert len(findings) == 1 and "actions/checkout@v4" in findings[0]
    assert ci_lint.main([str(FIXTURES / "unpinned.yml")]) == 1


def test_digest_pinned_passes() -> None:
    assert ci_lint.check_actions_pinned(FIXTURES / "pinned.yml") == []
    assert ci_lint.main([str(FIXTURES / "pinned.yml")]) == 0


def test_short_digest_and_branch_fail(tmp_path: Path) -> None:
    wf = tmp_path / "w.yml"
    wf.write_text("steps:\n  - uses: a/b@abcdef1\n  - uses: a/c@main\n  - uses: a/d@" + "g" * 40 + "\n")
    assert len(ci_lint.check_actions_pinned(wf)) == 3


def test_image_needs_a_digest(tmp_path: Path) -> None:
    wf = tmp_path / "w.yml"
    wf.write_text("services:\n  postgres:\n    image: postgres:16\n")
    assert len(ci_lint.check_actions_pinned(wf)) == 1
    wf.write_text("services:\n  postgres:\n    image: postgres:16@sha256:" + "a" * 64 + "\n")
    assert ci_lint.check_actions_pinned(wf) == []


def test_repo_workflows_are_pinned() -> None:
    assert ci_lint.main([str(ROOT / ".github" / "workflows")]) == 0
