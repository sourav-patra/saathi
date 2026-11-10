"""Inbound adapters: the only place the edge names a platform (RQ-105).

An adapter checks the proof that a request came from the platform (on the raw bytes, before any parse) and turns a
parsed body into `InboundMessage` values. It never sends anything and holds no send token.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from saathi.channels.telegram_elder import parse_update, verify_secret_token
from saathi.channels.whatsapp import parse_webhook, verify_signature
from saathi.domain import InboundMessage, Source

# Header names are lower case: the handler lower-cases the names it is given.
WA_SIGNATURE_HEADER = "x-hub-signature-256"
TG_SECRET_HEADER = "x-telegram-bot-api-secret-token"


class InboundAdapter(Protocol):
    source: Source

    def verify(self, raw: bytes, headers: Mapping[str, str]) -> bool:
        """True only when the body carries the platform's proof. Compared as bytes, in constant time."""
        ...

    def parse(self, payload: Any, now: datetime) -> list[InboundMessage]:
        """Messages in a parsed body. `ChannelParseError` for a body that is not the documented envelope."""
        ...

    def ignored(self, payload: Any) -> str | None:
        """A counter name for a body that yields no message on purpose (for example a button press), else None."""
        ...


class WhatsAppInbound:
    source = Source.WHATSAPP

    def __init__(self, app_secret: str) -> None:
        self._secret = app_secret

    def verify(self, raw: bytes, headers: Mapping[str, str]) -> bool:
        return verify_signature(self._secret, raw, headers.get(WA_SIGNATURE_HEADER))

    def parse(self, payload: Any, now: datetime) -> list[InboundMessage]:
        return parse_webhook(payload, now=now)

    def ignored(self, payload: Any) -> str | None:
        return None


class TelegramElderInbound:
    """The elder bot. It has no callback handler: a callback query is counted and dropped (RQ-106)."""

    source = Source.TELEGRAM_ELDER

    def __init__(self, secret_token: str) -> None:
        self._secret = secret_token

    def verify(self, raw: bytes, headers: Mapping[str, str]) -> bool:
        return verify_secret_token(self._secret, headers.get(TG_SECRET_HEADER))

    def parse(self, payload: Any, now: datetime) -> list[InboundMessage]:
        return parse_update(payload, now=now)

    def ignored(self, payload: Any) -> str | None:
        if isinstance(payload, dict) and "callback_query" in payload:
            return "elder_callback_ignored"
        return None
