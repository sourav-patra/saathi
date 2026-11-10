"""The consent gate at each enforcement point (M06; PL-05, PL-33, PL-34).

The worker before a request (R01 guard), the alert audio attachment, "show raw" and hosted routes call `gate`. It fails
closed: no row, a withdrawn use, a wrong recipient, an unknown capacity or an unreadable store all answer False, and it
never raises on a missing row. A False means the request takes the summary path (no hosted call, no audio attached).
"""

from __future__ import annotations

from saathi.errors import StoreUnavailable
from saathi.ports import Tx
from saathi.store.repos.consents import ConsentEvent, ConsentRepo

__all__ = ["ConsentEvent", "gate"]


async def gate(tx: Tx, use: str, recipient: str, *, subject: str = "elder") -> bool:
    try:
        return await ConsentRepo.allowed(tx, subject, use, recipient)
    except StoreUnavailable:
        return False
