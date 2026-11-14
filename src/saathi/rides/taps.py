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

    async def tap(self, household: HouseholdId, place_id: str, *, update_id: int) -> Card | None:
        """The card to show instead of the choice card, or None when the place is gone. Counts the ride."""
        async with self._store.tx(household=household) as tx:
            claim = await PlaceRepo.callback_claim(tx, update_id)
            if claim is None:
                pickup = await PlaceRepo.pickup(tx)
                await PlaceRepo.count_once(
                    tx, place_id, source="callback", source_id=str(update_id),
                    pickup_id=pickup.id if pickup is not None else None,
                )  # fmt: skip
                # A concurrent replay may have claimed this update first. Use the winner's place ids.
                claim = await PlaceRepo.callback_claim(tx, update_id)
            if claim is None:
                return None
            drop = await PlaceRepo.one(tx, claim[0])
            if drop is None:
                return None
            pickup = await PlaceRepo.one(tx, claim[1]) if claim[1] is not None else None
            return known_card(drop, pickup)
