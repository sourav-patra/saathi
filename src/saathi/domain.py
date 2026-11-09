"""Core domain types shared across channels, the graph and the policy engine.

Multi-household from day 1: every record hangs off a ``household_id`` so more
elders and families can be added without schema changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM_FORWARD = "telegram_forward"  # caregiver forwarding an elder's message


class MediaKind(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    OTHER = "other"


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
    id: str = Field(default_factory=new_id)
    household_id: str
    display_name: str                 # how Saathi addresses them, e.g. "the name the elder prefers"
    wa_phone_e164: str                # sender id on the helper number
    languages: list[str] = ["hi", "en"]


class Approver(BaseModel):
    id: str = Field(default_factory=new_id)
    household_id: str
    name: str
    telegram_user_id: int
    is_primary: bool = False


class InboundMessage(BaseModel):
    """A single message from (or forwarded on behalf of) an elder."""

    id: str = Field(default_factory=new_id)
    channel: Channel
    provider_message_id: str
    sender_phone_e164: str | None = None   # WhatsApp sender
    forwarded_by_telegram_id: int | None = None
    kind: MediaKind
    text: str | None = None                # text body or caption
    media_id: str | None = None            # provider media handle
    media_mime: str | None = None
    received_at: datetime = Field(default_factory=utcnow)


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
    tier_used: str = "T1"
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
