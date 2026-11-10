"""RequestRepo (M04; RQ-36, RQ-35). Every state change goes through the machine table (M21) first."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from saathi.ids import ElderId, InboxId, RequestId, derive_id
from saathi.ports import Tx
from saathi.states import REQUEST, TERMINAL, Machine
from saathi.store.repos._util import affected, json_value


class RequestRepo:
    MACHINE = Machine(REQUEST, phase=0)

    @staticmethod
    async def create_first(tx: Tx, inbox_id: InboxId, elder_id: ElderId, intent: str | None) -> RequestId:
        """Create `r_<inbox>` in `new` and take R01 to `received`. A repeat finds the row and changes nothing."""
        rid = RequestId(derive_id("r", inbox_id))
        tag = await tx.execute(
            "INSERT INTO requests (id, household_id, elder_id, channel, kind, inbox_id, state, intent)"
            " SELECT $1, i.household_id, $3, i.source, i.kind, i.id, 'new', $4 FROM inbox i WHERE i.id = $2"
            " ON CONFLICT (id) DO NOTHING",
            rid,
            inbox_id,
            elder_id,
            intent,
        )
        if affected(tag) == 1:
            await RequestRepo.transition(tx, rid, "new", "lease_creates_request", "received", None)
        return rid

    @staticmethod
    async def transition(tx: Tx, id: RequestId, from_: str, event: str, to: str, reason: str | None) -> bool:  # noqa: A002
        """One ``UPDATE ... WHERE id AND state = from_``, after the machine allows the pair. Raises `ValueError`,
        with no statement sent, for a pair outside the table, a later-phase row, a wrong target or a reason the row
        does not list: those are caller bugs. False, also with no statement, for a terminal from state; False when
        the row is no longer in `from_` (another worker won the race)."""
        if from_ in TERMINAL["request"]:
            return False
        row = RequestRepo.MACHINE.lookup(from_, event)
        if row is None:
            raise ValueError(f"request machine has no row for {from_!r} on {event!r}")
        if row.to != to:
            raise ValueError(f"request machine takes {from_!r} on {event!r} to {row.to!r}, not {to!r}")
        if (reason is not None or row.reasons) and reason not in row.reasons:
            raise ValueError(f"request machine row {from_!r} on {event!r} does not list reason {reason!r}")
        tag = await tx.execute(
            "UPDATE requests SET state = $3, state_reason = $4 WHERE id = $1 AND state = $2", id, from_, to, reason
        )
        return affected(tag) == 1

    @staticmethod
    async def get(tx: Tx, id: RequestId) -> Mapping[str, Any] | None:  # noqa: A002
        rows = await tx.fetch("SELECT * FROM requests WHERE id = $1", id)
        if not rows:
            return None
        row = dict(rows[0])
        for k in ("slots", "radar"):
            if k in row:
                row[k] = json_value(row[k])
        return row
