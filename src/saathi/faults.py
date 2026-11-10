"""Test-only fault hooks (M29; RQ-79).

`fault(point)` kills the process with SIGKILL when `SAATHI_ENV` is `test` and `SAATHI_FAULT_POINT` names `point`.
In every other case it does nothing: no switch, no kill. The crash tests use it to stop the edge and the worker at the
places where a restart could lose or repeat work. A production deployment never sets `SAATHI_ENV=test`, and the
check reads the environment at each call, so nothing is armed by importing this module.
"""

from __future__ import annotations

import os
import signal
from typing import Final

ENV_SWITCH: Final = "SAATHI_ENV"
ENV_POINT: Final = "SAATHI_FAULT_POINT"
SWITCH_VALUE: Final = "test"

EDGE_BEFORE_COMMIT: Final = "edge.before_commit"  # rows written, transaction not committed: no row, no 200
EDGE_AFTER_COMMIT: Final = "edge.after_commit"  # committed, 200 not yet sent: the platform retries, the key dedupes
WORKER_BEFORE_SETTLE: Final = "worker.before_settle"  # effects sent, row not settled: the next worker repeats the keys
ALERT_BETWEEN_SEND_AND_MARK: Final = "alert.between_send_and_mark"  # alert sent, not marked: the same key again

POINTS: Final = frozenset({EDGE_BEFORE_COMMIT, EDGE_AFTER_COMMIT, WORKER_BEFORE_SETTLE, ALERT_BETWEEN_SEND_AND_MARK})


def armed(point: str) -> bool:
    """True only with the test switch on and this point named."""
    return os.environ.get(ENV_SWITCH) == SWITCH_VALUE and os.environ.get(ENV_POINT) == point


def fault(point: str) -> None:
    """Kill this process at `point` when armed. A point that is not in `POINTS` is a mistake and raises only when the
    switch is on, so a typo can never change behaviour outside a test."""
    if os.environ.get(ENV_SWITCH) != SWITCH_VALUE:
        return
    if point not in POINTS:
        raise ValueError(f"unknown fault point {point!r}")
    if os.environ.get(ENV_POINT) == point:
        os.kill(os.getpid(), signal.SIGKILL)
