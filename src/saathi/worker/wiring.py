"""The platform-free parts of the worker composition (M12; RQ-105): the person directory and the caregiver stand-in.

The adapters that name a platform, and `build_worker` that wires everything from the settings, live in
`saathi/channels/worker_main.py`, the only place besides `edge/` allowed to import a platform client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from saathi.domain import ApproverUpdate, AudioBlob, Card, VoiceClip
from saathi.errors import ChannelDown
from saathi.ids import ApproverId, IdemKey, PersonRef
from saathi.ports import CardRef, ChannelHealth, SendReceipt
from saathi.store import Store
from saathi.store.keys import KeyRing
from saathi.store.repos import IdentityRepo
from saathi.store.repos._util import aad
from saathi.worker.scope import ADDRESS_SCOPE


class CoreDirectory:
    """Implements `PersonDirectory` in the core, the only place that holds the key that opens a sealed address."""

    def __init__(self, store: Store, keys: KeyRing) -> None:
        self._store = store
        self._keys = keys

    async def address(self, ref: PersonRef, channel: str) -> str:
        household = ADDRESS_SCOPE.get()
        if household is None:
            raise LookupError("no household is in scope for this address")
        async with self._store.tx(household=household) as tx:
            sealed = await IdentityRepo.sealed_identity(tx, ref)
            if sealed is None:
                raise LookupError("no active identity")
            identity_id, blob, key_id = sealed
            plain = await self._keys.open(tx, blob, key_id, aad=aad(household, identity_id, "ref"))
        return plain.decode()


class PendingCaregiverChannel:
    """Stands in until the caregiver bot adapter exists (WU-18). Every send fails loudly with `ChannelDown`, so the
    worker retries and the owner is told; nothing is dropped silently and nothing is sent."""

    async def send_card(self, to: ApproverId, card: Card, *, key: IdemKey) -> CardRef:
        raise ChannelDown()

    async def edit_card(self, ref: CardRef, card: Card) -> None:
        raise ChannelDown()

    async def send_text(self, to: ApproverId, text: str, *, key: IdemKey) -> SendReceipt:
        raise ChannelDown()

    async def send_audio(
        self, to: ApproverId, audio: AudioBlob | VoiceClip, caption: str, *, key: IdemKey
    ) -> SendReceipt:
        raise ChannelDown()

    def updates(self) -> AsyncIterator[ApproverUpdate]:
        raise ChannelDown()

    async def health(self) -> ChannelHealth:
        return ChannelHealth.DOWN


__all__ = ["CoreDirectory", "PendingCaregiverChannel"]
