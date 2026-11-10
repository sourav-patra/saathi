"""ScanFns: one method per security-definer function of the grants part (M04; RQ-109, RQ-38).

Each function returns ids and counts only, never content. A call runs in its own transaction on a connection that has
no household set; the function sets and restores the household itself. A role may call only the functions it was
granted (grants part, section 2); anything else raises `PermissionError` before a statement is sent.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from saathi.domain import Lease
from saathi.errors import StoreUnavailable
from saathi.ids import ApproverId, ElderId, EventId, HouseholdId, InboxId
from saathi.store.db import CONN_ERRORS, Role

SCAN_GRANTS: dict[Role, frozenset[str]] = {
    Role.EDGE: frozenset({"resolve_sender", "reject_inbound"}),
    Role.CORE: frozenset(
        {
            "resolve_sender",
            "resolve_approver",
            "lease_next",
            "reap_inbox",
            "unacked_inbox",
            "due_alerts",
            "unscanned_audio",
            "rejected_digest",
        }
    ),
    Role.OWNER: frozenset({"rejected_digest"}),
}


class ScanFns:
    def __init__(self, pool: asyncpg.Pool, role: Role) -> None:
        self._pool = pool
        self._role = role

    def _allow(self, name: str) -> None:
        if name not in SCAN_GRANTS.get(self._role, frozenset()):
            raise PermissionError(f"role {self._role.value} may not call {name}")

    async def _fetch(self, sql: str, *args: object) -> list[asyncpg.Record]:
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                return await conn.fetch(sql, *args)
        except CONN_ERRORS as exc:
            raise StoreUnavailable(type(exc).__name__) from exc

    async def resolve_sender(self, hmac: str) -> tuple[HouseholdId, ElderId] | None:
        self._allow("resolve_sender")
        rows = await self._fetch("SELECT household_id, elder_id FROM control.resolve_sender($1)", hmac)
        return (HouseholdId(rows[0]["household_id"]), ElderId(rows[0]["elder_id"])) if rows else None

    async def resolve_approver(self, telegram_id: int) -> tuple[HouseholdId, ApproverId] | None:
        self._allow("resolve_approver")
        rows = await self._fetch("SELECT household_id, approver_id FROM control.resolve_approver($1)", telegram_id)
        return (HouseholdId(rows[0]["household_id"]), ApproverId(rows[0]["approver_id"])) if rows else None

    async def lease_next(self, now: datetime, lease_s: int = 60) -> Lease | None:
        self._allow("lease_next")
        rows = await self._fetch(
            "SELECT household_id, inbox_id, elder_id, lease_token FROM control.lease_next($1, $2)", now, lease_s
        )
        if not rows:
            return None
        r = rows[0]
        return Lease(
            household_id=HouseholdId(r["household_id"]),
            inbox_id=InboxId(r["inbox_id"]),
            elder_id=ElderId(r["elder_id"]),
            token=UUID(str(r["lease_token"])),
        )

    async def reap_inbox(self, now: datetime) -> list[tuple[HouseholdId, InboxId]]:
        self._allow("reap_inbox")
        rows = await self._fetch("SELECT household_id, inbox_id FROM control.reap_inbox($1)", now)
        return [(HouseholdId(r["household_id"]), InboxId(r["inbox_id"])) for r in rows]

    async def unacked_inbox(self, now: datetime, limit: int = 50) -> list[tuple[HouseholdId, InboxId]]:
        self._allow("unacked_inbox")
        rows = await self._fetch("SELECT household_id, inbox_id FROM control.unacked_inbox($1, $2)", now, limit)
        return [(HouseholdId(r["household_id"]), InboxId(r["inbox_id"])) for r in rows]

    async def due_alerts(self, now: datetime, limit: int = 50) -> list[tuple[HouseholdId, EventId]]:
        self._allow("due_alerts")
        rows = await self._fetch("SELECT household_id, event_id FROM control.due_alerts($1, $2)", now, limit)
        return [(HouseholdId(r["household_id"]), EventId(r["event_id"])) for r in rows]

    async def unscanned_audio(self, now: datetime, max_s: int) -> list[tuple[HouseholdId, InboxId]]:
        self._allow("unscanned_audio")
        rows = await self._fetch("SELECT household_id, inbox_id FROM control.unscanned_audio($1, $2)", now, max_s)
        return [(HouseholdId(r["household_id"]), InboxId(r["inbox_id"])) for r in rows]

    async def rejected_digest(self, since: datetime) -> tuple[int, int]:
        self._allow("rejected_digest")
        rows = await self._fetch("SELECT distinct_senders, total FROM control.rejected_digest($1)", since)
        return (int(rows[0]["distinct_senders"]), int(rows[0]["total"]))

    async def reject_inbound(self, sender_hmac: str, reason: str) -> None:
        """Tenantless insert (C-6): the sender is unknown. No RETURNING: the edge cannot read the table."""
        self._allow("reject_inbound")
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "INSERT INTO rejected_inbound (sender_hmac, reason) VALUES ($1, $2)", sender_hmac, reason
                )
        except CONN_ERRORS as exc:
            raise StoreUnavailable(type(exc).__name__) from exc
