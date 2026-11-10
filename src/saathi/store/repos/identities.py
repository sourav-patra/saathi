"""IdentityRepo (M04; RQ-105, RQ-111a). The core holds a `PersonRef`; the sealed address opens only in the core."""

from __future__ import annotations

from saathi.ids import ElderId, PersonRef, new_id
from saathi.ports import Tx
from saathi.store.repos._util import affected


class IdentityRepo:
    @staticmethod
    async def person_ref(tx: Tx, elder_id: ElderId, source: str) -> PersonRef:
        """The opaque reference of this elder's active identity on `source`; `LookupError` when there is none."""
        rows = await tx.fetch(
            "SELECT person_ref FROM elder_identities WHERE elder_id = $1 AND channel = $2 AND active"
            " ORDER BY registered_at DESC LIMIT 1",
            elder_id,
            str(source),
        )
        if not rows:
            raise LookupError("no active identity")
        return PersonRef(rows[0]["person_ref"])

    @staticmethod
    async def sealed_address(tx: Tx, ref: PersonRef) -> tuple[bytes, str] | None:
        """The sealed platform address and its key id, for the `PersonDirectory` of the core to open."""
        rows = await tx.fetch(
            "SELECT person_ref_enc, key_id FROM elder_identities WHERE person_ref = $1 AND active", ref
        )
        return (bytes(rows[0]["person_ref_enc"]), str(rows[0]["key_id"])) if rows else None

    @staticmethod
    async def register(
        tx: Tx,
        elder_id: ElderId,
        source: str,
        *,
        sender_hmac: str,
        person_ref: PersonRef,
        person_ref_enc: bytes,
        key_id: str,
        identity_id: str | None = None,
    ) -> bool:
        """Owner role only. False when any unique key clashes (the sender hash, the person reference or the id): with no
        conflict target, every unique key counts as a repeat. CHECK, foreign key and NOT NULL failures still raise."""
        tag = await tx.execute(
            "INSERT INTO elder_identities (id, household_id, elder_id, channel, sender_hmac, person_ref,"
            " person_ref_enc, key_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT DO NOTHING",
            identity_id or new_id(),
            tx.household,
            elder_id,
            str(source),
            sender_hmac,
            person_ref,
            person_ref_enc,
            key_id,
        )
        return affected(tag) == 1
