"""FlagRepo: the pause switch (M04). An unreadable flag raises `FlagUnreadable`; the caller decides, never a default."""

from __future__ import annotations

import asyncpg

from saathi.errors import FlagUnreadable, StoreUnavailable
from saathi.ports import Tx
from saathi.store.repos._util import affected


class FlagRepo:
    @staticmethod
    async def read_paused(tx: Tx) -> bool:
        """True when this household is paused. No row means never paused."""
        try:
            rows = await tx.fetch("SELECT paused FROM runtime_flags WHERE household_id = $1", tx.household)
        except (asyncpg.PostgresError, StoreUnavailable, OSError) as exc:
            raise FlagUnreadable() from exc
        return bool(rows[0]["paused"]) if rows else False

    @staticmethod
    async def set_paused(tx: Tx, paused: bool, *, by: str, reason: str | None = None) -> bool:
        tag = await tx.execute(
            "INSERT INTO runtime_flags (household_id, paused, paused_by, reason, updated_at)"
            " VALUES ($1, $2, $3, $4, $5)"
            " ON CONFLICT (household_id) DO UPDATE SET paused = EXCLUDED.paused, paused_by = EXCLUDED.paused_by,"
            " reason = EXCLUDED.reason, updated_at = EXCLUDED.updated_at",
            tx.household,
            paused,
            by,
            reason,
            tx.now,
        )
        return affected(tag) == 1
