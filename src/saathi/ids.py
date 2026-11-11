"""Opaque identifier types and id helpers (M01, RQ-109, RQ-79).

Every identifier is a `NewType` so a household id cannot be passed where an elder id is wanted.
`PersonRef` is opaque: core code never sees a phone number or chat id (RQ-105).
"""

from __future__ import annotations

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
    """A stable child id, `<prefix>_<parent>`: the request of inbox `x` is `r_x` (idempotent effects)."""
    if not prefix.strip() or not parent.strip():
        raise ValueError("derive_id needs a non-empty prefix and parent")
    return f"{prefix}_{parent}"


def idem_key(*parts: str) -> IdemKey:
    """Join parts with ':'; no part may be empty, so two different effects never share a key."""
    if not parts or any(not p.strip() for p in parts):
        raise ValueError("idempotency key parts must be non-empty")
    return IdemKey(":".join(parts))


_E164_JUNK = " \t-.()"


def normalize_e164(raw: str) -> str:
    """The one phone form used by registration, the edge and the channels: ``+`` and ASCII digits only.

    Spaces, dashes, dots and brackets are dropped and a leading ``00`` counts as ``+``. Anything else, or a
    length outside 7 to 15 digits, raises `ValueError`.
    """
    text = "".join(c for c in raw if c not in _E164_JUNK)
    if text.startswith("00"):
        text = "+" + text[2:]
    digits = text.removeprefix("+")
    if not (digits.isascii() and digits.isdigit() and 7 <= len(digits) <= 15):
        raise ValueError("not an E.164 number")
    return "+" + digits
