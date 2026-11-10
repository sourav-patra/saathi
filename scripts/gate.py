"""Phase gate (RQ-100): evaluates the registry rows up to a phase and reports one state per row.

States: verified, invalid, unavailable, not_applicable. Only verified and not_applicable pass.
A row of a later phase is neither run nor printed (--list prints every row and runs none).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
State = Literal["verified", "invalid", "unavailable", "not_applicable"]
PASSING: frozenset[str] = frozenset({"verified", "not_applicable"})
RESULTS = ("verified", "invalid", "unavailable", "not applicable")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
PATH_PREFIXES = ("tests/", "scripts/", ".github/", "evals/", "infra/", "docs/", "src/")
PATH_SUFFIXES = (".py", ".toml", ".txt", ".md", ".sh", ".yml", ".json")

Runner = Callable[[Sequence[str]], "int | tuple[int, str]"]


class Row(BaseModel):
    id: str
    phase: int
    kind: Literal["auto", "manual"]
    command: list[str] | None = None
    receipt: str
    valid_days: int = 90


class Receipt(BaseModel):
    sentence: str
    fields: dict[str, str]


def subprocess_runner(argv: Sequence[str]) -> tuple[int, str]:
    """Run argv with no shell and return (exit code, stdout). A missing program raises FileNotFoundError."""
    done = subprocess.run(list(argv), cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False)
    return done.returncode, done.stdout


def _with_skip_report(argv: list[str]) -> list[str]:
    """Pytest rows run with -rs so that skipped tests show in the output."""
    if "pytest" in argv and "-rs" not in argv:
        i = argv.index("pytest")
        return [*argv[: i + 1], "-rs", *argv[i + 1 :]]
    return argv


def _pytest_all_ran(argv: Sequence[str], stdout: str) -> bool:
    """False when a pytest run skipped any test or passed none: a skipped test proves nothing (RQ-100)."""
    if "pytest" not in argv:
        return True
    if re.search(r"\b\d+ skipped\b", stdout):
        return False
    return re.search(r"\b[1-9]\d* passed\b", stdout) is not None


def load_registry(path: Path) -> list[Row]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = [Row(**r) for r in data.get("row", [])]
    seen: set[str] = set()
    for r in rows:
        if r.id in seen:
            raise ValueError(f"duplicate registry id {r.id}")
        seen.add(r.id)
        if r.kind == "auto" and not r.command:
            raise ValueError(f"auto row {r.id} has no command")
    return rows


def parse_receipt(path: Path) -> Receipt:
    """First line a plain sentence, then `key: value` lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].strip() or lines[0].startswith("#") or re.match(r"^[a-z_0-9]+:", lines[0]):
        raise ValueError(f"{path.name}: first line must be one plain sentence")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        key = key.strip().lstrip("-* ").strip("`")
        if sep and re.fullmatch(r"[a-z_0-9]+", key):
            fields[key] = value.strip().strip("`")
    return Receipt(sentence=lines[0].strip(), fields=fields)


def _latest_receipt(row_id: str, receipts: Path) -> Path | None:
    pattern = re.compile(rf"^RECEIPT-{re.escape(row_id.lower())}-(\d{{4}}-\d{{2}}-\d{{2}})\.md$")
    found = [(m.group(1), p) for p in receipts.glob("RECEIPT-*.md") if (m := pattern.match(p.name))]
    return max(found)[1] if found else None


def _evaluate_manual(row: Row, receipts: Path, today: date) -> State:
    path = _latest_receipt(row.id, receipts)
    if path is None:
        return "unavailable"
    try:
        rec = parse_receipt(path)
        when = date.fromisoformat(rec.fields.get("date", ""))
    except ValueError:
        return "invalid"
    f = rec.fields
    if path.name != f"RECEIPT-{row.id.lower()}-{when.isoformat()}.md" or not f.get("command"):
        return "invalid"
    if (today - when).days > row.valid_days or when > today:
        return "unavailable"
    if f.get("id", "").lower() != row.id.lower() or not HEX40.match(f.get("revision", "")):
        return "invalid"
    if not f.get("signer"):
        return "invalid"
    result = f.get("result", "")
    if result == "not applicable":
        return "not_applicable" if f.get("note") else "invalid"
    if result == "verified":
        return "verified"
    return "unavailable" if result == "unavailable" else "invalid"


def _prerequisite_missing(argv: Sequence[str], root: Path, env: Mapping[str, str]) -> bool:
    if shutil.which(argv[0]) is None:
        return True
    for tok in argv[1:]:
        if tok.startswith("$"):
            if not env.get(tok.lstrip("${").rstrip("}")):
                return True
            continue
        looks_like_path = not tok.startswith("-") and (tok.startswith(PATH_PREFIXES) or tok.endswith(PATH_SUFFIXES))
        if looks_like_path and not (root / tok).exists():
            return True
    return False


def evaluate(
    row: Row,
    receipts: Path,
    today: date,
    run: Runner = subprocess_runner,
    root: Path = ROOT,
    env: Mapping[str, str] | None = None,
) -> State:
    if row.kind == "manual":
        return _evaluate_manual(row, receipts, today)
    env = os.environ if env is None else env
    argv = list(row.command or [])
    if not argv or _prerequisite_missing(argv, root, env):
        return "unavailable"
    argv = [env.get(t.lstrip("${").rstrip("}"), t) if t.startswith("$") else t for t in argv]
    argv = _with_skip_report(argv)
    try:
        result = run(argv)
    except FileNotFoundError:
        return "unavailable"
    code, stdout = result if isinstance(result, tuple) else (result, "")
    if code == 2:
        return "unavailable"  # the exit-2 convention of the checkers: could not run, which is not a failed check
    if code != 0:
        return "invalid"
    if isinstance(result, tuple) and not _pytest_all_ran(argv, stdout):
        return "unavailable"
    return "verified"


def prd_phase_table(root: Path = ROOT) -> dict[str, int]:
    """Read the PRD phase table through 00-INDEX.md; fail on an id in two phases."""
    index = (root / "00-INDEX.md").read_text(encoding="utf-8")
    m = re.search(r"^\|contract\|(PRD-[^|]+\.md)\|", index, re.M)
    if not m:
        raise ValueError("00-INDEX.md has no PRD row")
    prd = (root / "docs" / "design" / m.group(1)).read_text(encoding="utf-8")
    table: dict[str, int] = {}
    for line in prd.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 5 or not re.match(r"^\d \w", cells[1]):
            continue
        phase = int(cells[1][0])
        for rid in expand_ids(cells[3]):
            if rid in table:
                raise ValueError(f"{rid} appears in two phases")
            table[rid] = phase
    return table


def expand_ids(text: str) -> list[str]:
    ids: list[str] = []
    for part in re.split(r",\s*", text.strip()):
        rng = re.fullmatch(r"([A-Z]+)-(\d+) to (?:[A-Z]+-)?(\d+)", part.strip())
        if rng:
            prefix, lo, hi = rng.group(1), rng.group(2), rng.group(3)
            ids += [f"{prefix}-{n:0{len(lo)}d}" for n in range(int(lo), int(hi) + 1)]
        elif re.fullmatch(r"[A-Z]+-\d+[a-z]?", part.strip()):
            ids.append(part.strip())
    return ids


def _git_revision() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    rev = out.stdout.strip()
    return rev if HEX40.match(rev) else None


def _write_run_receipt(row: Row, receipts: Path, today: date) -> Path | None:
    rev = _git_revision()
    if rev is None or not row.command:
        return None
    receipts.mkdir(parents=True, exist_ok=True)
    path = receipts / f"RECEIPT-{row.id.lower()}-{today.isoformat()}.md"
    path.write_text(
        f"The command of gate row {row.id} exited 0 at the stated revision.\n\n"
        f"id: {row.id}\nrevision: {rev}\ndate: {today.isoformat()}\ncommand: {' '.join(row.command)}\n"
        "result: verified\nnote: written by scripts/gate.py; no content\n",
        encoding="utf-8",
    )
    return path


def main(argv: Sequence[str], run: Runner = subprocess_runner, today: date | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gate.py", description=__doc__)
    ap.add_argument("--phase", type=int, help="evaluate rows whose phase is at or below N")
    ap.add_argument("--list", action="store_true", help="print every row with its phase and exit 0")
    ap.add_argument("--report", action="store_true", help="print every row of the phase, write a step summary, exit 0")
    ap.add_argument("--registry", type=Path, default=ROOT / "scripts" / "gate_registry.toml")
    ap.add_argument("--receipts", type=Path, default=ROOT / "docs" / "evidence" / "receipts")
    args = ap.parse_args(list(argv))
    rows = load_registry(args.registry)
    today = today or date.today()
    if args.list:
        for r in rows:
            print(f"{r.id} phase {r.phase} {r.kind}")
        return 0
    if args.phase is None:
        ap.error("--phase is required unless --list is given")
    lines: list[str] = []
    counts = dict.fromkeys(("verified", "invalid", "unavailable", "not_applicable"), 0)
    for r in rows:
        if r.phase > args.phase:
            continue
        state = evaluate(r, args.receipts, today, run)
        counts[state] += 1
        if state == "verified" and r.kind == "auto" and not args.report:
            _write_run_receipt(r, args.receipts, today)
        lines.append(f"{r.id} phase {r.phase} {r.kind} {state}")
    summary = f"summary: phase {args.phase}, " + ", ".join(f"{k} {v}" for k, v in counts.items())
    print("\n".join([*lines, summary]))
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if args.report and step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write("## Gate report\n\n```\n" + "\n".join([*lines, summary]) + "\n```\n")
    if args.report:
        return 0
    return 0 if counts["invalid"] == 0 and counts["unavailable"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
