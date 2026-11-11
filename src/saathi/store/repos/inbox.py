"""InboxRepo (M04; RQ-79, RQ-38). Every worker write names the lease token and returns False on a stale one."""

from __future__ import annotations

from datetime import datetime

from saathi.domain import Lease, NewInbox
from saathi.ids import InboxId, PublicKey
from saathi.ports import Sealer, Tx
from saathi.states import INBOX, Machine
from saathi.store.repos._util import aad, affected, content_key_id

_MACHINE = Machine(INBOX, phase=0)
_LEASED_TARGETS = frozenset(
    r.to for r in INBOX if r.from_ == "leased" and _MACHINE.lookup(r.from_, r.event) is not None
)
# Used only when a transcript arrives for an audio row that has no text part to copy an expiry from. The retention job
# of Phase 1 (RQ-42) replaces it.
DEFAULT_RAW_TTL_S = 30 * 24 * 3600


class InboxRepo:
    @staticmethod
    async def insert_if_new(
        tx: Tx, n: NewInbox, *, public_key: PublicKey, sealer: Sealer, expires_at: datetime
    ) -> bool:
        """Insert the row and one sealed part per `n.parts`. ``ON CONFLICT DO NOTHING`` with no RETURNING (the edge
        cannot read what it wrote); the command tag says whether the row was new. False on a repeat. With no conflict
        target, a clash on any unique key counts as a repeat, not only the provider message id (CHECK, foreign key
        and NOT NULL failures still raise)."""
        m = n.msg
        media = m.media
        tag = await tx.execute(
            "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
            " payload_sha256, media_ref, media_mime, not_before, received_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)"
            " ON CONFLICT DO NOTHING",  # no conflict target: naming one needs SELECT, which the edge lacks
            n.id,
            tx.household,
            m.provider_message_id,
            m.source.value,
            n.sender_hmac,
            n.elder_id,
            m.kind.value,
            n.payload_sha256,
            media.handle if media else None,
            media.mime if media else None,
            n.not_before,
            m.received_at,
        )
        if affected(tag) != 1:
            return False
        if n.parts:
            key_id = await content_key_id(tx, public_key)
            for p in n.parts:
                blob = sealer.seal(public_key, p.text.encode(), aad=aad(tx.household, n.id, p.part))
                await tx.execute(
                    "INSERT INTO raw_text (inbox_id, household_id, part, body_sealed, key_id, expires_at)"
                    " VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                    n.id,
                    tx.household,
                    p.part,
                    blob,
                    key_id,
                    expires_at,
                )
        return True

    @staticmethod
    async def renew(tx: Tx, lease: Lease, until: datetime) -> bool:
        tag = await tx.execute(
            "UPDATE inbox SET lease_until = $3 WHERE id = $1 AND lease_token = $2 AND state = 'leased'",
            lease.inbox_id,
            lease.token,
            until,
        )
        return affected(tag) == 1

    @staticmethod
    async def settle(tx: Tx, lease: Lease, state: str) -> bool:
        """Leave `leased` for a state the inbox machine allows (N06 to N10). A stale token changes nothing."""
        if state not in _LEASED_TARGETS:
            raise ValueError(f"inbox state {state!r} is not reachable from leased")
        tag = await tx.execute(
            "UPDATE inbox SET state = $3, lease_until = NULL, lease_token = NULL"
            " WHERE id = $1 AND lease_token = $2 AND state = 'leased'",
            lease.inbox_id,
            lease.token,
            state,
        )
        return affected(tag) == 1

    @staticmethod
    async def mark_acked(tx: Tx, inbox_id: InboxId, now: datetime) -> bool:
        tag = await tx.execute("UPDATE inbox SET acked_at = $2 WHERE id = $1 AND acked_at IS NULL", inbox_id, now)
        return affected(tag) == 1

    @staticmethod
    async def set_radar_at(tx: Tx, inbox_id: InboxId, now: datetime) -> bool:
        tag = await tx.execute("UPDATE inbox SET radar_at = $2 WHERE id = $1 AND radar_at IS NULL", inbox_id, now)
        return affected(tag) == 1

    @staticmethod
    async def add_transcript(tx: Tx, lease: Lease, text: str, sealer: Sealer, public_key: PublicKey) -> bool:
        """Seal a transcript part, only while the caller still holds the lease. False on a stale token or a repeat."""
        held = await tx.fetch(
            "SELECT 1 FROM inbox WHERE id = $1 AND lease_token = $2 AND state = 'leased'", lease.inbox_id, lease.token
        )
        if not held:
            return False
        key_id = await content_key_id(tx, public_key)
        blob = sealer.seal(public_key, text.encode(), aad=aad(tx.household, lease.inbox_id, "transcript"))
        tag = await tx.execute(
            "INSERT INTO raw_text (inbox_id, household_id, part, body_sealed, key_id, expires_at)"
            " VALUES ($1, $2, 'transcript', $3, $4,"
            "   coalesce((SELECT max(expires_at) FROM raw_text WHERE inbox_id = $1), $5::timestamptz"
            "            + make_interval(secs => $6)))"
            " ON CONFLICT DO NOTHING",
            lease.inbox_id,
            tx.household,
            blob,
            key_id,
            tx.now,
            float(DEFAULT_RAW_TTL_S),
        )
        return affected(tag) == 1
