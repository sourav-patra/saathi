"""Read the saved places of a household and count a ride (the worker and the caregiver bot run as the core role, which
may read `addresses` and change only `ride_count` and `last_ride_at`)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from saathi.domain import Card
from saathi.ports import Tx
from saathi.rides.place import Place

_COLUMNS = "id, label, formatted, lat, lng, aliases, is_pickup, ride_count"


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
        tx: Tx, place_id: str, *, source: Literal["callback", "inbox"], source_id: str, card: Card | None = None
    ) -> bool:
        """Claim and count in the caller's transaction. A failure rolls back both; a retry counts nothing more."""
        claimed = await tx.fetch(
            "INSERT INTO ride_counts (household_id, source, source_id, place_id, card)"
            " SELECT $1, $2, $3, id, $5::jsonb FROM addresses WHERE id = $4"
            " ON CONFLICT (household_id, source, source_id) DO NOTHING RETURNING place_id",
            tx.household,
            source,
            source_id,
            place_id,
            card.model_dump(mode="json") if card is not None else None,
        )
        if not claimed:
            return False
        if not await PlaceRepo.count_ride(tx, place_id):
            raise LookupError("ride place removed during count")
        return True

    @staticmethod
    async def callback_card(tx: Tx, update_id: int) -> Card | None:
        rows = await tx.fetch(
            "SELECT card FROM ride_counts WHERE source = 'callback' AND source_id = $1", str(update_id)
        )
        return Card.model_validate(rows[0]["card"]) if rows else None
