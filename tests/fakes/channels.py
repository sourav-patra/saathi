"""Fake elder and caregiver channels.

By default (`strict_keys=True`, LLD build part, rule on idempotency) a repeated idempotency key is refused with
`SendRejected("duplicate_key")` and sends nothing. With `strict_keys=False` it returns the first receipt, as a
platform that deduplicates would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Literal

from fakes.base import Failable
from saathi.domain import (
    ApproverUpdate,
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
from saathi.errors import MediaExpired, ProactiveNotAllowed, SendRejected
from saathi.ids import ApproverId, IdemKey, PersonRef, TemplateId

ALL_CAPS = frozenset(ChannelCap)


class FakeElderChannel(Failable):
    def __init__(
        self,
        kind: Literal["whatsapp", "telegram_elder"] = "whatsapp",
        capabilities: frozenset[ChannelCap] = ALL_CAPS,
        *,
        strict_keys: bool = True,
    ) -> None:
        super().__init__()
        self.strict_keys = strict_keys
        self.kind = kind
        self.capabilities = capabilities
        self.window_open = True  # False: free-form sends raise ProactiveNotAllowed, templates still go
        self.channel_health = ChannelHealth.UP
        self.media: dict[str, MediaBlob] = {}
        self.sent: list[tuple[str, PersonRef, object, IdemKey]] = []
        self._receipts: dict[IdemKey, SendReceipt] = {}

    def _send(self, method: str, to: PersonRef, payload: object, key: IdemKey, *, free_form: bool) -> SendReceipt:
        self._enter(method, to, payload, key=key)
        if key in self._receipts:
            if self.strict_keys:
                raise SendRejected("duplicate_key")
            return self._receipts[key]
        if free_form and not self.window_open:
            raise ProactiveNotAllowed("window_closed")
        receipt = SendReceipt(provider_id=f"fake-{len(self.sent) + 1}", key=key)
        self._receipts[key] = receipt
        self.sent.append((method, to, payload, key))
        return receipt

    async def send_text(self, to: PersonRef, text: str, *, key: IdemKey) -> SendReceipt:
        return self._send("send_text", to, text, key, free_form=True)

    async def send_audio(self, to: PersonRef, clip: VoiceClip, *, key: IdemKey) -> SendReceipt:
        return self._send("send_audio", to, clip, key, free_form=True)

    async def send_image(self, to: PersonRef, image: ImageBlob, caption: str, *, key: IdemKey) -> SendReceipt:
        return self._send("send_image", to, (image, caption), key, free_form=True)

    async def send_template(
        self, to: PersonRef, template: TemplateId, params: Mapping[str, str], *, key: IdemKey
    ) -> SendReceipt:
        return self._send("send_template", to, (template, dict(params)), key, free_form=False)

    async def fetch_media(self, ref: MediaRef) -> MediaBlob:
        self._enter("fetch_media", ref)
        if ref.source.value != self.kind:
            raise SendRejected("wrong_source")
        if ref.handle not in self.media:
            raise MediaExpired()
        return self.media[ref.handle]

    async def health(self) -> ChannelHealth:
        self._enter("health")
        return self.channel_health


class FakeCaregiverChannel(Failable):
    def __init__(self, *, strict_keys: bool = True) -> None:
        super().__init__()
        self.strict_keys = strict_keys
        self.channel_health = ChannelHealth.UP
        self.cards: dict[CardRef, Card] = {}
        self.sent: list[tuple[str, ApproverId, object, IdemKey]] = []
        self._queue: list[ApproverUpdate] = []
        self._receipts: dict[IdemKey, SendReceipt | CardRef] = {}

    def push_update(self, update: ApproverUpdate) -> None:
        self._queue.append(update)

    async def send_card(self, to: ApproverId, card: Card, *, key: IdemKey) -> CardRef:
        self._enter("send_card", to, card, key=key)
        known = self._receipts.get(key)
        if known is not None and self.strict_keys:
            raise SendRejected("duplicate_key")
        if isinstance(known, CardRef):
            return known
        ref = CardRef(approver_id=to, message_ref=f"card-{len(self.cards) + 1}")
        self._receipts[key] = ref
        self.cards[ref] = card
        self.sent.append(("send_card", to, card, key))
        return ref

    async def edit_card(self, ref: CardRef, card: Card) -> None:
        self._enter("edit_card", ref, card)
        self.cards[ref] = card

    def _send(self, method: str, to: ApproverId, payload: object, key: IdemKey) -> SendReceipt:
        self._enter(method, to, payload, key=key)
        known = self._receipts.get(key)
        if known is not None and self.strict_keys:
            raise SendRejected("duplicate_key")
        if isinstance(known, SendReceipt):
            return known
        receipt = SendReceipt(provider_id=f"cg-{len(self.sent) + 1}", key=key)
        self._receipts[key] = receipt
        self.sent.append((method, to, payload, key))
        return receipt

    async def send_text(self, to: ApproverId, text: str, *, key: IdemKey) -> SendReceipt:
        return self._send("send_text", to, text, key)

    async def send_audio(
        self, to: ApproverId, audio: AudioBlob | VoiceClip, caption: str, *, key: IdemKey
    ) -> SendReceipt:
        return self._send("send_audio", to, (audio, caption), key)

    def updates(self) -> AsyncIterator[ApproverUpdate]:
        self._enter("updates")
        return self._drain()

    async def _drain(self) -> AsyncIterator[ApproverUpdate]:
        while self._queue:
            yield self._queue.pop(0)

    async def health(self) -> ChannelHealth:
        self._enter("health")
        return self.channel_health
