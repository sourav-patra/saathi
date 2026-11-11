"""Fake `PersonDirectory`: resolves a `PersonRef` to a placeholder address. Addresses here are synthetic."""

from __future__ import annotations

from fakes.base import Failable
from saathi.ids import PersonRef


class FakePersonDirectory(Failable):
    def __init__(self, addresses: dict[tuple[str, str], str] | None = None) -> None:
        super().__init__()
        self.addresses = dict(addresses or {})

    async def address(self, ref: PersonRef, channel: str) -> str:
        self._enter("address", ref, channel)
        return self.addresses[(str(ref), channel)]
