"""LT-41: every auto gate row of Phase 0 has a fault receipt that shows its check failing (RQ-62, TQ-57)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RECEIPTS = ROOT / "docs" / "evidence" / "receipts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load("gate")
record_fault = _load("record_fault")
REGISTRY = gate.load_registry(ROOT / "scripts" / "gate_registry.toml")
AUTO_ROWS = [r for r in REGISTRY if r.kind == "auto" and r.phase == 0]


def _latest_fault_receipt(row_id: str) -> Path | None:
    found = sorted(RECEIPTS.glob(f"RECEIPT-fault-{row_id.lower()}-????-??-??.md"))
    return found[-1] if found else None


def test_every_auto_row_has_a_fault_receipt():
    assert AUTO_ROWS, "the registry has no Phase 0 auto rows"
    problems = []
    for row in AUTO_ROWS:
        path = _latest_fault_receipt(row.id)
        if path is None:
            problems.append(f"{row.id}: no fault receipt")
            continue
        rec = gate.parse_receipt(path)
        f = rec.fields
        if f.get("id") != row.id or f.get("command") != " ".join(row.command or []):
            problems.append(f"{row.id}: receipt id or command does not match the registry row")
        if f.get("result") != "invalid" or not f.get("fault"):
            problems.append(f"{row.id}: the fault did not produce invalid")
        if not gate.HEX40.match(f.get("revision", "")):
            problems.append(f"{row.id}: no 40 hex revision")
        if path.name != f"RECEIPT-fault-{row.id.lower()}-{date.fromisoformat(f.get('date', '')).isoformat()}.md":
            problems.append(f"{row.id}: file name and date disagree")
    assert not problems, "\n".join(problems)


def test_every_auto_command_has_a_defined_fault(tmp_path):
    for n, row in enumerate(AUTO_ROWS):
        scratch = tmp_path / str(n)
        scratch.mkdir()
        if row.command and "design_check.py" in " ".join(row.command):
            continue  # copies the tree; exercised by its own fault run
        fault = record_fault.make_fault(row.command or [], scratch)
        assert fault.argv != row.command and fault.description


def test_unknown_command_has_no_silent_fault(tmp_path):
    with pytest.raises(ValueError):
        record_fault.make_fault(["true"], tmp_path)


def test_record_fault_writes_a_parseable_receipt(tmp_path):
    p = record_fault.record_fault("RQ-62", "one case corrupted", "invalid", command=["uv", "run", "pytest"],
                                  receipts=tmp_path, today=date(2026, 11, 11), revision="a" * 40)  # fmt: skip
    assert p.name == "RECEIPT-fault-rq-62-2026-11-11.md"
    rec = gate.parse_receipt(p)
    assert rec.fields["result"] == "invalid" and rec.fields["fault"] == "one case corrupted"
    assert str(tmp_path) not in p.read_text()


def test_plugin_corrupts_a_passing_case(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    fault = record_fault.make_fault(["uv", "run", "pytest", "test_ok.py", "-q"], tmp_path)
    i = fault.argv.index("pytest")
    argv = [sys.executable, "-m", *fault.argv[i:]]
    env = {**__import__("os").environ, **fault.env}
    done = subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 1 and "deliberate fault" in done.stdout


def test_a_fault_that_stays_verified_is_reported(tmp_path, monkeypatch):
    row = gate.Row(id="RQ-72a", phase=0, kind="auto", command=["uv", "run", "python", "scripts/ci_lint.py", ".github"],
                   receipt="RECEIPT-v1")  # fmt: skip
    monkeypatch.setattr(record_fault.gate, "evaluate", lambda *a, **k: "verified")
    assert record_fault.run_fault(row, tmp_path, date(2026, 11, 11)) == "verified"
    text = next(tmp_path.glob("RECEIPT-fault-rq-72a-*.md")).read_text()
    assert "broken gate" in text and "result: verified" in text


def test_ci_lint_fault_is_invalid_for_real(tmp_path):
    row = next(r for r in AUTO_ROWS if r.id == "RQ-72a")
    assert record_fault.run_fault(row, tmp_path) == "invalid"
