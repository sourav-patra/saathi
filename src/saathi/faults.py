"""Test-only fault hooks (M29; RQ-79).

`fault(point)` kills the process with SIGKILL only when three things hold: `SAATHI_ENV` is `test`,
`SAATHI_FAULT_ARM_FILE` names a file that exists, and `SAATHI_FAULT_POINT` names `point`. In every other case it does
nothing. The crash tests use it to stop the edge, the worker and the alert service at the places where a restart could
lose or repeat work, and the crash rig creates the arm file in its own temporary directory. Compose pins
`SAATHI_ENV: prod` and no deployment has an arm file, so two separate mistakes would be needed to arm a service. The
check reads the environment and the file system at each call, so nothing is armed by importing this module.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Final

ENV_SWITCH: Final = "SAATHI_ENV"
ENV_POINT: Final = "SAATHI_FAULT_POINT"
ENV_ARM_FILE: Final = "SAATHI_FAULT_ARM_FILE"  # second guard (R16-1): must name an existing file
SWITCH_VALUE: Final = "test"

EDGE_BEFORE_COMMIT: Final = "edge.before_commit"  # rows written, transaction not committed: no row, no 200
EDGE_AFTER_COMMIT: Final = "edge.after_commit"  # committed, 200 not yet sent: the platform retries, the key dedupes
WORKER_BEFORE_SETTLE: Final = "worker.before_settle"  # effects sent, row not settled: the next worker repeats the keys
ALERT_BETWEEN_SEND_AND_MARK: Final = "alert.between_send_and_mark"  # alert sent, not marked: the same key again

POINTS: Final = frozenset({EDGE_BEFORE_COMMIT, EDGE_AFTER_COMMIT, WORKER_BEFORE_SETTLE, ALERT_BETWEEN_SEND_AND_MARK})


def _switch_on() -> bool:
    """The test switch and the arm file: both, or the hooks are inert."""
    if os.environ.get(ENV_SWITCH) != SWITCH_VALUE:
        return False
    arm = os.environ.get(ENV_ARM_FILE)
    return bool(arm) and Path(arm).is_file()  # type: ignore[arg-type]


def armed(point: str) -> bool:
    """True only with the test switch on, the arm file present and this point named."""
    return _switch_on() and os.environ.get(ENV_POINT) == point


def fault(point: str) -> None:
    """Kill this process at `point` when armed. A point that is not in `POINTS` is a mistake and raises only when the
    hooks are armed (switch and arm file), so a typo can never change behaviour outside a test."""
    if not _switch_on():
        return
    if point not in POINTS:
        raise ValueError(f"unknown fault point {point!r}")
    if os.environ.get(ENV_POINT) == point:
        os.kill(os.getpid(), signal.SIGKILL)
