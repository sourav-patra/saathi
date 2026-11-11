A deliberate fault applied to gate row RQ-17 reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: RQ-17
revision: a6d94aeb824f4749a051a3c6da276a016784d037
date: 2026-11-11
command: uv run pytest tests/test_guards.py tests/test_replies.py tests/test_voice_store.py -q
fault: one test case corrupted
result: invalid
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
