"""Pytest plugin of the deliberate gate fault (RQ-62): it corrupts the first test case of the run.

Loaded only by scripts/record_fault.py (`-p fault_plugin` with scripts/ on PYTHONPATH). A gate row whose tests
still pass with one case corrupted would prove nothing; the fault run must report invalid.
"""

from __future__ import annotations

import pytest

_state = {"done": False}


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> None:
    if not _state["done"]:
        _state["done"] = True
        raise AssertionError(f"deliberate fault (RQ-62): case {item.name} corrupted")
