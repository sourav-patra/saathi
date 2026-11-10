"""Rule-to-test index (RQ-63): every rule at or below PHASE has a test; every marker names a rule."""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def collect_markers(tests: Path) -> dict[str, list[str]]:
    """AST scan of test functions decorated with @rule("PE-nn", ...): rule id -> ["file::test", ...]."""
    found: dict[str, list[str]] = {}
    for path in sorted(tests.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not node.name.startswith("test"):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fn = dec.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if name != "rule":
                    continue
                for arg in dec.args:
                    rid = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "<non-literal>"
                    found.setdefault(rid, []).append(f"{path.name}::{node.name}")
    return found


def load_rules(rules: Path) -> list[dict]:
    return tomllib.loads(rules.read_text(encoding="utf-8")).get("rule", [])


def check_rules(rules: Path, markers: Mapping[str, list[str]], phase: int) -> list[str]:
    """Findings: a rule at or below `phase` with no test, and a marker that names no rule.
    A rule of a later phase is not evaluated."""
    table = load_rules(rules)
    known = {r["id"] for r in table}
    findings = [
        f"marker names unknown rule {rid}: {', '.join(tests)}" for rid, tests in markers.items() if rid not in known
    ]
    for r in table:
        if r["phase"] <= phase and not markers.get(r["id"]):
            findings.append(f"rule {r['id']} (phase {r['phase']}) has no test")
    return findings


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog="rule_index.py")
    ap.add_argument("--phase", type=int, help="default: the number in the PHASE file")
    ap.add_argument("--rules", type=Path, default=ROOT / "scripts" / "rules.toml")
    ap.add_argument("--tests", type=Path, default=ROOT / "tests")
    args = ap.parse_args(list(argv))
    phase = args.phase if args.phase is not None else int((ROOT / "PHASE").read_text().strip())
    findings = check_rules(args.rules, collect_markers(args.tests), phase)
    for f in findings:
        print(f)
    print(f"rule index: phase {phase}, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
