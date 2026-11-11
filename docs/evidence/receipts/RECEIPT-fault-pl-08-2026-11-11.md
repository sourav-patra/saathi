A deliberate fault applied to gate row PL-08 reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: PL-08
revision: a6d94aeb824f4749a051a3c6da276a016784d037
date: 2026-11-11
command: uv run python scripts/leak_scan.py --history
fault: a planted cloud key in a scratch repository and its history
result: invalid
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
