"""A store with no database: it enforces the household rule (RQ-109) and records statements."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fakes.base import Failable
from saathi.clock import FakeClock
from saathi.errors import KeyMissing, TenantNotSet
from saathi.ids import HouseholdId, PublicKey
from saathi.ports import Tx

DEFAULT_TAG = "INSERT 0 1"


class FakeTx(Failable):
    """Records statements. `execute` returns the next scripted command tag, else `default_tag`; it can fail."""

    def __init__(
        self,
        household: HouseholdId,
        now: datetime,
        *,
        tags: Iterable[str] = (),
        default_tag: str = DEFAULT_TAG,
    ) -> None:
        super().__init__()
        self.household = household
        self.now = now
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[Mapping[str, Any]] = []
        self.tags: deque[str] = deque(tags)
        self.default_tag = default_tag

    async def execute(self, sql: str, *args: Any) -> str:
        self._enter("execute", sql, *args)
        self.statements.append((sql, args))
        return self.tags.popleft() if self.tags else self.default_tag

    async def fetch(self, sql: str, *args: Any) -> list[Mapping[str, Any]]:
        self._enter("fetch", sql, *args)
        self.statements.append((sql, args))
        return list(self.rows)


class FakeStore(Failable):
    def __init__(self, clock: FakeClock | None = None, *, default_tag: str = DEFAULT_TAG) -> None:
        super().__init__()
        self.clock = clock or FakeClock()
        self.default_tag = default_tag  # the tag `execute` returns in each transaction
        self.tags: deque[str] = deque()  # scripted tags, taken by the next statements of any transaction
        self.transactions: list[FakeTx] = []
        self.locked: list[HouseholdId] = []

    @asynccontextmanager
    async def tx(self, *, household: HouseholdId, lock_household: bool = False) -> AsyncIterator[Tx]:
        self._enter("tx", household=household, lock_household=lock_household)
        if not household:
            raise TenantNotSet()
        if lock_household:
            self.locked.append(household)
        tx = FakeTx(household=household, now=self.clock.now(), default_tag=self.default_tag)
        tx.tags = self.tags
        self.transactions.append(tx)
        yield tx


class FakeSealer(Failable):
    """Reversible stand-in for sealing: the blob is bound to its aad, as the real one is."""

    def seal(self, public: PublicKey, plaintext: bytes, *, aad: bytes) -> bytes:
        self._enter("seal", public, plaintext, aad=aad)
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
