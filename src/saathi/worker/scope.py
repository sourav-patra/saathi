"""The household a worker step is acting for, for the one adapter that needs it: the person directory (RQ-105).

`PersonDirectory.address(ref, channel)` carries no household, yet a sealed address opens only inside its household's
transaction (row-level security). The worker sets this variable where it starts work for a household, and the directory
reads it. The variable is task-local, so concurrent steps do not see each other's value.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from saathi.ids import HouseholdId

ADDRESS_SCOPE: ContextVar[HouseholdId | None] = ContextVar("address_scope", default=None)


@contextmanager
def acting_for(household: HouseholdId) -> Iterator[None]:
    token = ADDRESS_SCOPE.set(household)
    try:
        yield
    finally:
        ADDRESS_SCOPE.reset(token)
