"""Read the saved places of a household and count a ride (the worker and the caregiver bot run as the core role, which
may read `addresses` and change only `ride_count` and `last_ride_at`)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from saathi.errors import StoreUnavailable
from saathi.ids import InboxId
from saathi.ports import Tx
from saathi.rides.place import Place

_COLUMNS = "id, label, formatted, lat, lng, aliases, is_pickup, ride_count"
log = logging.getLogger(__name__)


async def optional[T](tx: Tx, work: Callable[[], Awaitable[T]], *, inbox: InboxId) -> T | None:
    """Isolate optional ride SQL in the row's transaction; connection failures and cancellation propagate."""
    await tx.execute("SAVEPOINT ride")
    try:
        settings = (
            await tx.fetch(
                "SELECT current_setting('lock_timeout') AS lock_timeout,"
                " current_setting('statement_timeout') AS statement_timeout"
            )
        )[0]
        await tx.execute("SELECT set_config('lock_timeout', '200ms', true)")
        await tx.execute("SELECT set_config('statement_timeout', '500ms', true)")
        result = await work()
        # Restore and release inside the try, so a failure here also rolls back to the savepoint only.
        await tx.execute("SELECT set_config('lock_timeout', $1, true)", settings["lock_timeout"])
        await tx.execute("SELECT set_config('statement_timeout', $1, true)", settings["statement_timeout"])
        await tx.execute("RELEASE SAVEPOINT ride")
    except StoreUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001  (a ride SQL failure must not undo required row work)
        # ROLLBACK restores the settings as well as the optional writes.
        await tx.execute("ROLLBACK TO SAVEPOINT ride")
        await tx.execute("RELEASE SAVEPOINT ride")
        log.warning("ride database failed for %s: %s", inbox, type(exc).__name__)
        return None
    return result


def _place(row: Mapping[str, Any]) -> Place:
    return Place(
        id=str(row["id"]),
        label=str(row["label"]),
        formatted=str(row["formatted"]),
        lat=row["lat"],
        lng=row["lng"],
        aliases=tuple(row["aliases"] or ()),
        is_pickup=bool(row["is_pickup"]),
        ride_count=int(row["ride_count"]),
    )


class PlaceRepo:
    @staticmethod
    async def all(tx: Tx) -> list[Place]:
        """Every place of the transaction's household, by label."""
        rows = await tx.fetch(f"SELECT {_COLUMNS} FROM addresses ORDER BY label, id")
        return [_place(r) for r in rows]

    @staticmethod
    async def one(tx: Tx, place_id: str) -> Place | None:
        rows = await tx.fetch(f"SELECT {_COLUMNS} FROM addresses WHERE id = $1", place_id)
        return _place(rows[0]) if rows else None

    @staticmethod
    async def pickup(tx: Tx) -> Place | None:
        rows = await tx.fetch(f"SELECT {_COLUMNS} FROM addresses WHERE is_pickup")
        return _place(rows[0]) if rows else None

    @staticmethod
    async def count_ride(tx: Tx, place_id: str) -> bool:
        """One more ride for this place, now. False when the place does not exist (it was removed)."""
        rows = await tx.fetch(
            "UPDATE addresses SET ride_count = ride_count + 1, last_ride_at = $2 WHERE id = $1 RETURNING id",
            place_id,
            tx.now,
        )
        return bool(rows)

    @staticmethod
    async def count_once(
        tx: Tx, place_id: str, *, source: Literal["callback", "inbox"], source_id: str, pickup_id: str | None = None
    ) -> bool:
        """Claim and count in the caller's transaction. A failure rolls back both; a retry counts nothing more."""
        claimed = await tx.fetch(
            "INSERT INTO ride_counts (household_id, source, source_id, place_id, pickup_id)"
            " SELECT $1, $2, $3, id, $5::text FROM addresses WHERE id = $4"
            " ON CONFLICT (household_id, source, source_id) DO NOTHING RETURNING place_id",
            tx.household,
            source,
            source_id,
            place_id,
            pickup_id,
        )
        if not claimed:
            return False
        if not await PlaceRepo.count_ride(tx, place_id):
            raise LookupError("ride place removed during count")
        return True

    @staticmethod
    async def callback_claim(tx: Tx, update_id: int) -> tuple[str, str | None] | None:
        rows = await tx.fetch(
            "SELECT place_id, pickup_id FROM ride_counts WHERE source = 'callback' AND source_id = $1", str(update_id)
        )
        return (rows[0]["place_id"], rows[0]["pickup_id"]) if rows else None
