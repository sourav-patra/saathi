"""Rate state (M07; RQ-81c). The edge reads only a time, from a security-definer function."""

from __future__ import annotations

from datetime import datetime

from saathi.ids import ElderId
from saathi.ports import Tx


async def rate_state(tx: Tx, elder: ElderId, now: datetime) -> datetime | None:
    """`control.rate_state`: the time the elder's queue opens again when the limit is reached, else None.

    The limit and window are household config values read inside the function. A message over the limit is still
    persisted and still scanned; only its `not_before` is set.
    """
    rows = await tx.fetch("SELECT control.rate_state($1, $2) AS not_before", now, elder)
    if not rows:
        return None
    value = rows[0]["not_before"]
    return value if isinstance(value, datetime) else None
