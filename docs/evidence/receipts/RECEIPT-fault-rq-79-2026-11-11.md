A deliberate fault applied to gate row RQ-79 reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: RQ-79
revision: a6d94aeb824f4749a051a3c6da276a016784d037
date: 2026-11-11
command: uv run pytest tests/test_inbox_crash.py tests/test_lease.py tests/test_edge.py tests/test_channels_contract.py tests/test_whatsapp.py tests/test_elder_bot_authority.py -q
fault: one test case corrupted
result: invalid
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
