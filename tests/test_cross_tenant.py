"""LT-02: row-level isolation as the real runtime roles, scan functions and the household setting (M04; RQ-109, 38).

Seeding uses the superuser (it bypasses row-level security); every check connects as the role under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest
from conftest import PgDb
from store_helpers import NOW, open_store

from saathi.clock import FakeClock
from saathi.errors import TenantNotSet
from saathi.ids import HouseholdId
from saathi.store import Role, Store

A, B = "hA", "hB"
# readonly bypasses row-level security by attribute, so it is not part of the matrix
ROLES = ("saathi_edge", "saathi_core", "saathi_model", "saathi_owner")
# LLD grants section 2: the owner reads every households row (policy owner_hh); it cannot change one.
READ_ALL_HOUSEHOLDS = {("saathi_owner", "households")}
LATER = datetime(2100, 1, 1, tzinfo=UTC)


def seed_sql(h: str, tag: str = "") -> list[tuple[str, str, tuple]]:
    """(table, insert statement, args) for one household. `tag` makes every key new, for the refused insert."""
    k = tag
    return [
        ("households", "INSERT INTO households (id, name) VALUES ($1, 'H')", (h + k,)),
        ("elders", "INSERT INTO elders (id, household_id, display_name) VALUES ($1, $2, 'E')", ("e" + h + k, h)),
        ("approvers", "INSERT INTO approvers (id, household_id, name, telegram_user_id) VALUES ($1, $2, 'P', $3)",
         ("p" + h + k, h, 5000 + len(k) + ord(h[-1]))),
        ("addresses", "INSERT INTO addresses (id, household_id, label, formatted) VALUES ($1, $2, 'home', 'x')",
         ("ad" + h + k, h)),
        ("profile_facts", "INSERT INTO profile_facts (household_id, key, value, source) VALUES ($1, 'k', '{}', 's')",
         (h,)),
        ("elder_identities",
         "INSERT INTO elder_identities (id, household_id, elder_id, channel, sender_hmac, person_ref, person_ref_enc,"
         " key_id) VALUES ($1, $2, $3, 'whatsapp', $4, $5, '\\x00', 'k')",
         ("i" + h + k, h, "e" + h, "s" + h + k, "r" + h + k)),
        ("inbox",
         "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
         " payload_sha256)"
         " VALUES ($1, $2, $3, 'whatsapp', 's', $4, 'text', 'x')", ("m" + h + k, h, "wa:" + h + k, "e" + h)),
        ("raw_text",
         "INSERT INTO raw_text (inbox_id, household_id, part, body_sealed, key_id, expires_at)"
         " VALUES ($1, $2, $3, '\\x00', 'k', now())", ("m" + h, h, "caption" if k else "text")),
        ("requests", "INSERT INTO requests (id, household_id, channel, kind) VALUES ($1, $2, 'whatsapp', 'text')",
         ("q" + h + k, h)),
        ("quotes",
         "INSERT INTO quotes (id, request_id, household_id, quote_hash, body, total_inr)"
         " VALUES ($1, $2, $3, 'x', '{}', 1)", ("qt" + h + k, "q" + h, h)),
        ("approvals",
         "INSERT INTO approvals (quote_id, approver_id, decision, household_id) VALUES ($1, $2, 'approve', $3)",
         ("qt" + h, "p" + h, h)),
        ("commits", "INSERT INTO commits (quote_id, household_id, service, amount_inr) VALUES ($1, $2, 's', 1)",
         ("qt" + h, h)),
        ("runtime_flags", "INSERT INTO runtime_flags (household_id) VALUES ($1)", (h,)),
        ("alert_events",
         "INSERT INTO alert_events (id, household_id, elder_id, inbox_id, kind, origin, t0)"
         " VALUES ($1, $2, $3, $4, 'emergency', 'radar_edge', now())", ("al" + h + k, h, "e" + h, "m" + h)),
        ("alert_attempts",
         "INSERT INTO alert_attempts (household_id, event_id, stage, approver_id, channel, idem_key, due_at)"
         " VALUES ($1, $2, 'a', $3, 'telegram', $4, now())", (h, "al" + h, "p" + h, "idem" + h + k)),
        ("tg_updates", "INSERT INTO tg_updates (bot, update_id, household_id, kind) VALUES ('elder', $1, $2, 'm')",
         (ord(h[-1]) * 10 + len(k), h)),
        ("audit_log",
         "INSERT INTO audit_log (household_id, run_id, actor, event, key_id) VALUES ($1, 'r', 'system:t', 'e', 'k')",
         (h,)),
        ("signoffs",
         "INSERT INTO control.signoffs (household_id, scope, granter, expires_at) VALUES ($1, 'SO-1', 'g',"
         " now() + interval '1 day')", (h,)),
        ("consent_events",
         "INSERT INTO control.consent_events (household_id, subject, use, recipient, event, capacity, language, how,"
         " notice_text_sha256, recorded_by) VALUES ($1, 'elder', 'u', 'r', 'grant', 'self', 'hi', 'spoken', 'x', 'o')",
         (h,)),
        ("household_config",
         "INSERT INTO control.household_config (household_id, version, values, created_by) VALUES ($1, $2, '{}', 'o')",
         (h, 2 if k else 1)),
        ("household_keys",
         "INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
         " VALUES ($1, $2, 'k', '\\x00')",
         (h, "totp" if k else "audit")),
        ("llm_spend", "INSERT INTO llm_spend (tier, model, cost_inr, household_id) VALUES ('t', 'm', 1, $1)", (h,)),
        ("llm_reservations",
         "INSERT INTO llm_reservations (household_id, run_id, tier, reserved_inr, month)"
         " VALUES ($1, 'r', 't', 1, '2026-01')",
         (h,)),
    ]  # fmt: skip


TABLES = [t for t, _, _ in seed_sql(A)]
QUALIFIED = {"signoffs": "control.signoffs", "consent_events": "control.consent_events",
             "household_config": "control.household_config", "household_keys": "control.household_keys"}  # fmt: skip


def col(table: str) -> str:
    return "id" if table == "households" else "household_id"


async def seed_both(db: PgDb) -> None:
    su = await asyncpg.connect(db.dsn())
    try:
        for h in (A, B):
            for _t, sql, args in seed_sql(h):
                await su.execute(sql, *args)
    finally:
        await su.close()


async def test_seed_covers_every_household_table(pg_db: PgDb):
    """The matrix below is not vacuous: it names exactly the tables that carry a household."""
    su = await asyncpg.connect(pg_db.dsn())
    try:
        rows = await su.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind = 'r' AND n.nspname IN ('public', 'control')"
            " AND (c.relname = 'households' OR EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid"
            " AND a.attname = 'household_id' AND NOT a.attisdropped))"
        )
    finally:
        await su.close()
    assert {r["relname"] for r in rows} == set(TABLES)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", ROLES)
async def test_other_household_row_untouched(pg_db: PgDb, role: str, table: str):
    await seed_both(pg_db)
    t = QUALIFIED.get(table, table)
    c = col(table)
    pool_role = Role(role.removeprefix("saathi_"))
    async with open_store(pg_db, pool_role) as store:
        # B's row is invisible, unchangeable and undeletable from a transaction of A: zero rows or no privilege.
        checks = [
            (f"SELECT count(*) FROM {t} WHERE {c} = '{B}'", 0),
            (f"UPDATE {t} SET {c} = {c} WHERE {c} = '{B}'", "UPDATE 0"),
            (f"DELETE FROM {t} WHERE {c} = '{B}'", "DELETE 0"),
        ]
        if (role, table) in READ_ALL_HOUSEHOLDS:
            checks = checks[1:]
        for sql, expect in checks:
            try:
                async with store.tx(household=HouseholdId(A)) as tx:
                    got = (await tx.fetch(sql))[0]["count"] if sql.startswith("SELECT") else await tx.execute(sql)
            except asyncpg.InsufficientPrivilegeError:
                continue
            assert got == expect, f"{role} {sql}"
    su = await asyncpg.connect(pg_db.dsn())
    try:
        assert await su.fetchval(f"SELECT count(*) FROM {t} WHERE {c} = '{B}'") >= 1  # nothing was removed
    finally:
        await su.close()


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", ROLES)
async def test_insert_other_household_refused(pg_db: PgDb, role: str, table: str):
    await seed_both(pg_db)
    [(sql, args)] = [(s, a) for t, s, a in seed_sql(B, "x") if t == table]
    pool_role = Role(role.removeprefix("saathi_"))
    async with open_store(pg_db, pool_role) as store:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):  # no grant, or a row that violates the policy
            async with store.tx(household=HouseholdId(A)) as tx:
                await tx.execute(sql, *args)


@pytest.mark.parametrize("role", ROLES)
async def test_unset_household_returns_nothing(pg_db: PgDb, role: str):
    await seed_both(pg_db)
    conn = await asyncpg.connect(pg_db.dsn(role))  # a plain connection: no Store, so nothing sets the household
    try:
        assert await conn.fetchval("SELECT current_setting('app.household_id', true)") in (None, "")
        for table in TABLES:
            if (role, table) in READ_ALL_HOUSEHOLDS:
                continue  # the one policy that does not look at the household setting
            try:
                n = await conn.fetchval(f"SELECT count(*) FROM {QUALIFIED.get(table, table)}")
            except asyncpg.InsufficientPrivilegeError:
                continue
            assert n == 0, f"{role} sees {table} with no household set"
    finally:
        await conn.close()


async def test_own_household_is_visible(pg_db: PgDb):
    """The control of the tests above: with A set, the core does see A's rows (and only A's)."""
    await seed_both(pg_db)
    async with open_store(pg_db, Role.CORE) as store, store.tx(household=HouseholdId(A)) as tx:
        rows = await tx.fetch("SELECT id FROM inbox")
        assert [r["id"] for r in rows] == ["m" + A]


async def test_tx_without_household_raises(pg_db: PgDb):
    async with open_store(pg_db, Role.CORE) as store:
        for empty in ("", "  "):
            with pytest.raises(TenantNotSet):
                async with store.tx(household=HouseholdId(empty)):
                    pytest.fail("the body must not run")


async def test_tx_without_household_sends_no_statement():
    class Boom:
        def acquire(self):
            raise AssertionError("the pool was used before the household was checked")

    store = Store(Boom(), Role.CORE, None)  # type: ignore[arg-type]
    with pytest.raises(TenantNotSet):
        async with store.tx(household=HouseholdId("")):
            pass


async def test_household_setting_never_leaks_across_a_pooled_connection(pg_db: PgDb):
    await seed_both(pg_db)
    async with open_store(pg_db, Role.CORE, max_size=1) as store:  # one connection: every tx reuses it
        async with store.tx(household=HouseholdId(A)) as tx:
            first_pid = await tx.fetch("SELECT pg_backend_pid() AS pid")
            assert [r["id"] for r in await tx.fetch("SELECT id FROM inbox")] == ["m" + A]
        async with store.tx(household=HouseholdId(B)) as tx:
            assert (await tx.fetch("SELECT pg_backend_pid() AS pid")) == first_pid
            assert [r["id"] for r in await tx.fetch("SELECT id FROM inbox")] == ["m" + B]
            assert await tx.fetch("SELECT 1 FROM inbox WHERE id = $1", "m" + A) == []
        # a rolled-back transaction leaks nothing either
        with pytest.raises(RuntimeError):
            async with store.tx(household=HouseholdId(A)):
                raise RuntimeError("boom")
        async with store.tx(household=HouseholdId(B)) as tx:
            assert await tx.fetch("SELECT 1 FROM inbox WHERE id = $1", "m" + A) == []
        # outside any transaction the connection has no household
        async with store._pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_setting('app.household_id', true)") in (None, "")
            assert await conn.fetchval("SELECT count(*) FROM inbox") == 0


async def _noop_reset(conn: asyncpg.Connection) -> None:
    """Stand-in for the pool's RESET ALL on release: the pool then hides nothing."""


async def test_household_setting_is_transaction_local_without_pool_reset(pg_db: PgDb):
    """F-1: the leak test above passes on the pool's own reset. Here the reset does nothing, so only
    ``set_config(..., true)`` keeps the household off the next user of the connection."""
    await seed_both(pg_db)
    pool = await asyncpg.create_pool(pg_db.dsn("saathi_core"), min_size=1, max_size=1, reset=_noop_reset)
    try:
        store = Store(pool, Role.CORE, FakeClock(NOW))
        async with store.tx(household=HouseholdId(A)) as tx:
            assert [r["id"] for r in await tx.fetch("SELECT id FROM inbox")] == ["m" + A]
        async with pool.acquire() as conn:  # the same connection, nothing reset
            assert await conn.fetchval("SELECT current_setting('app.household_id', true)") in (None, "")
            assert await conn.fetchval("SELECT count(*) FROM inbox") == 0
    finally:
        await pool.close()


async def test_lock_household_takes_an_advisory_lock(pg_db: PgDb):
    async with open_store(pg_db, Role.CORE) as store:
        async with store.tx(household=HouseholdId(A), lock_household=True) as tx:
            held = await tx.fetch("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND granted")
            assert held[0]["n"] == 1
        async with store.tx(household=HouseholdId(A)) as tx:
            assert (await tx.fetch("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'"))[0]["n"] == 0


IDS_ONLY = {
    "household_id", "elder_id", "approver_id", "inbox_id", "event_id", "lease_token", "distinct_senders", "total",
}  # fmt: skip


async def test_scan_functions_return_ids_only(pg_db: PgDb):
    await seed_both(pg_db)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        rows = await su.fetch(
            "SELECT p.proname, pg_get_function_result(p.oid) AS res, p.prosecdef FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'control' AND p.proname = ANY($1)",
            ["resolve_sender", "resolve_approver", "lease_next", "reap_inbox", "unacked_inbox", "due_alerts",
             "unscanned_audio", "rejected_digest"],
        )  # fmt: skip
    finally:
        await su.close()
    assert len(rows) == 8
    for r in rows:
        assert r["prosecdef"]
        cols = {c.strip().split(" ")[0] for c in r["res"].removeprefix("TABLE(").removesuffix(")").split(",")}
        assert cols <= IDS_ONLY, f"{r['proname']} returns {cols - IDS_ONLY}"

    async with open_store(pg_db, Role.EDGE) as edge:
        await edge.scan.reject_inbound("unknown-hmac", "unregistered")
    async with open_store(pg_db, Role.CORE) as store:
        scan = store.scan
        assert await scan.resolve_sender("s" + A) == (A, "e" + A)
        assert await scan.resolve_approver(5000 + ord("A")) == (A, "p" + A)
        assert await scan.resolve_approver(1) is None
        lease = await scan.lease_next(LATER)
        assert lease is not None and (lease.household_id, lease.inbox_id, lease.elder_id) == (A, "m" + A, "e" + A)
        assert await scan.unacked_inbox(LATER) == [(A, "m" + A), (B, "m" + B)]
        assert await scan.due_alerts(LATER) == [] and await scan.unscanned_audio(LATER, 1) == []
        assert await scan.reap_inbox(LATER) == []
        assert await scan.rejected_digest(NOW) == (1, 1)
    # the function set the household for its own loop and put it back: nothing stays set on the connection
    async with open_store(pg_db, Role.CORE, max_size=1) as store:
        await store.scan.lease_next(LATER)
        async with store._pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_setting('app.household_id', true)") in (None, "")


async def test_scan_is_limited_to_granted_functions(pg_db: PgDb):
    async with open_store(pg_db, Role.MODEL) as model, open_store(pg_db, Role.MIGRATOR) as mig:
        for store in (model, mig):
            with pytest.raises(PermissionError):
                store.scan  # noqa: B018
    async with open_store(pg_db, Role.EDGE) as edge, open_store(pg_db, Role.CORE) as core:
        with pytest.raises(PermissionError):
            await edge.scan.lease_next(LATER)
        with pytest.raises(PermissionError):
            await core.scan.reject_inbound("h", "r")


async def test_sender_lookup_returns_no_content(pg_db: PgDb):
    await seed_both(pg_db)
    async with open_store(pg_db, Role.EDGE) as edge:
        got = await edge.scan.resolve_sender("s" + A)
        assert got == (A, "e" + A) and all(type(x) is str for x in got)
        assert await edge.scan.resolve_sender("no-such-hmac") is None
        # the edge has no read on the identity table: the sealed address and person ref stay out of its reach
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with edge.tx(household=HouseholdId(A)) as tx:
                await tx.fetch("SELECT person_ref_enc FROM elder_identities")
