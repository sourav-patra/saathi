"""WU-27: actual safety assertions kill each source mutant in a fresh tree."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from scripts import record_fault

gate = record_fault.gate
mutations = record_fault.mutation_faults
ROOT = Path(__file__).resolve().parent.parent


def _row(test_file: str):
    return gate.Row(
        id="RQ-17" if test_file == "test_guards.py" else "PE-12", phase=0, kind="auto",
        command=[sys.executable, "-m", "pytest", f"tests/{test_file}", "-q"], receipt="RECEIPT-v1",
    )


@pytest.mark.parametrize(
    ("test_file", "expected"),
    [
        ("test_guards.py", {
            "radar_and": "test_fake_classifier_adds_flag", "radar_phrase": "test_phrases_only_added",
        }),
        ("test_policy.py", {
            "policy_payment": "test_config_cannot_widen_payment_methods",
            "policy_hash": "test_commit_rejects_modified_approved_quote",
        }),
    ],
)
def test_real_mutants_fail_and_clean_control_passes(test_file, expected, tmp_path):
    targets = {m.target for m in mutations.mutations_for(_row(test_file).command)}
    before = {target: hashlib.sha256((ROOT / target).read_bytes()).hexdigest() for target in targets}
    assert record_fault.run_fault(_row(test_file), tmp_path) == "invalid"
    receipt = gate.parse_receipt(next(tmp_path.glob("RECEIPT-fault-*.md")))
    assert receipt.fields["clean"] == "verified; exit 0"
    for mutation_id, failed_case in expected.items():
        evidence = receipt.fields[f"mutation_{mutation_id}"]
        assert evidence.startswith("invalid; exit 1;")
        assert failed_case in evidence
    assert receipt.fields["result"] == "invalid"
    assert before == {target: hashlib.sha256((ROOT / target).read_bytes()).hexdigest() for target in targets}


def test_copies_are_independent_and_mutation_requires_exact_source(tmp_path):
    first = mutations.copy_tree(ROOT, tmp_path / "first")
    second = mutations.copy_tree(ROOT, tmp_path / "second")
    mutation = mutations.RADAR_MUTATIONS[1]
    original = (second / mutation.target).read_bytes()
    mutation.apply(first)
    assert (second / mutation.target).read_bytes() == original
    assert (first / mutation.target).read_bytes() != original
    with pytest.raises(ValueError, match="expected exactly one source match"):
        mutation.apply(first)


def test_discovers_all_policy_rule_index_entries():
    from rule_index import collect_markers, load_rules

    markers = collect_markers(ROOT / "tests")
    expected = {rid for rid, tests in markers.items() if any(t.startswith("test_policy.py::") for t in tests)}
    rows = mutations.policy_rule_rows(ROOT)
    assert {r["id"] for r in rows} == expected
    assert {"PE-01", "PE-12", "PE-31"} <= expected
    rules = {r["id"]: r for r in load_rules(ROOT / "scripts" / "rules.toml")}
    assert all(r["phase"] == rules[r["id"]]["phase"] for r in rows)


def test_every_radar_registry_row_uses_both_mutations():
    rows = gate.load_registry(ROOT / "scripts" / "gate_registry.toml")
    affected = [r for r in rows if "tests/test_guards.py" in (r.command or [])]
    assert {"RQ-17", "RQ-116"} <= {r.id for r in affected}
    assert all(mutations.mutations_for(r.command) == mutations.RADAR_MUTATIONS for r in affected)
    assert mutations.mutations_for(["uv", "run", "pytest", "tests/test_radar_extra.py"]) == mutations.RADAR_MUTATIONS
    assert not mutations.mutations_for(["uv", "run", "pytest", "tests/test_replies.py"])


@pytest.mark.parametrize("survivor", [0, 1])
def test_a_surviving_mutation_makes_cli_fail(survivor, tmp_path, monkeypatch):
    outcomes = [(0, "1 passed"), (1, "1 failed")]
    results = iter([(0, "1 passed"), outcomes[survivor], outcomes[1 - survivor]])
    monkeypatch.setattr(record_fault, "env_runner", lambda *a: lambda argv: next(results))
    assert record_fault.main(["--row", "RQ-17", "--receipts", str(tmp_path)]) == 1
    receipt = gate.parse_receipt(next(tmp_path.glob("RECEIPT-fault-*.md")))
    assert receipt.fields["result"] == "verified"
    assert "broken gate" in receipt.sentence
    expected = ["verified; exit 0", "invalid; exit 1"]
    assert receipt.fields["mutation_radar_and"] == expected[survivor]
    assert receipt.fields["mutation_radar_phrase"] == expected[1 - survivor]


@pytest.mark.parametrize("code,stdout", [(2, "collection error"), (1, "interpreter startup failed")])
def test_execution_errors_are_not_killed_mutants(code, stdout, tmp_path, monkeypatch):
    results = iter([(0, "1 passed"), (code, stdout), (code, stdout)])
    monkeypatch.setattr(record_fault, "env_runner", lambda *a: lambda argv: next(results))
    assert record_fault.run_fault(_row("test_policy.py"), tmp_path) == "unavailable"


def test_broken_clean_control_stops_mutation_evidence(tmp_path, monkeypatch):
    results = iter([(1, "1 failed")])
    monkeypatch.setattr(record_fault, "env_runner", lambda *a: lambda argv: next(results))
    assert record_fault.run_fault(_row("test_policy.py"), tmp_path) == "unavailable"
    receipt = gate.parse_receipt(next(tmp_path.glob("RECEIPT-fault-*.md")))
    assert receipt.fields["clean"] == "invalid; exit 1"
    assert "mutation_policy_payment" not in receipt.fields
    assert "mutation_policy_hash" not in receipt.fields


def test_combined_row_runs_only_affected_safety_files():
    command = ["uv", "run", "pytest", "tests/test_guards.py", "tests/test_voice_store.py", "-q"]
    assert mutations.affected_command(command) == ["uv", "run", "pytest", "tests/test_guards.py", "-q"]


def test_explicit_policy_rule_runs_even_in_a_later_phase(tmp_path, monkeypatch):
    seen = []

    def run(row, receipts):
        seen.append(row)
        return "invalid"

    monkeypatch.setattr(record_fault, "run_fault", run)
    assert record_fault.main(["--row", "PE-12", "--receipts", str(tmp_path)]) == 0
    assert [r.id for r in seen] == ["PE-12"]


def test_unknown_row_is_not_a_successful_empty_run(tmp_path):
    with pytest.raises(SystemExit) as error:
        record_fault.main(["--row", "not-a-rule", "--receipts", str(tmp_path)])
    assert error.value.code == 2
