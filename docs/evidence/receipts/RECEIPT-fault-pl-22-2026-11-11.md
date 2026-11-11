A deliberate fault applied to gate row PL-22 reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: PL-22
revision: a6d94aeb824f4749a051a3c6da276a016784d037
date: 2026-11-11
command: uv run python scripts/repo_settings.py --check PL-22
fault: a stub gh answering with a wrong setting
result: invalid
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
