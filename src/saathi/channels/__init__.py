"""Channel implementations. Platform names live only in the modules here and in `edge/` (RQ-105)."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from saathi.errors import SaathiError, SendRejected
from saathi.ids import IdemKey, PersonRef

__all__ = ["ChannelParseError", "PersonDirectory", "SentKeys"]

SENT_KEYS_LIMIT = 4096


class ChannelParseError(SaathiError):
    """A webhook body or update whose envelope is not the shape the platform documents."""


class PersonDirectory(Protocol):
    """Resolves a `PersonRef` to a platform address. Implemented in the core, which holds the private key."""

    async def address(self, ref: PersonRef, channel: str) -> str: ...


class SentKeys:
    """Bounded memory of idempotency keys already delivered, with a per-key lock.

    `claim(key)` serialises sends that share a key: a second caller waits, then finds the key recorded and gets
    `SendRejected("duplicate_key")`. A send that failed records nothing, so the waiter may then try. Durable
    de-duplication stays with the effects table; this only stops a double delivery inside one process.
    """

    def __init__(self, limit: int = SENT_KEYS_LIMIT) -> None:
        self._limit = limit
        self._done: OrderedDict[IdemKey, None] = OrderedDict()
        self._locks: dict[IdemKey, list] = {}

    def __len__(self) -> int:
        return len(self._done)

    def __contains__(self, key: object) -> bool:
        return key in self._done

    @asynccontextmanager
    async def claim(self, key: IdemKey) -> AsyncIterator[None]:
        entry = self._locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                if key in self._done:
                    raise SendRejected("duplicate_key")
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self._locks[key]

    def record(self, key: IdemKey) -> None:
        self._done[key] = None
        self._done.move_to_end(key)
        while len(self._done) > self._limit:
            self._done.popitem(last=False)
