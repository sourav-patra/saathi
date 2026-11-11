"""CLI: `python -m evals.run <suite>|--all`.

Exit 1 when a dataset is missing, n is 0, a suite is invalid, or a suite is skipped without a named reason (RQ-61).
A suite that is unavailable with a named reason (private set absent, code under test not built yet) is printed as
unavailable and does not pass; `--strict` turns it into exit 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals import report
from evals.dataset import DatasetEmpty, DatasetMissing, EvalError
from evals.plan import ApprovalMissing, authorize, load_approvals, load_plan
from evals.registry import REGISTRY_PATH, Row, check_registry, load_registry
from evals.results import Results
from evals.suites.false_alarm import FalseAlarmSuite
from evals.suites.pending import PendingSuite
from evals.suites.radar import RadarHeldoutSuite, RadarSuite
from evals.suites.redaction import RedactionSuite
from evals.suites.trace_canary import TraceCanarySuite

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DATASETS = HERE / "datasets"


def harness_suites() -> dict[str, object]:
    suites = (RadarSuite(), RadarHeldoutSuite(), RedactionSuite(), FalseAlarmSuite(), TraceCanarySuite())
    return {s.name: s for s in suites}


def current_phase(repo_root: Path = REPO_ROOT) -> int:
    try:
        return int((repo_root / "PHASE").read_text().strip())
    except (OSError, ValueError):
        return 0


def run_row(row: Row, root: Path, suites: dict[str, object] | None = None) -> Results:
    if row.runner == "pytest":
        return Results(suite=row.name, state="unavailable", reason=f"delegated to pytest: {row.target}")
    if row.runner in ("private", "pending"):
        return PendingSuite(row.name, row.reason).unavailable()
    suite = (suites or harness_suites()).get(row.name)
    if suite is None:
        return Results(suite=row.name, state="invalid", reason="registered as a harness suite but no runner exists")
    try:
        return suite.run(suite.load(root))
    except DatasetMissing as exc:
        if getattr(suite, "absent_is_unavailable", False):
            return Results(suite=row.name, state="unavailable", reason=str(exc))
        return Results(suite=row.name, state="invalid", reason=f"dataset missing: {exc}")
    except DatasetEmpty as exc:
        return Results(suite=row.name, state="invalid", reason=f"n is 0: {exc}")
    except EvalError as exc:
        return Results(suite=row.name, state="invalid", reason=str(exc))
    except Exception as exc:  # noqa: BLE001  (one crashing suite must not hide the others; the message may hold content)
        return Results(suite=row.name, state="invalid", reason=f"suite crashed: {type(exc).__name__}")


def exit_code(results: list[Results], strict: bool = False) -> int:
    for r in results:
        if r.state == "invalid":
            return 1
        if r.state == "unavailable" and (not r.reason or strict):
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.run")
    ap.add_argument("suite", nargs="?", help="a suite name from evals/registry.toml")
    ap.add_argument("--all", action="store_true", help="every suite at or below the current phase")
    ap.add_argument("--root", type=Path, default=DATASETS, help="datasets root")
    ap.add_argument("--strict", action="store_true", help="unavailable counts as a failure")
    ap.add_argument("--plan", type=Path, help="validate a model-variable run plan (Phase 0 plans only)")
    ap.add_argument("--approvals", type=Path, help="owner approval rows for paid runs")
    ap.add_argument("--receipt", metavar="DATE", help="print the radar receipt for the suite run (counts, hash, role)")
    args = ap.parse_args(argv)

    if args.plan:
        try:
            plan = load_plan(args.plan)
            authorize(plan, load_approvals(args.approvals))
        except ApprovalMissing as exc:
            print(f"refused: {exc}")
            return 1
        except EvalError as exc:
            print(f"invalid: {exc}")
            return 1
        print(f"plan ok: {plan.suite}, {plan.repeats} repeats, paid={plan.paid}; no model run is started in Phase 0")
        return 0

    problems = check_registry(REGISTRY_PATH, tuple(harness_suites()))
    if problems:
        for p in problems:
            print(f"registry: {p}")
        return 1
    rows = load_registry()
    if args.all:
        phase = current_phase()
        rows = [r for r in rows if r.phase <= phase]
    elif args.suite:
        rows = [r for r in rows if r.name == args.suite]
        if not rows:
            print(f"unknown suite: {args.suite}")
            return 1
    else:
        ap.print_usage()
        return 1

    results = [run_row(r, args.root) for r in rows]
    for res in results:
        print("\n".join(report.render(res)))
    print(report.summary(results))
    if args.receipt and len(results) == 1 and results[0].state != "unavailable" and results[0].rates:
        print(report.radar_receipt(results[0], date=args.receipt), end="")
    return exit_code(results, args.strict)


if __name__ == "__main__":
    sys.exit(main())
