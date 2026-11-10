"""A store with no database: it enforces the household rule (RQ-109) and records statements."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fakes.base import Failable
from saathi.clock import FakeClock
from saathi.errors import KeyMissing, TenantNotSet
from saathi.ids import HouseholdId, PublicKey
from saathi.ports import Tx


@dataclass
class FakeTx:
    household: HouseholdId
    now: datetime
    statements: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    rows: list[Mapping[str, Any]] = field(default_factory=list)

    async def execute(self, sql: str, *args: Any) -> str:
        self.statements.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Mapping[str, Any]]:
        self.statements.append((sql, args))
        return list(self.rows)


class FakeStore(Failable):
    def __init__(self, clock: FakeClock | None = None) -> None:
        super().__init__()
        self.clock = clock or FakeClock()
        self.transactions: list[FakeTx] = []
        self.locked: list[HouseholdId] = []

    @asynccontextmanager
    async def tx(self, *, household: HouseholdId, lock_household: bool = False) -> AsyncIterator[Tx]:
        self._enter("tx", household=household, lock_household=lock_household)
        if not household:
            raise TenantNotSet()
        if lock_household:
            self.locked.append(household)
        tx = FakeTx(household=household, now=self.clock.now())
        self.transactions.append(tx)
        yield tx


class FakeSealer:
    """Reversible stand-in for sealing: the blob is bound to its aad, as the real one is."""

    def seal(self, public: PublicKey, plaintext: bytes, *, aad: bytes) -> bytes:
        return b"sealed:" + aad + b":" + plaintext


class FakeOpener(Failable):
    def __init__(self) -> None:
        super().__init__()
        self.keys: set[str] = set()

    async def open(self, tx: Tx, blob: bytes, key_id: str, *, aad: bytes) -> bytes:
        self._enter("open", tx, blob, key_id, aad=aad)
        if key_id not in self.keys:
            raise KeyMissing()
        prefix = b"sealed:" + aad + b":"
        if not blob.startswith(prefix):
            raise KeyMissing()
        return blob[len(prefix) :]
