A deliberate fault applied to gate row RQ-63 reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: RQ-63
revision: a6d94aeb824f4749a051a3c6da276a016784d037
date: 2026-11-11
command: uv run python scripts/rule_index.py --phase 0
fault: a planted phase 0 rule with no test
result: invalid
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
