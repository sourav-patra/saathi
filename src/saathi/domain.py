"""Core domain types shared across channels, the worker, the alert path and the policy engine (M01).

Multi-household from day 1: every record that names an elder, a request or an event also names its
`household_id` (RQ-109). Core types hold no phone number, chat id or platform client (RQ-105): the
elder is reached through an opaque `PersonRef`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from saathi.clock import SystemClock
from saathi.ids import (
    ApproverId,
    ElderId,
    EventId,
    HouseholdId,
    IdemKey,
    InboxId,
    PersonRef,
    ReplyKey,
    RunId,
    TemplateId,
    VoiceId,
    derive_id,
    idem_key,
    new_id,
)

__all__ = [
    "ApproverId", "ElderId", "EventId", "HouseholdId", "IdemKey", "InboxId", "PersonRef", "ReplyKey",
    "RunId", "TemplateId", "VoiceId", "derive_id", "idem_key", "new_id",
]  # fmt: skip


def utcnow() -> datetime:
    return SystemClock().now()


class DataClass(StrEnum):
    """PL-01: the four data classes."""

    RESTRICTED = "restricted"
    PERSONAL = "personal"
    PUBLIC = "public"
    SYNTHETIC = "synthetic"


class Sensitivity(StrEnum):
    """M19: what a gateway request carries. Elder content is PERSONAL; only PUBLIC work may use a free tier."""

    PERSONAL = "personal"
    PUBLIC = "public"


class Grade(IntEnum):
    """M19: the model grade that served a request."""

    G0 = 0
    G1 = 1
    G2 = 2
    G3 = 3
    G4 = 4


class Source(StrEnum):
    """Where an inbound message came from. Replaces the old `Channel` enum."""

    WHATSAPP = "whatsapp"
    TELEGRAM_ELDER = "telegram_elder"
    CAREGIVER_FORWARD = "caregiver_forward"


class MediaKind(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    UNSUPPORTED = "unsupported"  # sticker, contact, location (inbox state `unsupported`)


class ChannelCap(StrEnum):
    VOICE_IN = "voice_in"
    VOICE_OUT = "voice_out"
    IMAGE_IN = "image_in"
    IMAGE_OUT = "image_out"
    VIDEO_IN = "video_in"
    TEXT = "text"
    PROACTIVE_FREE = "proactive_free"
    TEMPLATES = "templates"


class ChannelHealth(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


class AttemptResult(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    ANSWERED_NO_ACK = "answered_no_ack"  # stage c: call answered, no key press (A13)
    VOICEMAIL = "voicemail"


class VisionTask(StrEnum):
    OCR = "ocr"
    LABEL = "label"


# --------------------------------------------------------------------------- #
# Blobs and small port values. All frozen: a port never mutates what it is given.
# --------------------------------------------------------------------------- #
class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class MediaRef(_Frozen):
    source: Source
    handle: str
    mime: str | None = None


class MediaBlob(_Frozen):
    data: bytes
    mime: str


class AudioBlob(_Frozen):
    data: bytes
    mime: str = "audio/ogg"


class WavBlob(_Frozen):
    data: bytes


class ImageBlob(_Frozen):
    data: bytes
    mime: str = "image/jpeg"


class VoiceClip(_Frozen):
    """A stored, hash-checked clip (D-69)."""

    key: ReplyKey
    language: str = "hi"
    data: bytes
    sha256: str
    version: int = 1


class DataBlock(_Frozen):
    """External text (transcript, OCR, web, screen) carried as data, never as instructions."""

    text: str
    origin: str


class Transcript(_Frozen):
    text: str
    language: str | None = None
    confidence: float | None = None
    engine: str = "unknown"


class SendReceipt(_Frozen):
    provider_id: str
    key: IdemKey


class Button(_Frozen):
    """One card button: a callback (`data`) or a link (`url`), never both and never neither."""

    text: str
    data: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _one_target(self) -> Button:
        if (self.data is None) == (self.url is None):
            raise ValueError("a button has exactly one of data and url")
        return self


class Card(_Frozen):
    """A caregiver card built from slots (enums and counts), never from model prose (M15)."""

    title: str
    lines: list[str] = Field(default_factory=list)
    buttons: list[Button] = Field(default_factory=list)


class CardRef(_Frozen):
    approver_id: ApproverId
    message_ref: str


class ApproverUpdate(_Frozen):
    """A callback or message from a registered approver; an update id is seen at most once."""

    update_id: int
    approver_id: ApproverId
    kind: Literal["callback", "text"]
    data: str = ""
    nonce: str | None = None


class AlertEventRef(_Frozen):
    household_id: HouseholdId
    event_id: EventId
    kind: Literal["emergency", "scam", "unscanned"]


class VoiceCallback(_Frozen):
    call_id: str
    digit: str
    signature: str
    timestamp: datetime


class VoiceAckResult(_Frozen):
    approver_id: ApproverId


class VisionResult(_Frozen):
    ocr_text: DataBlock
    labels: list[str] = Field(default_factory=list)
    refusal: bool = False


class Message(_Frozen):
    role: Literal["system", "user", "assistant"]
    content: str | DataBlock


class ModelRequest[T: BaseModel](BaseModel):
    """What the gateway takes (M19). Elder content is personal by default (PL-02)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    step: str
    messages: list[Message]
    schema_: type[T]
    run: RunId
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    intent: str | None = None
    media: tuple[MediaRef, ...] = ()


class ModelResult[T: BaseModel](BaseModel):
    value: T | None = None
    refusal: bool = False
    refusal_reason: str | None = None
    grade: Grade
    tokens: int = 0
    cost_inr: Decimal = Decimal(0)
    cached: bool = False


Actor = Annotated[str, StringConstraints(pattern=r"^(elder|approver:[\w-]+|system:[\w-]+|agent:[\w-]+)$")]
SafeValue = int | bool | Annotated[str, StringConstraints(pattern=r"^[\w:.-]{0,64}$")]


class AuditEvent(_Frozen):
    """M17. `detail` holds only safe values; `sensitive` holds raw values of which only a keyed hash is stored."""

    household: HouseholdId
    run_id: RunId
    actor: Actor
    event: str
    subject: str | None = None
    detail: Mapping[str, SafeValue] = Field(default_factory=dict)
    sensitive: Mapping[str, str] = Field(default_factory=dict)


class AuditRef(_Frozen):
    id: str


class ClassifierFlag(_Frozen):
    kind: str
    score: float


SpanValue = str | int | float | bool | None


# --------------------------------------------------------------------------- #
# Inbox and worker types (LLD intake M01)
# --------------------------------------------------------------------------- #
class InboundMessage(BaseModel):
    """Parsed by a channel, never stored whole. `sender_ref` is the platform sender id, hashed before it is stored."""

    source: Source
    provider_message_id: str
    sender_ref: str
    kind: MediaKind
    text: str | None = None
    media: MediaRef | None = None
    received_at: datetime


class RawPart(BaseModel):
    part: Literal["text", "caption", "transcript"]
    text: str


class NewInbox(BaseModel):
    """One inbox row and its parts, inserted in one transaction."""

    id: InboxId
    household_id: HouseholdId
    elder_id: ElderId
    msg: InboundMessage
    sender_hmac: str
    payload_sha256: str
    not_before: datetime | None = None
    parts: list[RawPart] = Field(default_factory=list)


class Lease(BaseModel):
    household_id: HouseholdId
    inbox_id: InboxId
    elder_id: ElderId
    token: UUID


class AlertOpen(BaseModel):
    kind: Literal["emergency", "scam", "unscanned"]
    origin: str
    rule_ids: list[str] = Field(default_factory=list)


SummaryFlag = Literal["none", "asr_failed", "media_lost", "consent_missing", "unsupported"]


class SummarySlots(BaseModel):
    """What the caregiver summary says, as enums, flags and rule ids only. No field can hold transcript text (RQ-97)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: MediaKind
    intent: str = "unknown"  # an `Intent` value; anything else is stored as `unknown`
    emergency: bool = False
    scam: bool = False
    rule_ids: tuple[str, ...] = ()
    transcribed: bool = False
    audio_attached: bool = False
    flag: SummaryFlag = "none"

    @field_validator("rule_ids")
    @classmethod
    def _rule_ids_only(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not all(re.fullmatch(r"RD-[A-Z]{3}-\d{1,4}", v) for v in value):
            raise ValueError("a summary lists radar rule ids and nothing else")
        return value


class AlertView(BaseModel):
    """One alert event as the alert service reads it (M14): the row of `alert_events`, ids and times only."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    id: EventId
    household_id: HouseholdId
    elder_id: ElderId
    inbox_id: InboxId
    kind: Literal["emergency", "scam", "unscanned"]
    origin: str = ""
    state: Literal["open", "acknowledged", "blocked"] = "open"
    stage: Literal["a", "b", "c"] = "a"
    rule_ids: tuple[str, ...] = ()
    t0: datetime
    next_due_at: datetime | None = None
    acked_by: ApproverId | None = None
    acked_at: datetime | None = None
    false_alarm: bool = False
    audio_ref: str | None = None
    blocked_reason: str | None = None
    followup_due_at: datetime | None = None
    last_elder_clip_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Request-side types kept for Phase 1
# --------------------------------------------------------------------------- #
class Intent(StrEnum):
    RIDE = "ride"
    GROCERY = "grocery"
    SHOPPING = "shopping"
    FOOD = "food"
    CLAIM_CHECK = "claim_check"
    TECH_HELP = "tech_help"
    APPOINTMENT = "appointment"
    REMINDER = "reminder"
    GENERAL_QA = "general_qa"
    MEDICINE = "medicine"        # out of v1 scope -> summary to approver
    BILL_PAYMENT = "bill_payment"  # out of v1 scope -> summary to approver
    CHIT_CHAT = "chit_chat"
    UNKNOWN = "unknown"


# Intents that may lead to a commit (money or a booking) and therefore always
# pass through the approval gate + policy engine.
COMMIT_INTENTS: frozenset[Intent] = frozenset(
    {Intent.RIDE, Intent.GROCERY, Intent.SHOPPING, Intent.FOOD, Intent.APPOINTMENT}
)
OUT_OF_SCOPE_INTENTS: frozenset[Intent] = frozenset({Intent.MEDICINE, Intent.BILL_PAYMENT})


class PaymentMethod(StrEnum):
    COD = "cod"            # cash on delivery / cash to driver
    WALLET = "wallet"      # prepaid in-app wallet with a small balance
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    PAY_LATER = "pay_later"
    UNKNOWN = "unknown"


class Elder(BaseModel):
    id: ElderId = Field(default_factory=lambda: ElderId(new_id()))
    household_id: HouseholdId
    display_name: str                 # how Saathi addresses them; a profile value, never a literal in code
    languages: list[str] = ["hi", "en"]


class Approver(BaseModel):
    id: ApproverId = Field(default_factory=lambda: ApproverId(new_id()))
    household_id: HouseholdId
    name: str
    telegram_user_id: int
    is_primary: bool = False


class Slots(BaseModel):
    """Loosely-typed slots; subgraphs validate their own required fields."""

    model_config = {"extra": "allow"}


class Understanding(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    summary_en: str                       # one line for the approver card
    slots: dict = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    urgency: str = "normal"               # "normal" | "urgent"
    tier_used: str = "none"  # "G<n>" of the grade that answered; "none" when no model did
    issue_category: str | None = None     # anonymised taxonomy label (v2 dataset)


class LineItem(BaseModel):
    sku: str
    name: str
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)
    category: str = "general"

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


class Quote(BaseModel):
    """What the agent proposes to commit. Hashed and bound to an approval."""

    id: str = Field(default_factory=new_id)
    household_id: str
    intent: Intent
    service: str                           # e.g. "swiggy", "rapido"
    items: list[LineItem] = Field(default_factory=list)
    ride_type: str | None = None
    pickup: str | None = None
    drop: str | None = None
    address_id: str | None = None
    payment_method: PaymentMethod
    total: Decimal = Field(ge=0)
    currency: str = "INR"
    created_at: datetime = Field(default_factory=utcnow)
