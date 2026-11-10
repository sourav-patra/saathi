"""Lease helper (M12; RQ-79). One row of an elder's queue is leased at a time, by one atomic function call.

`control.lease_next` takes an advisory lock per elder and skips a locked one, so two workers never hold rows of the same
elder and never wait for each other (LT-09). The lease lasts `lease_s`; `LeaseRenewer` pushes it forward every third of
that while a step runs. `holds` re-checks the token and locks the row for the rest of the transaction, so a worker that
lost its lease writes nothing (a stalled worker finds `LeaseLost` at its next transaction).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from saathi.domain import Lease
from saathi.errors import SaathiError
from saathi.ids import HouseholdId
from saathi.ports import Clock, Tx
from saathi.store import Store
from saathi.store.repos import InboxRepo

log = logging.getLogger(__name__)

LEASE_S = 60


class LeaseLost(SaathiError):
    """The inbox row is no longer leased to this token: it expired and was taken, or it was settled."""


async def lease_next(store: Store, clock: Clock, *, lease_s: int = LEASE_S) -> Lease | None:
    """The oldest due row of some elder, leased to this caller; None when nothing is due."""
    return await store.scan.lease_next(clock.now(), lease_s)


async def holds(tx: Tx, lease: Lease) -> Mapping[str, Any]:
    """The leased inbox row, locked until the transaction ends. `LeaseLost` when the token no longer holds it."""
    rows = await tx.fetch(
        "SELECT elder_id, source, kind, media_ref, media_mime, received_at, attempts FROM inbox"
        " WHERE id = $1 AND lease_token = $2 AND state = 'leased' FOR UPDATE",
        lease.inbox_id,
        lease.token,
    )
    if not rows:
        raise LeaseLost()
    return rows[0]


class LeaseRenewer:
    """Renews the lease every `lease_s / 3` while the block runs. A refused renewal sets `lost`; the block's own
    transactions then fail on `holds` before they write anything."""

    def __init__(
        self, store: Store, lease: Lease, clock: Clock, *, lease_s: int = LEASE_S, interval_s: float | None = None
    ) -> None:
        self._store = store
        self._lease = lease
        self._clock = clock
        self._lease_s = lease_s
        self._interval_s = interval_s if interval_s is not None else lease_s / 3
        self._task: asyncio.Task[None] | None = None
        self.lost = False
        self.renewals = 0

    async def __aenter__(self) -> Lease:
        self._task = asyncio.create_task(self._renew_loop())
        return self._lease

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _renew_loop(self) -> None:
        household = HouseholdId(self._lease.household_id)
        while not self.lost:
            await asyncio.sleep(self._interval_s)
            try:
                until = self._clock.now() + timedelta(seconds=self._lease_s)
                async with self._store.tx(household=household) as tx:
                    ok = await InboxRepo.renew(tx, self._lease, until)
            except Exception as exc:  # noqa: BLE001  (a store hiccup is retried at the next beat)
                log.warning("lease renewal failed: %s", type(exc).__name__)
                continue
            if ok:
                self.renewals += 1
            else:
                self.lost = True
