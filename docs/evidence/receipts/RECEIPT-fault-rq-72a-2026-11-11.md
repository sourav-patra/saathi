A deliberate fault applied to gate row RQ-72a reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: RQ-72a
revision: a6d94aeb824f4749a051a3c6da276a016784d037
date: 2026-11-11
command: uv run python scripts/ci_lint.py .github/workflows
fault: a planted workflow with an unpinned uses
result: invalid
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
