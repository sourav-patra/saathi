"""A caregiver's tap on a place choice (`ride:<place id>`): count the ride and return the known-place card."""

from __future__ import annotations

from saathi.domain import Card
from saathi.ids import HouseholdId
from saathi.ports import Store
from saathi.rides.card import known_card
from saathi.rides.repo import PlaceRepo


class PgRideTaps:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def tap(self, household: HouseholdId, place_id: str) -> Card | None:
        """The card to show instead of the choice card, or None when the place is gone. Counts the ride."""
        async with self._store.tx(household=household) as tx:
            drop = await PlaceRepo.one(tx, place_id)
            if drop is None or drop.is_pickup:
                return None
            await PlaceRepo.count_ride(tx, place_id)
            return known_card(drop, await PlaceRepo.pickup(tx))
