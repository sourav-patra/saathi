"""Phase 0 era message types, kept only until the units that rewrite their callers land.

The hub's disposition table removes `Channel` and the phone-number fields from `domain.py`. The
old callers still use them, so they live here until the units that rewrite those callers land.
Nothing new may import this module (tests/test_foundation.py pins the importer set; it may only shrink).

Removal plan, one importer per unit:
- WU-08 replaces `channels/whatsapp.py`
- WU-14 replaces `app.py`; it has dropped its import and `LegacyHouseholdEnv` is gone
- WU-15 replaced `pipeline.py` (removed; its import is gone)
- WU-18 replaces `channels/telegram.py`
- WU-20 replaces `tests/test_channels_llm_pipeline.py`
The last of these to land deletes this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from saathi.domain import new_id, utcnow


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    TELEGRAM_FORWARD = "telegram_forward"


class MediaKind(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    OTHER = "other"


class InboundMessage(BaseModel):
    """A single message from (or forwarded on behalf of) an elder."""

    id: str = Field(default_factory=new_id)
    channel: Channel
    provider_message_id: str
    sender_phone_e164: str | None = None
    forwarded_by_telegram_id: int | None = None
    kind: MediaKind
    text: str | None = None
    media_id: str | None = None
    media_mime: str | None = None
    received_at: datetime = Field(default_factory=utcnow)
