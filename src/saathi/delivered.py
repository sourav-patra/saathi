"""Sends that count a repeated key as delivered (WU-08 rulings; RQ-79).

A platform may deliver twice after a crash and a channel refuses a key it has already sent with
`SendRejected("duplicate_key")`. For the worker that refusal means the first send reached the person, so the effect
is done. These wrappers return a receipt for it; every other error goes up unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any

from saathi.domain import (
    AudioBlob,
    Card,
    CardRef,
    ChannelCap,
    ChannelHealth,
    ImageBlob,
    MediaBlob,
    MediaRef,
    SendReceipt,
    VoiceClip,
)
from saathi.errors import SendRejected
from saathi.ids import ApproverId, IdemKey, PersonRef, TemplateId
from saathi.ports import CaregiverChannel, ElderChannel

DUPLICATE = "duplicate_key"


async def _once(call: Awaitable[SendReceipt], key: IdemKey) -> SendReceipt:
    try:
        return await call
    except SendRejected as exc:
        if exc.code != DUPLICATE:
            raise
        return SendReceipt(provider_id=DUPLICATE, key=key)


class DeliveredElderChannel:
    """An `ElderChannel` whose repeated keys count as delivered."""

    def __init__(self, inner: ElderChannel) -> None:
        self._inner = inner

    @property
    def kind(self) -> Any:
        return self._inner.kind

    @property
    def capabilities(self) -> frozenset[ChannelCap]:
        return self._inner.capabilities

    def note_inbound(self, person: PersonRef, at: Any) -> None:
        """Pass the elder's last message time to a channel that tracks the 24 hour window (WhatsApp)."""
        note = getattr(self._inner, "note_inbound", None)
        if note is not None:
            note(person, at)

    async def send_text(self, to: PersonRef, text: str, *, key: IdemKey) -> SendReceipt:
        return await _once(self._inner.send_text(to, text, key=key), key)

    async def send_audio(self, to: PersonRef, clip: VoiceClip, *, key: IdemKey) -> SendReceipt:
        return await _once(self._inner.send_audio(to, clip, key=key), key)

    async def send_image(self, to: PersonRef, image: ImageBlob, caption: str, *, key: IdemKey) -> SendReceipt:
        return await _once(self._inner.send_image(to, image, caption, key=key), key)

    async def send_template(
        self, to: PersonRef, template: TemplateId, params: Mapping[str, str], *, key: IdemKey
    ) -> SendReceipt:
        return await _once(self._inner.send_template(to, template, params, key=key), key)

    async def fetch_media(self, ref: MediaRef) -> MediaBlob:
        return await self._inner.fetch_media(ref)

    async def health(self) -> ChannelHealth:
        return await self._inner.health()


class DeliveredCaregiverChannel:
    """A `CaregiverChannel` whose repeated keys count as delivered."""

    def __init__(self, inner: CaregiverChannel) -> None:
        self._inner = inner

    async def send_card(self, to: ApproverId, card: Card, *, key: IdemKey) -> CardRef:
        try:
            return await self._inner.send_card(to, card, key=key)
        except SendRejected as exc:
            if exc.code != DUPLICATE:
                raise
            return CardRef(approver_id=to, message_ref=DUPLICATE)

    async def edit_card(self, ref: CardRef, card: Card) -> None:
        await self._inner.edit_card(ref, card)

    async def send_text(self, to: ApproverId, text: str, *, key: IdemKey) -> SendReceipt:
        return await _once(self._inner.send_text(to, text, key=key), key)

    async def send_audio(
        self, to: ApproverId, audio: AudioBlob | VoiceClip, caption: str, *, key: IdemKey
    ) -> SendReceipt:
        return await _once(self._inner.send_audio(to, audio, caption, key=key), key)

    def updates(self):  # type: ignore[no-untyped-def]
        return self._inner.updates()

    async def health(self) -> ChannelHealth:
        return await self._inner.health()
