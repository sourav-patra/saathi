"""Opaque identifier types and id helpers (M01, RQ-109, RQ-79).

Every identifier is a `NewType` so a household id cannot be passed where an elder id is wanted.
`PersonRef` is opaque: core code never sees a phone number or chat id (RQ-105).
"""

from __future__ import annotations

import hashlib
from typing import NewType
from uuid import uuid4

HouseholdId = NewType("HouseholdId", str)
ElderId = NewType("ElderId", str)
ApproverId = NewType("ApproverId", str)
InboxId = NewType("InboxId", str)
RequestId = NewType("RequestId", str)
EventId = NewType("EventId", str)
RunId = NewType("RunId", str)
PersonRef = NewType("PersonRef", str)
IdemKey = NewType("IdemKey", str)
ReplyKey = NewType("ReplyKey", str)
TemplateId = NewType("TemplateId", str)
VoiceId = NewType("VoiceId", str)
PublicKey = NewType("PublicKey", bytes)


def new_id() -> str:
    return uuid4().hex


def derive_id(prefix: str, parent: str) -> str:
    """A stable child id: the same prefix and parent always give the same id (idempotent effects)."""
    if not prefix.strip() or not parent.strip():
        raise ValueError("derive_id needs a non-empty prefix and parent")
    digest = hashlib.sha256(f"{prefix}:{parent}".encode()).hexdigest()[:32]
    return f"{prefix}_{digest}"


def idem_key(*parts: str) -> IdemKey:
    """Join parts with ':'; no part may be empty, so two different effects never share a key."""
    if not parts or any(not p.strip() for p in parts):
        raise ValueError("idempotency key parts must be non-empty")
    return IdemKey(":".join(parts))
