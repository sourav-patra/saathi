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
            previous = await PlaceRepo.callback_card(tx, update_id)
            if previous is not None:
                return previous
            drop = await PlaceRepo.one(tx, place_id)
            if drop is None:
                return None
            card = known_card(drop, await PlaceRepo.pickup(tx))
            await PlaceRepo.count_once(tx, place_id, source="callback", source_id=str(update_id), card=card)
            # A concurrent replay may have claimed this update first. Always edit to the original card.
            return await PlaceRepo.callback_card(tx, update_id)
