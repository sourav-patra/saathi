"""LT-34: the phase gate (RQ-100). Fake runners and temp registries only; no row of the real registry is run."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gate  # noqa: E402

TODAY = date(2026, 11, 10)
REV = "a" * 40


def _registry(tmp: Path, rows: str) -> Path:
    p = tmp / "reg.toml"
    p.write_text(rows)
    return p


def _row(i: str, phase: int, kind: str, command: str = "", valid_days: int = 90) -> str:
    cmd = f"command = {command}\n" if command else ""
    head = f'[[row]]\nid = "{i}"\nphase = {phase}\nkind = "{kind}"\n'
    return f'{head}{cmd}receipt = "RECEIPT-v1"\nvalid_days = {valid_days}\n\n'


def _receipt(
    dirp: Path, i: str, when: date, signer: str | None = "owner role", result: str = "verified", note: str = ""
) -> None:
    dirp.mkdir(exist_ok=True)
    lines = [
        f"This receipt proves the owner check for {i}.",
        "",
        f"id: {i}",
        f"revision: {REV}",
        f"date: {when.isoformat()}",
        "command: read the page",
        f"result: {result}",
    ]
    if signer:
        lines.append(f"signer: {signer}")
    if note:
        lines.append(f"note: {note}")
    (dirp / f"RECEIPT-{i.lower()}-{when.isoformat()}.md").write_text("\n".join(lines) + "\n")


def _run(tmp: Path, rows: str, *extra: str, runner=lambda argv: 0) -> int:
    reg = _registry(tmp, rows)
    return gate.main(["--registry", str(reg), "--receipts", str(tmp / "rc"), *extra], run=runner, today=TODAY)


def test_registry_matches_prd_phase_table():
    prd = gate.prd_phase_table(gate.ROOT)
    reg = {r.id: r.phase for r in gate.load_registry(gate.ROOT / "scripts" / "gate_registry.toml")}
    phase0 = {k for k, v in prd.items() if v == 0}
    assert phase0 <= set(reg), f"registry misses {sorted(phase0 - set(reg))}"
    for rid, phase in reg.items():
        assert prd.get(rid) == phase, f"{rid}: registry phase {phase}, PRD {prd.get(rid)}"
    assert (gate.ROOT / "PHASE").read_text().strip() == "0"


def test_later_phase_row_not_printed(tmp_path, capsys):
    rows = _row("RQ-1", 0, "auto", '["true"]') + _row("RQ-2", 2, "manual")
    assert _run(tmp_path, rows, "--phase", "1") == 0
    out = capsys.readouterr().out
    assert "RQ-1 phase 0 auto verified" in out and "RQ-2" not in out


def test_missing_receipt_unavailable_exit1(tmp_path, capsys):
    reg = gate.load_registry(_registry(tmp_path, _row("RQ-5", 0, "manual")))
    assert gate.evaluate(reg[0], tmp_path / "rc", TODAY) == "unavailable"
    assert _run(tmp_path, _row("RQ-5", 0, "manual"), "--phase", "0") == 1
    assert "RQ-5 phase 0 manual unavailable" in capsys.readouterr().out


def test_auto_false_invalid_exit1(tmp_path, capsys):
    assert _run(tmp_path, _row("RQ-6", 0, "auto", '["false"]'), "--phase", "0", runner=gate.subprocess_runner) == 1
    assert "RQ-6 phase 0 auto invalid" in capsys.readouterr().out


def test_auto_missing_prerequisite_unavailable(tmp_path):
    row = gate.load_registry(_registry(tmp_path, _row("RQ-7", 0, "auto", '["true", "tests/test_nope_xyz.py"]')))[0]
    assert gate.evaluate(row, tmp_path, TODAY, run=lambda a: 0) == "unavailable"
    missing = gate.load_registry(_registry(tmp_path, _row("RQ-8", 0, "auto", '["no-such-program-xyz"]')))[0]
    assert gate.evaluate(missing, tmp_path, TODAY, run=lambda a: 0) == "unavailable"


def test_aged_receipt_unavailable(tmp_path):
    row = gate.load_registry(_registry(tmp_path, _row("RQ-9", 0, "manual", valid_days=30)))[0]
    rc = tmp_path / "rc"
    _receipt(rc, "RQ-9", TODAY - timedelta(days=10))
    assert gate.evaluate(row, rc, TODAY) == "verified"
    (rc / f"RECEIPT-rq-9-{(TODAY - timedelta(days=10)).isoformat()}.md").unlink()
    _receipt(rc, "RQ-9", TODAY - timedelta(days=31))
    assert gate.evaluate(row, rc, TODAY) == "unavailable"


def test_receipt_without_signer_not_verified(tmp_path):
    row = gate.load_registry(_registry(tmp_path, _row("RQ-10", 0, "manual")))[0]
    _receipt(tmp_path / "rc", "RQ-10", TODAY, signer=None)
    assert gate.evaluate(row, tmp_path / "rc", TODAY) != "verified"


def test_list_prints_all_ids(tmp_path, capsys):
    rows = _row("RQ-1", 0, "auto", '["true"]') + _row("RQ-2", 3, "manual")
    assert _run(tmp_path, rows, "--list") == 0
    out = capsys.readouterr().out
    assert "RQ-1 phase 0 auto" in out and "RQ-2 phase 3 manual" in out
    ids = {r.id for r in gate.load_registry(gate.ROOT / "scripts" / "gate_registry.toml")}
    assert len(ids) == 68


def test_not_applicable_needs_reason(tmp_path):
    row = gate.load_registry(_registry(tmp_path, _row("RQ-11", 0, "manual")))[0]
    rc = tmp_path / "rc"
    _receipt(rc, "RQ-11", TODAY, result="not applicable")
    assert gate.evaluate(row, rc, TODAY) == "invalid"
    (rc / f"RECEIPT-rq-11-{TODAY.isoformat()}.md").unlink()
    _receipt(rc, "RQ-11", TODAY, result="not applicable", note="owner: item deferred, re-entry condition not met")
    assert gate.evaluate(row, rc, TODAY) == "not_applicable"


def test_report_mode_exits_zero_and_prints_every_row(tmp_path, capsys):
    assert _run(tmp_path, _row("RQ-5", 0, "manual"), "--phase", "0", "--report") == 0
    assert "unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("text", ["RQ-16 to RQ-18", "PL-01 to PL-05"])
def test_expand_ids_ranges(text):
    assert len(gate.expand_ids(text)) == 3 or len(gate.expand_ids(text)) == 5
