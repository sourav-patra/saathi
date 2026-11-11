A deliberate fault applied to gate row RQ-103 reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: RQ-103
revision: a6d94aeb824f4749a051a3c6da276a016784d037
date: 2026-11-11
command: python3 scripts/design_check.py
fault: a copy of the tree with one TQ row deleted
result: invalid
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
