"""LT-37: design_check.py runs in CI, passes on the repository and fails on a fault in a scratch copy (RQ-103, PL-24).

scripts/design_check.py is a locked design file and is never edited. Each fault is applied to a copy under tmp_path;
the copy's index and lock hashes are refreshed after the edit so that only the intended check can fail.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "design_check.py"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(root: Path) -> tuple[int, str]:
    argv = [sys.executable, str(SCRIPT), "--root", str(root)]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    return done.returncode, done.stdout + done.stderr


def make_copy(tmp: Path) -> Path:
    dst = tmp / "repo"
    dst.mkdir()
    shutil.copytree(ROOT / "docs", dst / "docs", ignore=shutil.ignore_patterns("evidence"))
    shutil.copytree(ROOT / "docs" / "evidence" / "reviews", dst / "docs" / "evidence" / "reviews")
    shutil.copy(ROOT / "00-INDEX.md", dst / "00-INDEX.md")
    shutil.copy(ROOT / "DOC-PHILOSOPHY.md", dst / "DOC-PHILOSOPHY.md")
    return dst


def edit(repo: Path, rel: str, old: str, new: str) -> None:
    """Replace old with new in a design file, then refresh its index prefix and lock hash."""
    p = repo / rel
    before = sha(p)
    text = p.read_text()
    assert old in text, f"{old!r} not in {rel}"
    p.write_text(text.replace(old, new, 1))
    after = sha(p)
    idx = repo / "00-INDEX.md"
    idx.write_text(idx.read_text().replace(before[:12], after[:12]))
    locks = repo / "docs" / "LOCKS.md"
    locks.write_text(locks.read_text().replace(before, after))


def test_repo_is_ok() -> None:
    code, out = run(ROOT)
    assert code == 0 and out.rstrip().endswith("result: OK"), out


def test_copy_is_ok_before_a_fault(tmp_path: Path) -> None:
    code, out = run(make_copy(tmp_path))
    assert code == 0, out


def test_stale_pin_fails(tmp_path: Path) -> None:
    repo = make_copy(tmp_path)
    pin = "TRD-saathi-build-2026-11-09.md sha256 "
    edit(repo, "docs/design/LLD-saathi-build-2026-11-10.md", pin + "6", pin + "0")
    code, out = run(repo)
    assert code == 1 and "pin of TRD-saathi-build-2026-11-09.md is stale" in out, out


def test_deleted_tq_row_unmapped(tmp_path: Path) -> None:
    repo = make_copy(tmp_path)
    rel = "docs/design/TRD-saathi-checks-2026-11-09.md"
    row = next(line for line in (repo / rel).read_text().splitlines() if line.startswith("| TQ-59 |"))
    edit(repo, rel, row + "\n", "")
    code, out = run(repo)
    assert code == 1 and "unmapped ids:" in out and "RQ-101" in out, out


def test_lld_rq_without_module_fails(tmp_path: Path) -> None:
    repo = make_copy(tmp_path)
    edit(repo, "docs/design/LLD-saathi-2026-11-10.md", "| RQ-103; PL-24 | TQ-59 |", "| PL-24 | TQ-59 |")
    code, out = run(repo)
    assert code == 1 and "lld: Phase 0 ids with no module row: RQ-103" in out, out


def test_lld_rq_without_test_fails(tmp_path: Path) -> None:
    repo = make_copy(tmp_path)
    rel = "docs/design/LLD-saathi-tests-2026-11-10.md"
    row = next(line for line in (repo / rel).read_text().splitlines() if line.startswith("| LT-37 |"))
    edit(repo, rel, row + "\n", "")
    code, out = run(repo)
    assert code == 1 and re.search(r"lld: Phase 0 ids with no test row: .*RQ-103", out), out


def test_index_base_path_resolves(tmp_path: Path) -> None:
    """D-94: a name with no slash is in docs/design/; a name with a slash is relative to the repository root."""
    repo = make_copy(tmp_path)
    idx = repo / "00-INDEX.md"
    text = idx.read_text()
    assert "|SPEC-saathi-product-2026-11-10.md|" in text
    name = "SPEC-saathi-product-2026-11-10.md"
    idx.write_text(text.replace(f"|{name}|", f"|docs/design/{name}|"))
    assert run(repo)[0] == 0
    idx.write_text(text.replace("|SPEC-saathi-product-2026-11-10.md|", "|./SPEC-saathi-product-2026-11-10.md|"))
    code, out = run(repo)
    # The locked script reports `index path missing` only after its locks check, which reads the missing file and
    # raises first (design-issue in RECEIPT-wu-02). Either way the run must fail and name the unresolved path.
    assert code == 1 and "SPEC-saathi-product-2026-11-10.md" in out and (
        "index path missing" in out or "FileNotFoundError" in out
    ), out


def test_ci_runs_design_check() -> None:
    """The CI job `design-check` runs the script from the repository root."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    job = re.search(r"^  design-check:\n(.*?)(?=^  \S|\Z)", ci, re.M | re.S)
    assert job and "run: python3 scripts/design_check.py" in job.group(1)
