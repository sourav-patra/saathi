"""Helpers for tests of the store: a Store on a real runtime role, and synthetic seed rows (superuser, bypasses RLS)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import asyncpg
from conftest import PgDb

from saathi.clock import FakeClock
from saathi.store import Role, Store, create_pool

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def open_store(
    db: PgDb, role: Role, *, clock: FakeClock | None = None, max_size: int = 4
) -> AsyncIterator[Store]:
    """A Store whose pool logs in as the runtime role itself, never as a superuser."""
    pool = await create_pool(db.dsn(f"saathi_{role.value}"), min_size=1, max_size=max_size)
    try:
        yield Store(pool, role, clock or FakeClock(NOW))
    finally:
        await pool.close()


async def seed_household(db: PgDb, h: str) -> None:
    """One household with an elder, an approver, an identity, a content key and one inbox row `m<h>`."""
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute("INSERT INTO households (id, name) VALUES ($1, $2)", h, f"Household {h}")
        await su.execute("INSERT INTO elders (id, household_id, display_name) VALUES ($1, $2, 'Elder')", "e" + h, h)
        await su.execute(
            "INSERT INTO approvers (id, household_id, name, telegram_user_id) VALUES ($1, $2, 'Approver', $3)",
            "a" + h, h, 1000 + sum(h.encode()),
        )  # fmt: skip
        await su.execute(
            "INSERT INTO elder_identities (id, household_id, elder_id, channel, sender_hmac, person_ref,"
            " person_ref_enc, key_id) VALUES ($1, $2, $3, 'whatsapp', $4, $5, '\\x00', 'k1')",
            "i" + h, h, "e" + h, "hmac-" + h, "ref-" + h,
        )  # fmt: skip
        await su.execute(
            "INSERT INTO control.household_keys (household_id, purpose, key_id, public_key, wrapped_key)"
            " VALUES ($1, 'content', 'k1', $2, '\\x00')",
            h, b"pub-" + h.encode(),
        )  # fmt: skip
    finally:
        await su.close()


async def seed_inbox(db: PgDb, h: str, inbox_id: str, *, state: str = "received", kind: str = "text") -> None:
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute(
            "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
            " payload_sha256, state, received_at) VALUES ($1, $2, $3, 'whatsapp', $4, $5, $6, 'sha', $7, $8)",
            inbox_id, h, "wa:" + inbox_id, "hmac-" + h, "e" + h, kind, state, NOW,
        )  # fmt: skip
    finally:
        await su.close()
