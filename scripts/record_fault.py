#!/usr/bin/env python3
"""Fault recorder (RQ-62, TQ-57): python3 scripts/record_fault.py [--phase N] [--row ID ...] [--receipts DIR]

For each auto row of the gate registry it applies one deliberate fault, evaluates the faulted command with the
gate's own `evaluate`, and writes `RECEIPT-fault-<id>-<date>.md` with the fault and the state it produced. The
fault never touches the working tree: it runs on planted inputs in a temporary directory.

Faults by check:
- a pytest row: one test case is corrupted (scripts/fault_plugin.py fails the first case; `-x` stops the run);
- doc_lint: a planted document whose file name holds a space; design_check: a copy of the tree with one TQ row
  deleted; ci_lint: a planted workflow with an unpinned `uses:`; license_audit: a planted LGPL manifest line;
  rule_index: a planted phase 0 rule with no test; leak_scan: a planted key in a scratch repository (and a planted
  denylist term for the names row); repo_settings: a stub `gh` that answers with a setting that is wrong.

A fault that leaves the row verified is a broken gate: the receipt records `verified` and the run exits 1.
Exit 0: every fault produced invalid. 1: some fault did not.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate  # noqa: E402

ROOT = HERE.parent
RECEIPTS = ROOT / "docs" / "evidence" / "receipts"


@dataclass
class Fault:
    """A faulted command: argv to evaluate, extra environment, and a one-line description (no paths)."""

    argv: list[str]
    description: str
    env: dict[str, str] = field(default_factory=dict)


def record_fault(
    gate_id: str,
    fault: str,
    result: gate.State,
    *,
    command: Sequence[str] = (),
    receipts: Path = RECEIPTS,
    today: date | None = None,
    revision: str | None = None,
) -> Path:
    """Write RECEIPT-fault-<id>-<date>.md (first line a sentence, then `key: value` lines) and return its path."""
    today = today or date.today()
    revision = revision or gate._git_revision() or ""
    receipts.mkdir(parents=True, exist_ok=True)
    path = receipts / f"RECEIPT-fault-{gate_id.lower()}-{today.isoformat()}.md"
    outcome = "reported invalid" if result == "invalid" else f"reported {result}, which is a broken gate"
    path.write_text(
        f"A deliberate fault applied to gate row {gate_id} {outcome}; the clean row is run by scripts/gate.py.\n\n"
        f"kind: receipt\nid: {gate_id}\nrevision: {revision}\ndate: {today.isoformat()}\n"
        f"command: {' '.join(command)}\nfault: {fault}\nresult: {result}\n"
        "restore: the fault ran on planted inputs in a scratch directory; the tree was not changed\n"
        "note: written by scripts/record_fault.py; no content\n",
        encoding="utf-8",
    )
    return path


def _script(argv: Sequence[str]) -> str | None:
    return next((t for t in argv if t.startswith("scripts/") and t.endswith(".py")), None)


def _replace_after(argv: list[str], flag: str, value: str) -> list[str]:
    out = list(argv)
    if flag in out:
        out[out.index(flag) + 1] = value
    else:
        out += [flag, value]
    return out


def _git_init(repo: Path) -> None:
    ident = ["-c", "user.name=fault", "-c", "user.email=fault@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), *ident, "commit", "-q", "--no-verify", "-m", "fault"], check=True)


def make_fault(argv: Sequence[str], scratch: Path, root: Path = ROOT) -> Fault:
    """The deliberate fault for one row command. Raises ValueError for a command with no known fault."""
    argv = list(argv)
    if "pytest" in argv:
        i = argv.index("pytest")
        path = os.pathsep.join(p for p in (str(HERE), os.environ.get("PYTHONPATH", "")) if p)
        return Fault([*argv[: i + 1], "-p", "fault_plugin", "-x", *argv[i + 1 :]], "one test case corrupted",
                     {"PYTHONPATH": path})  # fmt: skip
    script = _script(argv)
    if script == "scripts/doc_lint.py":
        doc = scratch / "docs" / "notes about faults.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("Notes on a planted fault.\n", encoding="utf-8")
        return Fault([*argv[: argv.index(script) + 1], str(doc.parent)], "a document whose file name holds a space")
    if script == "scripts/design_check.py":
        copy = scratch / "tree"
        files = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
        for rel in filter(None, files.stdout.decode().split("\0")):
            src = root / rel
            if src.is_file() and not src.is_symlink():
                (copy / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, copy / rel)
        target = sorted((copy / "docs" / "design").glob("TRD-saathi-checks-*.md"))[0]
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        k = next(n for n, line in enumerate(lines) if line.startswith("| TQ-"))
        target.write_text("".join(lines[:k] + lines[k + 1 :]), encoding="utf-8")
        return Fault([*argv, "--root", str(copy)], "a copy of the tree with one TQ row deleted")
    if script == "scripts/ci_lint.py":
        wf = scratch / "workflows" / "ci.yml"
        wf.parent.mkdir(parents=True)
        wf.write_text("jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n", encoding="utf-8")
        return Fault([*argv[: argv.index(script) + 1], str(wf.parent)], "a planted workflow with an unpinned uses")
    if script == "scripts/license_audit.py":
        m = scratch / "manifest.txt"
        m.write_text("libplanted | LGPL-2.1-only\n", encoding="utf-8")
        return Fault(_replace_after(argv, "--manifest", str(m)), "a planted LGPL line in the image manifest")
    if script == "scripts/rule_index.py":
        rules = scratch / "rules.toml"
        planted = '\n[[rule]]\nid = "PE-999"\nimplements = "RQ-62"\nphase = 0\n'
        rules.write_text((root / "scripts" / "rules.toml").read_text(encoding="utf-8") + planted, encoding="utf-8")
        return Fault(_replace_after(argv, "--rules", str(rules)), "a planted phase 0 rule with no test")
    if script == "scripts/leak_scan.py":
        repo = scratch / "repo"
        repo.mkdir()
        if any(t.startswith("$") for t in argv):  # the names row
            names = scratch / "names.txt"
            names.write_text("zorblaxfault\n", encoding="utf-8")
            (repo / "notes.txt").write_text("a line naming zorblaxfault\n", encoding="utf-8")
            faulted = [names.as_posix() if t.startswith("$") else t for t in argv]
            _git_init(repo)
            return Fault([*faulted, str(repo)], "a planted denylist term in a scratch repository")
        (repo / "config.txt").write_text("key = " + "AK" + "IA" + "PLANTEDFAULT0001\n", encoding="utf-8")
        _git_init(repo)
        return Fault([*argv, str(repo)], "a planted cloud key in a scratch repository and its history")
    if script == "scripts/repo_settings.py":
        check = argv[argv.index("--check") + 1]
        body = '[{"login": "someone-else", "permissions": {"push": true}}]' if check == "PL-21" else "{}"
        bindir = scratch / "bin"
        bindir.mkdir()
        stub = bindir / "gh"
        stub.write_text(f"#!/bin/sh\ncat <<'EOF'\n{body}\nEOF\n", encoding="utf-8")
        stub.chmod(0o755)
        path = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
        return Fault([*argv, "--repo", "fault-owner/fault-repo"], "a stub gh answering with a wrong setting",
                     {"PATH": path})  # fmt: skip
    raise ValueError(f"no deliberate fault is defined for: {' '.join(argv)}")


def env_runner(extra: Mapping[str, str]) -> gate.Runner:
    def run(argv: Sequence[str]) -> tuple[int, str]:
        env = {**os.environ, **extra}
        done = subprocess.run(list(argv), cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800, check=False)
        return done.returncode, done.stdout

    return run


def run_fault(row: gate.Row, receipts: Path = RECEIPTS, today: date | None = None) -> gate.State:
    with tempfile.TemporaryDirectory(prefix="saathi-fault-") as tmp:
        fault = make_fault(row.command or [], Path(tmp))
        faulted = row.model_copy(update={"command": fault.argv})
        env = {**os.environ, **fault.env}
        state = gate.evaluate(faulted, receipts, today or date.today(), env_runner(fault.env), env=env)
    record_fault(row.id, fault.description, state, command=row.command or [], receipts=receipts, today=today)
    return state


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--phase", type=int, default=0)
    ap.add_argument("--row", action="append", default=[], help="only these row ids (repeatable)")
    ap.add_argument("--registry", type=Path, default=ROOT / "scripts" / "gate_registry.toml")
    ap.add_argument("--receipts", type=Path, default=RECEIPTS)
    args = ap.parse_args(argv)
    rows = [r for r in gate.load_registry(args.registry) if r.kind == "auto" and r.phase <= args.phase]
    if args.row:
        rows = [r for r in rows if r.id in set(args.row)]
    broken = 0
    for r in rows:
        state = run_fault(r, args.receipts)
        broken += state != "invalid"
        print(f"{r.id} fault {state}")
    print(f"record fault: {len(rows)} row(s), {broken} not invalid")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
