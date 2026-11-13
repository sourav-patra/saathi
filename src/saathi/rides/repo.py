"""Read the saved places of a household and count a ride (the worker and the caregiver bot run as the core role, which
may read `addresses` and change only `ride_count` and `last_ride_at`)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
