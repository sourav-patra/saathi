"""LT-36: the document lint fails on each rule's fixture and passes on the clean one (RQ-101, PL-19)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "doc_lint"
_spec = importlib.util.spec_from_file_location("doc_lint", ROOT / "scripts" / "doc_lint.py")
assert _spec and _spec.loader
doc_lint = importlib.util.module_from_spec(_spec)
sys.modules["doc_lint"] = doc_lint
_spec.loader.exec_module(doc_lint)

RULES = [
    "filename_date", "filename_space", "dash", "filler", "size", "index_row", "record_shape", "closing_line",
    "bullet_bold",
]


def rules_hit(path: Path, names: Path | None = None) -> set[str]:
    findings, _ = doc_lint.lint([path], names)
    return {f.rule for f in findings}


def test_rule_names_match_the_lld() -> None:
    assert list(doc_lint.RULES) == RULES


@pytest.mark.parametrize("rule", RULES)
def test_fails_per_rule(rule: str) -> None:
    fails = sorted((FIXTURES / rule).glob("fail*"))
    assert fails, f"no failing fixture for {rule}"
    for d in fails:
        assert rules_hit(d) == {rule}, d.name


@pytest.mark.parametrize("rule", RULES)
def test_passes_per_rule(rule: str) -> None:
    assert rules_hit(FIXTURES / rule / "pass") == set()


def test_names_rule_fails_with_file() -> None:
    names = FIXTURES / "names" / "names.txt"
    assert rules_hit(FIXTURES / "names" / "fail", names) == {"names"}
    assert rules_hit(FIXTURES / "names" / "pass", names) == set()


def test_names_unavailable_without_file(capsys: pytest.CaptureFixture[str]) -> None:
    findings, notes = doc_lint.lint([FIXTURES / "names" / "fail"], None)
    assert findings == []
    assert any(n.startswith("names: unavailable") for n in notes)
    assert doc_lint.main([str(FIXTURES / "names" / "fail")]) == 0
    assert "names: unavailable" in capsys.readouterr().out


def test_clean_fixture_passes() -> None:
    assert doc_lint.main([str(FIXTURES / "clean")]) == 0


def test_main_exits_1_on_a_finding() -> None:
    assert doc_lint.main([str(FIXTURES / "dash" / "fail")]) == 1


def test_contract_and_index_limits(tmp_path: Path) -> None:
    contract = tmp_path / "SPEC-big-2026-11-10.md"
    contract.write_text("x " * 12100 + "\n\nSkipped or not checked: nothing.\n")
    index = tmp_path / "00-INDEX.md"
    index.write_text("# Index\n\n" + "filler row\n" * 200 + "\nSkipped or not checked: nothing.\n")
    findings, _ = doc_lint.lint([tmp_path])
    assert {(f.rule, f.path.name) for f in findings} == {("size", contract.name), ("size", index.name)}


def test_contract_over_22500_warns_without_failing(tmp_path: Path) -> None:
    contract = tmp_path / "SPEC-warn-2026-11-10.md"
    contract.write_text("x " * 11400 + "\n\nSkipped or not checked: nothing.\n")
    findings, notes = doc_lint.lint([contract])
    assert findings == []
    assert any("warn at 22500" in n for n in notes)


def test_receipt_is_exempt_from_closing_line(tmp_path: Path) -> None:
    receipt = tmp_path / "RECEIPT-sample-2026-11-10.md"
    receipt.write_text("This receipt proves a thing.\n\nkind: receipt\n- a bullet\n")
    assert rules_hit(receipt) == set()


def test_design_set_passes() -> None:
    paths = [ROOT / "00-INDEX.md", ROOT / "DOC-PHILOSOPHY.md", ROOT / "docs" / "design", ROOT / "docs" / "LOCKS.md",
             ROOT / "docs" / "archive"]
    findings, _ = doc_lint.lint(paths)
    assert [str(f) for f in findings] == []


def test_ci_scope_passes() -> None:
    """The exact CI command: doc_lint.py 00-INDEX.md README.md docs."""
    findings, _ = doc_lint.lint([ROOT / "00-INDEX.md", ROOT / "README.md", ROOT / "docs"])
    assert [str(f) for f in findings] == []
