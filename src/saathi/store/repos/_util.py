"""Small helpers shared by the repos."""

from __future__ import annotations

import json
from typing import Any

from saathi.errors import KeyMissing
from saathi.ids import PublicKey
from saathi.ports import Tx


def affected(tag: str) -> int:
    """Rows changed, from an asyncpg command tag such as ``INSERT 0 1`` or ``UPDATE 3``."""
    try:
        return int(tag.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


def aad(household: str, subject: str, part: str) -> bytes:
    """AAD of a sealed box: ``household|subject|part`` (data part, section 4), so a moved blob fails to open."""
    return f"{household}|{subject}|{part}".encode()


async def content_key_id(tx: Tx, public_key: PublicKey) -> str:
    """The key id of a content public key of this household (the edge may read it; it holds no private half)."""
    rows = await tx.fetch(
        "SELECT key_id FROM control.household_keys WHERE household_id = $1 AND purpose = 'content' AND public_key = $2",
        tx.household,
        bytes(public_key),
    )
    if not rows:
        raise KeyMissing()
    return str(rows[0]["key_id"])


def json_value(value: Any) -> Any:
    """jsonb as a Python value, whether or not the pool decodes it."""
    return json.loads(value) if isinstance(value, str) else value
