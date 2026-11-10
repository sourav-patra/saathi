"""SignoffRepo (M06; RQ-95a, PL-26): the owner writes, the core reads, an expired or revoked row is off."""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest
from conftest import PgDb
from store_helpers import NOW, open_store

from saathi.ids import HouseholdId
from saathi.store import Role
from saathi.store.repos import SignoffRepo

H = HouseholdId("h1")


async def seed(db: PgDb) -> None:
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute("INSERT INTO households (id, name) VALUES ('h1', 'Household h1')")
        await su.execute("INSERT INTO households (id, name) VALUES ('h2', 'Household h2')")
    finally:
        await su.close()


async def test_no_row_is_off(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:
        state = await SignoffRepo.valid(tx, "SO-1", NOW)
    assert state.valid is False and state.id is None and state.revoked is False


async def test_grant_then_core_reads_valid_and_expiry_behaves_as_off(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.OWNER) as owner, owner.tx(household=H) as tx:
        sid = await SignoffRepo.grant(tx, "SO-1", granter="owner", expires_at=NOW + timedelta(days=90))
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:
        now = await SignoffRepo.valid(tx, "SO-1", NOW)
        assert (now.valid, now.id, now.revoked) == (True, sid, False)
        assert now.expires_at == NOW + timedelta(days=90)
        assert (await SignoffRepo.valid(tx, "SO-1", NOW + timedelta(days=90))).valid is False  # at expiry, off
        assert (await SignoffRepo.valid(tx, "SO-1", NOW + timedelta(days=91))).valid is False
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=HouseholdId("h2")) as tx:
        assert (await SignoffRepo.valid(tx, "SO-1", NOW)).valid is False  # another household's row is invisible


async def test_revoke_turns_it_off_once(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.OWNER) as owner:
        async with owner.tx(household=H) as tx:
            sid = await SignoffRepo.grant(tx, "SO-1", granter="owner", expires_at=NOW + timedelta(days=90))
        async with owner.tx(household=H) as tx:
            assert await SignoffRepo.revoke(tx, sid, NOW) is True
            assert await SignoffRepo.revoke(tx, sid, NOW) is False
            assert await SignoffRepo.revoke(tx, sid + 99, NOW) is False
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:
        state = await SignoffRepo.valid(tx, "SO-1", NOW)
    assert (state.valid, state.revoked, state.id) == (False, True, sid)


async def test_newest_grant_wins_after_a_revoke(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.OWNER) as owner:
        async with owner.tx(household=H) as tx:
            first = await SignoffRepo.grant(tx, "SO-1", granter="owner", expires_at=NOW + timedelta(days=90))
            await SignoffRepo.revoke(tx, first, NOW)
            second = await SignoffRepo.grant(tx, "SO-1", granter="owner", expires_at=NOW + timedelta(days=90))
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:
        state = await SignoffRepo.valid(tx, "SO-1", NOW)
    assert state.valid is True and state.id == second


async def test_agent_roles_cannot_write(pg_db: PgDb):
    await seed(pg_db)
    for role in (Role.CORE, Role.MODEL, Role.EDGE):
        async with open_store(pg_db, role) as store, store.tx(household=H) as tx:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await SignoffRepo.grant(tx, "SO-1", granter="x", expires_at=NOW + timedelta(days=1))
