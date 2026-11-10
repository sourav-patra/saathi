"""Protocol ports (M03; TRD ports section 2 and LLD intake M03).

Every side effect sits behind one of these so that machines and graphs run with the fakes in
`tests/fakes/`. A port raises only the errors listed for it in `saathi.errors.PORT_ERRORS`; any
other exception ends the run `failed`, reason `no_result` (RQ-35, RQ-36). Every port that sends or
writes takes an `IdemKey` built from the inbox id, attempt id or commit_intent id (RQ-79).

Not here yet: `CommerceAdapter` and `PhoneDevice` are Phase 1 ports (their errors exist already),
and the repo attributes of `Store` (inbox, requests, alerts and the rest) arrive with the store in
WU-06, which defines the repo protocols they need.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from saathi.domain import (
    AlertEventRef,
    ApproverUpdate,
    AttemptResult,
    AudioBlob,
    AuditEvent,
    AuditRef,
    Card,
    CardRef,
    ChannelCap,
    ChannelHealth,
    ClassifierFlag,
    ImageBlob,
    MediaBlob,
    MediaRef,
    ModelRequest,
    ModelResult,
    SendReceipt,
    SpanValue,
    Transcript,
    VisionResult,
    VisionTask,
    VoiceAckResult,
    VoiceCallback,
    VoiceClip,
    WavBlob,
)
from saathi.ids import ApproverId, HouseholdId, IdemKey, PersonRef, PublicKey, ReplyKey, RunId, TemplateId, VoiceId


@runtime_checkable
class ElderChannel(Protocol):
    """WhatsApp primary, Telegram elder bot backup. Core code holds a `PersonRef`, never an address (RQ-105)."""

    kind: Literal["whatsapp", "telegram_elder"]
    capabilities: frozenset[ChannelCap]

    async def send_text(self, to: PersonRef, text: str, *, key: IdemKey) -> SendReceipt: ...

    async def send_audio(self, to: PersonRef, clip: VoiceClip, *, key: IdemKey) -> SendReceipt: ...

    async def send_image(self, to: PersonRef, image: ImageBlob, caption: str, *, key: IdemKey) -> SendReceipt: ...

    async def send_template(
        self, to: PersonRef, template: TemplateId, params: Mapping[str, str], *, key: IdemKey
    ) -> SendReceipt: ...

    async def fetch_media(self, ref: MediaRef) -> MediaBlob: ...

    async def health(self) -> ChannelHealth: ...


@runtime_checkable
class CaregiverChannel(Protocol):
    """The Telegram caregiver bot, a different bot from the elder's."""

    async def send_card(self, to: ApproverId, card: Card, *, key: IdemKey) -> CardRef: ...

    async def edit_card(self, ref: CardRef, card: Card) -> None: ...

    async def send_text(self, to: ApproverId, text: str, *, key: IdemKey) -> SendReceipt: ...

    async def send_audio(
        self, to: ApproverId, audio: AudioBlob | VoiceClip, caption: str, *, key: IdemKey
    ) -> SendReceipt: ...

    def updates(self) -> AsyncIterator[ApproverUpdate]: ...

    async def health(self) -> ChannelHealth: ...


@runtime_checkable
class Escalator(Protocol):
    """One implementation per stage: a Telegram, b WhatsApp template, c voice call."""

    stage: Literal["a", "b", "c"]

    async def notify(self, event: AlertEventRef, approver: ApproverId, *, key: IdemKey) -> AttemptResult: ...

    async def on_dtmf(self, cb: VoiceCallback) -> VoiceAckResult: ...


@runtime_checkable
class ASR(Protocol):
    async def transcribe(self, audio: AudioBlob, *, hint: str = "hi") -> Transcript: ...


@runtime_checkable
class AudioCodec(Protocol):
    async def to_wav16k(self, audio: AudioBlob, *, max_seconds: float) -> WavBlob: ...

    async def encode_clip(self, wav: WavBlob) -> VoiceClip: ...


@runtime_checkable
class TTS(Protocol):
    async def synthesize(self, text: str, *, voice: VoiceId, max_seconds: float) -> VoiceClip: ...


@runtime_checkable
class VoiceStore(Protocol):
    """Fixed clips (D-69). `fixed` checks the stored sha256 before it returns."""

    async def fixed(self, key: ReplyKey, language: str = "hi") -> VoiceClip: ...


@runtime_checkable
class Vision(Protocol):
    async def read(self, image: ImageBlob, task: VisionTask, *, run: RunId) -> VisionResult: ...


@runtime_checkable
class Gateway(Protocol):
    """The only model client."""

    async def complete[T: BaseModel](self, req: ModelRequest[T]) -> ModelResult[T]: ...


@runtime_checkable
class AuditSink(Protocol):
    async def append(self, ev: AuditEvent, *, tx: Tx | None = None) -> AuditRef: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    async def sleep_until(self, when: datetime) -> None: ...


@runtime_checkable
class Tx(Protocol):
    """What a repo needs from a transaction. The household is set before any statement (RQ-109)."""

    household: HouseholdId
    now: datetime

    async def execute(self, sql: str, *args: Any) -> str: ...

    async def fetch(self, sql: str, *args: Any) -> list[Mapping[str, Any]]: ...


@runtime_checkable
class Store(Protocol):
    def tx(self, *, household: HouseholdId, lock_household: bool = False) -> AbstractAsyncContextManager[Tx]:
        """`household` is required with no default; the first statement is SET LOCAL app.household_id."""
        ...


@runtime_checkable
class Classifier(Protocol):
    """G1 local classifier. Phase 0 has a fake only."""

    async def flags(self, text: str, *, run: RunId) -> list[ClassifierFlag]: ...


@runtime_checkable
class TraceExporter(Protocol):
    def span(self, name: str, **fields: SpanValue) -> None:
        """Never raises and never blocks the caller."""
        ...


@runtime_checkable
class Sealer(Protocol):
    """Implemented by KeyRing (M05); repos take it so the store does not import the key module."""

    def seal(self, public: PublicKey, plaintext: bytes, *, aad: bytes) -> bytes: ...


@runtime_checkable
class Opener(Protocol):
    async def open(self, tx: Tx, blob: bytes, key_id: str, *, aad: bytes) -> bytes: ...


@runtime_checkable
class MediaStore(Protocol):
    async def put(self, ref: MediaRef, blob: MediaBlob, *, household: HouseholdId) -> str:
        """Returns a local path key."""
        ...

    async def get(self, key: str) -> MediaBlob: ...
