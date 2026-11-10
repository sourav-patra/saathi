"""LT-03: roles, grants, sequences and function privileges (M27; RQ-56a, 95a, 109; TQ-39, 41, 71).

Seeding uses the superuser (it bypasses row-level security); every check connects as the role under test.
"""

from __future__ import annotations

import re

import asyncpg
import pytest
from conftest import ALL_ROLES, PgDb

EDGE, CORE, MODEL, OWNER, READONLY = (f"saathi_{r}" for r in ("edge", "core", "model", "owner", "readonly"))
RUNTIME = (EDGE, CORE, MODEL, OWNER, READONLY)
SCHEMAS = "('public', 'control')"


async def connect(db: PgDb, role: str | None = None, household: str | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(db.dsn(role))
    if household is not None:
        await conn.execute("SELECT set_config('app.household_id', $1, false)", household)
    return conn


async def seed(db: PgDb) -> None:
    """Two households, each with an elder, an approver, an identity and one inbox row."""
    su = await connect(db)
    try:
        for h in ("h1", "h2"):
            await su.execute("INSERT INTO households (id, name) VALUES ($1, $2)", h, f"Household {h}")
            await su.execute("INSERT INTO elders (id, household_id, display_name) VALUES ($1, $2, 'Elder')", "e" + h, h)
            await su.execute(
                "INSERT INTO approvers (id, household_id, name, telegram_user_id) VALUES ($1, $2, 'Approver', $3)",
                "a" + h, h, 1000 + int(h[1]),
            )
            await su.execute(
                "INSERT INTO elder_identities (id, household_id, elder_id, channel, sender_hmac, person_ref,"
                " person_ref_enc, key_id) VALUES ($1, $2, $3, 'whatsapp', $4, $5, '\\x00', 'k1')",
                "i" + h, h, "e" + h, "hmac-" + h, "ref-" + h,
            )
            await su.execute(
                "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
                " payload_sha256) VALUES ($1, $2, $3, 'whatsapp', $4, $5, 'text', 'sha')",
                "m" + h, h, "wa:" + h, "hmac-" + h, "e" + h,
            )
    finally:
        await su.close()


async def denied(conn: asyncpg.Connection, sql: str, *args: object) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute(sql, *args)


async def test_core_cannot_update_or_delete_audit_log(pg_db: PgDb):
    await seed(pg_db)
    core = await connect(pg_db, CORE, "h1")
    try:
        seq = await core.fetchval("SELECT control.audit_append('r1', 'system:test', 'ev', NULL, NULL, 'k1', '{}')")
        assert seq is not None
        await denied(core, "UPDATE audit_log SET event = 'x'")
        await denied(core, "DELETE FROM audit_log")
        await denied(core, "TRUNCATE audit_log")
        await denied(core, "INSERT INTO audit_log (household_id, run_id, actor, event, key_id)"
                           " VALUES ('h1', 'r', 'system:x', 'e', 'k')")  # only the function appends
        await denied(core, "SELECT count(*) FROM audit_log")  # append only, and not readable by the core either
    finally:
        await core.close()
    su = await connect(pg_db)
    try:
        assert await su.fetchval("SELECT count(*) FROM audit_log") == 1
    finally:
        await su.close()
    # even the owner of the table cannot change a row through the policies: append-only means no UPDATE/DELETE policy
    mig = await connect(pg_db, "saathi_migrator", "h1")
    try:
        assert await mig.execute("UPDATE audit_log SET event = 'x'") == "UPDATE 0"
        assert await mig.execute("DELETE FROM audit_log") == "DELETE 0"
    finally:
        await mig.close()


async def test_model_cannot_write_approvals_or_control(pg_db: PgDb):
    await seed(pg_db)
    model = await connect(pg_db, MODEL, "h1")
    try:
        await denied(model, "INSERT INTO approvals (quote_id, approver_id, decision, household_id)"
                            " VALUES ('q', 'ah1', 'approve', 'h1')")
        await denied(model, "UPDATE approvals SET decision = 'reject'")
        await denied(model, "INSERT INTO control.signoffs (household_id, scope, granter, expires_at)"
                            " VALUES ('h1', 'SO-1', 'x', now() + interval '1 day')")
        await denied(model, "SELECT * FROM control.household_config")
        await denied(model, "SELECT * FROM control.household_keys")
        await denied(model, "UPDATE runtime_flags SET paused = false")
        await denied(model, "SELECT * FROM audit_log")
        await denied(model, "SELECT control.audit_append('r', 'system:x', 'e', NULL, NULL, 'k', '{}')")
        # what it may do: its own ledger tables
        await model.execute("INSERT INTO llm_spend (tier, model, cost_inr, household_id) VALUES ('t', 'm', 1, 'h1')")
        await model.execute("INSERT INTO llm_reservations (household_id, run_id, tier, reserved_inr, month)"
                            " VALUES ('h1', 'r', 't', 1, '2026-11')")
        await model.execute("UPDATE llm_reservations SET state = 'settled', settled_inr = 1")
        await denied(model, "UPDATE llm_reservations SET reserved_inr = 0")
    finally:
        await model.close()


EDGE_INSERT_TABLES = {"inbox", "raw_text", "rejected_inbound", "alert_events"}


async def test_edge_inserts_only_its_tables(pg_db: PgDb):
    await seed(pg_db)
    conn = await connect(pg_db)
    try:
        rows = await conn.fetch(
            f"""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE c.relkind IN ('r', 'p') AND n.nspname IN {SCHEMAS}
                   AND has_table_privilege('{EDGE}', c.oid, 'INSERT')"""
        )
        assert {r["relname"] for r in rows} == EDGE_INSERT_TABLES
        for priv in ("UPDATE", "DELETE", "TRUNCATE"):
            assert await conn.fetch(
                f"""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE c.relkind IN ('r', 'p') AND n.nspname IN {SCHEMAS}
                       AND has_table_privilege('{EDGE}', c.oid, '{priv}')"""
            ) == [], priv
    finally:
        await conn.close()
    edge = await connect(pg_db, EDGE, "h1")
    try:
        await edge.execute(
            "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
            " payload_sha256) VALUES ('m2', 'h1', 'wa:2', 'whatsapp', 'hmac-h1', 'eh1', 'text', 'sha')")
        await edge.execute("INSERT INTO raw_text (inbox_id, household_id, part, body_sealed, key_id, expires_at)"
                           " VALUES ('m2', 'h1', 'text', '\\x01', 'k1', now() + interval '30 days')")
        await edge.execute("INSERT INTO alert_events (id, household_id, elder_id, inbox_id, kind, origin, t0)"
                           " VALUES ('al1', 'h1', 'eh1', 'm2', 'emergency', 'radar_edge', now())")
        await edge.execute("INSERT INTO rejected_inbound (sender_hmac, reason) VALUES ('unknown', 'no_identity')")
        # a duplicate shows as the tag INSERT 0 0 with no SELECT grant (LLD data rule 3)
        assert await edge.execute(
            "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
            " payload_sha256) VALUES ('m3', 'h1', 'wa:2', 'whatsapp', 'hmac-h1', 'eh1', 'text', 'sha')"
            " ON CONFLICT DO NOTHING") == "INSERT 0 0"
        await denied(edge, "INSERT INTO requests (id, household_id, channel, kind) VALUES ('r', 'h1', 'c', 'k')")
        await denied(edge, "INSERT INTO audit_log (household_id, run_id, actor, event, key_id)"
                           " VALUES ('h1', 'r', 'system:x', 'e', 'k')")
        await denied(edge, "UPDATE inbox SET state = 'dead'")
        await denied(edge, "DELETE FROM inbox")
    finally:
        await edge.close()


async def test_edge_cannot_read_raw_text(pg_db: PgDb):
    await seed(pg_db)
    edge = await connect(pg_db, EDGE, "h1")
    try:
        for table in ("raw_text", "inbox", "elder_identities", "elders", "households", "rejected_inbound",
                      "alert_events"):
            await denied(edge, f"SELECT * FROM {table}")
        # the only column it may read: the public half of the content key and its metadata
        await edge.execute("SELECT household_id, purpose, key_id, public_key, retired_at FROM control.household_keys")
        await denied(edge, "SELECT wrapped_key FROM control.household_keys")
    finally:
        await edge.close()


async def test_only_readonly_has_bypassrls(pg_db: PgDb):
    conn = await connect(pg_db)
    try:
        rows = await conn.fetch("SELECT rolname FROM pg_roles WHERE rolbypassrls AND rolname LIKE 'saathi\\_%'")
        assert [r["rolname"] for r in rows] == [READONLY]
        assert await conn.fetch(
            "SELECT 1 FROM pg_roles WHERE (rolsuper OR rolcreaterole) AND rolname LIKE 'saathi\\_%'") == []
    finally:
        await conn.close()
    await seed(pg_db)
    ro = await connect(pg_db, READONLY)  # no household set: it still sees every household
    try:
        assert await ro.fetchval("SELECT count(*) FROM households") == 2
        assert await ro.fetchval("SELECT count(*) FROM inbox") == 2
        await denied(ro, "UPDATE households SET name = 'x'")
        await denied(ro, "INSERT INTO households (id, name) VALUES ('h9', 'x')")
    finally:
        await ro.close()


async def test_no_runtime_role_owns_a_table(pg_db: PgDb):
    conn = await connect(pg_db)
    try:
        rows = await conn.fetch(
            f"""SELECT c.relname, pg_get_userbyid(c.relowner) AS owner FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE c.relkind IN ('r', 'p', 'S', 'v', 'm') AND n.nspname IN {SCHEMAS}"""
        )
        assert rows and {r["owner"] for r in rows} == {"saathi_migrator"}
        # functions belong to the two no-login roles and the migrator, never to a runtime role
        fns = await conn.fetch(
            "SELECT p.proname, pg_get_userbyid(p.proowner) AS owner FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'control'"
        )
        owners = {r["proname"]: r["owner"] for r in fns}
        assert owners["audit_append"] == "saathi_audit"
        assert owners["apply_tenant_rls"] == "saathi_migrator"
        for name, owner in owners.items():
            if name not in ("audit_append", "apply_tenant_rls"):
                assert owner == "saathi_scan", name
        # no runtime role can create objects in either schema
        for role in RUNTIME:
            for schema in ("public", "control"):
                assert not await conn.fetchval("SELECT has_schema_privilege($1, $2, 'CREATE')", role, schema), role
        for role in ("saathi_scan", "saathi_audit"):  # the temporary CREATE of the hand-over is revoked again
            assert not await conn.fetchval("SELECT has_schema_privilege($1, 'control', 'CREATE')", role), role
    finally:
        await conn.close()


async def test_every_bigserial_sequence_usable_by_its_inserting_role(pg_db: PgDb):
    conn = await connect(pg_db)
    try:
        rows = await conn.fetch(
            f"""SELECT s.oid, n.nspname || '.' || s.relname AS seq, t.oid AS tbl, t.relname AS tname
                  FROM pg_class s JOIN pg_namespace n ON n.oid = s.relnamespace
                  JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass AND d.deptype = 'a'
                  JOIN pg_class t ON t.oid = d.refobjid
                 WHERE s.relkind = 'S' AND n.nspname IN {SCHEMAS}"""
        )
        got = {r["seq"].split(".", 1)[1] if r["seq"].startswith("public.") else r["seq"] for r in rows}
        assert got == {
            "rejected_inbound_id_seq", "alert_attempts_id_seq", "approvals_id_seq", "commits_id_seq",
            "profile_facts_id_seq", "llm_spend_id_seq", "llm_reservations_id_seq",
            "control.signoffs_id_seq", "control.consent_events_id_seq",
        }
        # audit_log.seq is an identity column: owned by identity, not serial, and no role needs a grant
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_class s JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'i'"
            " WHERE s.relkind = 'S' AND d.refobjid = 'audit_log'::regclass") == 1
        inserters = 0
        for r in rows:
            for role in RUNTIME:
                if await conn.fetchval("SELECT has_table_privilege($1, $2::oid, 'INSERT')", role, r["tbl"]):
                    inserters += 1
                    assert await conn.fetchval("SELECT has_sequence_privilege($1, $2::oid, 'USAGE')", role, r["oid"]), (
                        f"{role} inserts into {r['tname']} but cannot use {r['seq']}"
                    )
            for role in (READONLY,):  # a role that does not insert holds no USAGE it does not need
                assert not await conn.fetchval("SELECT has_sequence_privilege($1, $2::oid, 'USAGE')", role, r["oid"])
        assert inserters >= len(rows)
    finally:
        await conn.close()
    # and it works for real: each inserting role takes a value from its own sequence
    await seed(pg_db)
    edge = await connect(pg_db, EDGE, "h1")
    try:
        await edge.execute("INSERT INTO rejected_inbound (sender_hmac, reason) VALUES ('s', 'r')")
    finally:
        await edge.close()
    owner = await connect(pg_db, OWNER, "h1")
    try:
        await owner.execute("INSERT INTO control.signoffs (household_id, scope, granter, expires_at)"
                            " VALUES ('h1', 'SO-1', 'x', now() + interval '1 day')")
        await owner.execute(
            "INSERT INTO control.consent_events (household_id, subject, use, recipient, event, capacity, language,"
            " how, notice_text_sha256, recorded_by) VALUES ('h1', 'elder', 'u', 'r', 'grant', 'self', 'en',"
            " 'spoken', 'sha', 'x')")
    finally:
        await owner.close()
    core = await connect(pg_db, CORE, "h1")
    try:
        await core.execute("INSERT INTO profile_facts (household_id, key, value, source) VALUES ('h1', 'k', '1', 'c')")
    finally:
        await core.close()


async def test_public_holds_no_execute_on_control(pg_db: PgDb):
    conn = await connect(pg_db)
    try:
        rows = await conn.fetch(
            """SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'control'
                  AND (p.proacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0))"""
        )
        assert [r["proname"] for r in rows] == []
        # the default privileges keep it that way for a function a later migration adds
        mig = await asyncpg.connect(pg_db.dsn("saathi_migrator"))
        try:
            await mig.execute("CREATE FUNCTION control.planted() RETURNS int LANGUAGE sql AS 'SELECT 1'")
            await mig.execute("CREATE FUNCTION public.planted() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        finally:
            await mig.close()
        planted = await conn.fetch(
            "SELECT n.nspname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE p.proname = 'planted' AND (p.proacl IS NULL OR EXISTS"
            " (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0))"
        )
        assert planted == []
    finally:
        await conn.close()


async def test_scan_can_list_households(pg_db: PgDb):
    await seed(pg_db)
    su = await connect(pg_db)
    try:
        await su.execute("SET ROLE saathi_scan")  # no household set: the scan policies read every row
        assert [r["id"] for r in await su.fetch("SELECT id FROM households ORDER BY id")] == ["h1", "h2"]
        assert await su.fetchval("SELECT count(*) FROM elder_identities") == 2
        assert await su.fetchval("SELECT count(*) FROM approvers") == 2
        await su.execute("RESET ROLE")
        await su.execute("SET ROLE saathi_owner")  # the owner reads every household through owner_hh
        assert await su.fetchval("SELECT count(*) FROM households") == 2
        await su.execute("RESET ROLE")
        await su.execute("SET ROLE saathi_core")  # core reads through the tenant policy only: nothing unset
        assert await su.fetchval("SELECT count(*) FROM households") == 0
    finally:
        await su.close()
    core = await connect(pg_db, CORE)  # the scan functions return ids from every household
    try:
        rows = await core.fetch("SELECT * FROM control.lease_next(now(), 60)")
        assert len(rows) == 1 and set(rows[0].keys()) == {"household_id", "inbox_id", "elder_id", "lease_token"}
        assert await core.fetchval("SELECT current_setting('app.household_id', true)") in (None, "")  # restored
    finally:
        await core.close()


EDGE_FUNCTIONS = {"resolve_sender", "rate_state", "beat"}


async def test_edge_can_call_only_resolve_sender_rate_state_beat(pg_db: PgDb):
    await seed(pg_db)
    conn = await connect(pg_db)
    try:
        rows = await conn.fetch(
            f"""SELECT p.proname, has_function_privilege('{EDGE}', p.oid, 'EXECUTE') AS ok FROM pg_proc p
                  JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'control'"""
        )
        assert {r["proname"] for r in rows if r["ok"]} == EDGE_FUNCTIONS
        assert {r["proname"] for r in rows} >= EDGE_FUNCTIONS | {"lease_next", "audit_append", "due_alerts"}
        # model and readonly call none of them
        for role in (MODEL, READONLY):
            assert [r for r in await conn.fetch(
                f"""SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = 'control' AND has_function_privilege('{role}', p.oid, 'EXECUTE')""")] == [], role
    finally:
        await conn.close()
    edge = await connect(pg_db, EDGE, "h1")
    try:
        got = await edge.fetch("SELECT * FROM control.resolve_sender('hmac-h1')")
        assert [(r["household_id"], r["elder_id"]) for r in got] == [("h1", "eh1")]
        assert await edge.fetch("SELECT * FROM control.resolve_sender('nobody')") == []
        assert await edge.fetchval("SELECT control.rate_state(now(), 'eh1')") is None
        await edge.fetch("SELECT * FROM control.beat('edge', now())")
        with pytest.raises(asyncpg.RaiseError, match="does not match the calling role"):
            await edge.fetch("SELECT * FROM control.beat('core', now())")  # the edge cannot write the core's beat
        await denied(edge, "SELECT * FROM control.lease_next(now(), 60)")
        await denied(edge, "SELECT control.audit_append('r', 'system:x', 'e', NULL, NULL, 'k', '{}')")
        await denied(edge, "SELECT * FROM control.resolve_approver(1001)")
    finally:
        await edge.close()
    core = await connect(pg_db, CORE)
    try:
        core_beat = await core.fetch("SELECT * FROM control.beat('core', now())")
        assert core_beat[0]["core_status"] == "ok"
        with pytest.raises(asyncpg.RaiseError):
            await core.fetch("SELECT * FROM control.beat('edge', now())")
    finally:
        await core.close()


async def test_owner_can_create_household_elder_approver_identity(pg_db: PgDb):
    owner = await connect(pg_db, OWNER, "h7")
    try:
        await owner.execute("INSERT INTO households (id, name) VALUES ('h7', 'Household h7')")
        await owner.execute("INSERT INTO elders (id, household_id, display_name) VALUES ('e7', 'h7', 'Elder')")
        await owner.execute("INSERT INTO approvers (id, household_id, name, telegram_user_id)"
                            " VALUES ('a7', 'h7', 'Approver', 7007)")
        await owner.execute(
            "INSERT INTO elder_identities (id, household_id, elder_id, channel, sender_hmac, person_ref,"
            " person_ref_enc, key_id) VALUES ('i7', 'h7', 'e7', 'whatsapp', 'hmac-7', 'ref-7', '\\x00', 'k1')")
        await owner.execute("INSERT INTO control.household_config (household_id, version, values, created_by)"
                            " VALUES ('h7', 1, '{}', 'owner')")
        await owner.execute("INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
                            " VALUES ('h7', 'audit', 'k1', '\\x00')")
        assert await owner.execute("UPDATE elder_identities SET active = false") == "UPDATE 1"
        assert await owner.execute(
            "UPDATE control.household_keys SET retired_at = now()") == "UPDATE 1"
        # the owner has no more than that
        await denied(owner, "DELETE FROM households")
        await denied(owner, "UPDATE elders SET display_name = 'x'")
        await denied(owner, "UPDATE elder_identities SET sender_hmac = 'x'")
        await denied(owner, "INSERT INTO requests (id, household_id, channel, kind) VALUES ('r', 'h7', 'c', 'k')")
        await denied(owner, "UPDATE control.household_config SET values = '{}'")
        await denied(owner, "UPDATE control.consent_events SET use = 'x'")
        # row-level security still binds the owner to the household it names
        with pytest.raises(asyncpg.PostgresError):
            await owner.execute("INSERT INTO elders (id, household_id, display_name) VALUES ('e8', 'h8', 'x')")
    finally:
        await owner.close()


async def test_owner_can_audit_append(pg_db: PgDb):
    await seed(pg_db)
    owner = await connect(pg_db, OWNER, "h1")
    try:
        s1 = await owner.fetchval(
            "SELECT control.audit_append('r1', 'system:admin', 'household.create', 'h1', NULL, 'k1', '{}')")
        s2 = await owner.fetchval("SELECT control.audit_append('r1', 'approver:a1', 'ev', NULL, NULL, 'k1', NULL)")
        assert s2 > s1
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.fetchval("SELECT control.audit_append('r1', 'robot', 'ev', NULL, NULL, 'k1', '{}')")
        await denied(owner, "SELECT * FROM audit_log")  # append only: even the owner cannot read it back
        await denied(owner, "UPDATE audit_log SET event = 'x'")
    finally:
        await owner.close()
    nohh = await connect(pg_db, OWNER)
    try:
        with pytest.raises(asyncpg.RaiseError, match="TenantNotSet"):
            await nohh.fetchval("SELECT control.audit_append('r1', 'system:admin', 'ev', NULL, NULL, 'k1', '{}')")
    finally:
        await nohh.close()
    su = await connect(pg_db)
    try:
        rows = await su.fetch("SELECT household_id, actor, detail::text AS detail FROM audit_log ORDER BY seq")
        assert [(r["household_id"], r["actor"], r["detail"]) for r in rows] == [
            ("h1", "system:admin", "{}"), ("h1", "approver:a1", "{}")]
    finally:
        await su.close()


async def test_signoffs_core_reads_only_and_owner_revokes_only(pg_db: PgDb):
    await seed(pg_db)
    owner = await connect(pg_db, OWNER, "h1")
    try:
        await owner.execute("INSERT INTO control.signoffs (household_id, scope, granter, expires_at)"
                            " VALUES ('h1', 'SO-1', 'x', now() + interval '1 day')")
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.execute("INSERT INTO control.signoffs (household_id, scope, granter, expires_at)"
                                " VALUES ('h1', 'SO-9', 'x', now() + interval '1 day')")
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.execute("INSERT INTO control.signoffs (household_id, scope, granter, expires_at)"
                                " VALUES ('h1', 'SO-1', 'x', now() - interval '1 day')")
        await denied(owner, "UPDATE control.signoffs SET expires_at = now() + interval '900 days'")
        await denied(owner, "DELETE FROM control.signoffs")
        assert await owner.execute("UPDATE control.signoffs SET revoked_at = now()") == "UPDATE 1"
    finally:
        await owner.close()
    core = await connect(pg_db, CORE, "h1")
    try:
        assert await core.fetchval("SELECT count(*) FROM control.signoffs") == 1
        await denied(core, "INSERT INTO control.signoffs (household_id, scope, granter, expires_at)"
                           " VALUES ('h1', 'SO-1', 'x', now() + interval '1 day')")
        await denied(core, "UPDATE control.signoffs SET revoked_at = NULL")
        await denied(core, "DELETE FROM control.signoffs")
    finally:
        await core.close()
    edge = await connect(pg_db, EDGE, "h1")
    try:
        await denied(edge, "SELECT * FROM control.signoffs")
    finally:
        await edge.close()


async def test_migrator_is_not_superuser(pg_db: PgDb):
    conn = await connect(pg_db, "saathi_migrator")
    try:
        row = await conn.fetchrow(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication FROM pg_roles"
            " WHERE rolname = current_user")
        assert not any(row.values())
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("CREATE ROLE saathi_rogue")
        # the migrator holds the two function-owner roles by SET only, never by inheritance
        assert await conn.fetchval("SELECT pg_has_role('saathi_scan', 'USAGE')") is False
        assert await conn.fetchval("SELECT pg_has_role('saathi_scan', 'SET')") is True
    finally:
        await conn.close()
    assert set(ALL_ROLES) >= {EDGE, CORE, MODEL, OWNER, READONLY}


# --- the whole grant matrix, typed from LLD grants parts 2 to 5 (not read from the database) --------------------

_REQUEST_COLS = ("state state_reason questions_asked urgent expires_at superseded_by slots quote_id plan_step intent"
                 " confidence tier_used radar trace_id").split()
_ALERT_COLS = ("state stage next_due_at acked_by acked_at false_alarm blocked_reason followup_due_at"
               " last_elder_clip_at").split()
_HOUSEHOLD_ADMIN = ("households elders approvers elder_identities control.household_config control.consent_events"
                    " control.household_keys control.signoffs").split()
# Every table of schemas public and control when 004 granted "ALL TABLES" to readonly, and the four 005 added.
_EVERY_TABLE = (
    "households elders approvers elder_identities inbox raw_text rejected_inbound alert_events alert_attempts"
    " tg_updates requests quotes commits approvals addresses profile_facts runtime_flags audit_log schema_migrations"
    " llm_spend llm_reservations voice_clips control.heartbeats control.signoffs control.consent_events"
    " control.household_config control.household_keys"
).split()

# role -> {"table": {obj: {privileges}}, "column": {"table.col": {privileges}}, "seq": {...}, "fn": {...}}
EXPECTED: dict[str, dict[str, dict[str, set[str]]]] = {
    "saathi_edge": {
        "table": {t: {"INSERT"} for t in ("inbox", "raw_text", "rejected_inbound", "alert_events")},
        "column": {f"control.household_keys.{c}": {"SELECT"}
                   for c in ("household_id", "purpose", "key_id", "public_key", "retired_at")},
        "seq": {"rejected_inbound_id_seq": {"USAGE"}},
        "fn": {"control.resolve_sender(text)": {"EXECUTE"}, "control.rate_state(timestamp with time zone,text)":
               {"EXECUTE"}, "control.beat(text,timestamp with time zone,text)": {"EXECUTE"}},
    },
    "saathi_core": {
        "table": {
            **{t: {"SELECT"} for t in (
                "households elders approvers elder_identities inbox raw_text alert_events alert_attempts tg_updates"
                " requests quotes commits approvals addresses profile_facts runtime_flags control.signoffs"
                " control.consent_events control.household_config control.household_keys voice_clips").split()},
            **{t: {"SELECT", "INSERT"} for t in (
                "raw_text alert_events alert_attempts tg_updates requests quotes commits approvals addresses"
                " profile_facts runtime_flags").split()},
        },
        "column": {
            **{f"inbox.{c}": {"UPDATE"} for c in
               "state attempts lease_until lease_token acked_at radar_at not_before".split()},
            **{f"alert_events.{c}": {"UPDATE"} for c in _ALERT_COLS},
            **{f"alert_attempts.{c}": {"UPDATE"} for c in ("status", "sent_at", "provider_ref_hmac")},
            **{f"requests.{c}": {"UPDATE"} for c in _REQUEST_COLS},
            **{f"runtime_flags.{c}": {"UPDATE"} for c in ("paused", "paused_by", "reason", "updated_at")},
        },
        "seq": {s: {"USAGE"} for s in
                ("alert_attempts_id_seq", "approvals_id_seq", "commits_id_seq", "profile_facts_id_seq")},
        "fn": {f"control.{f}": {"EXECUTE"} for f in (
            "resolve_sender(text)", "resolve_approver(bigint)", "lease_next(timestamp with time zone,integer)",
            "reap_inbox(timestamp with time zone)", "unacked_inbox(timestamp with time zone,integer)",
            "rate_state(timestamp with time zone,text)", "due_alerts(timestamp with time zone,integer)",
            "unscanned_audio(timestamp with time zone,integer)", "rejected_digest(timestamp with time zone)",
            "audit_append(text,text,text,text,text,text,jsonb)", "beat(text,timestamp with time zone,text)")},
    },
    "saathi_model": {
        "table": {"llm_spend": {"SELECT", "INSERT"}, "llm_reservations": {"SELECT", "INSERT"}},
        "column": {"llm_reservations.state": {"UPDATE"}, "llm_reservations.settled_inr": {"UPDATE"}},
        "seq": {"llm_spend_id_seq": {"USAGE"}, "llm_reservations_id_seq": {"USAGE"}},
        "fn": {},
    },
    "saathi_owner": {
        "table": {**{t: {"SELECT", "INSERT"} for t in _HOUSEHOLD_ADMIN}, "voice_clips": {"SELECT", "INSERT"}},
        "column": {
            "elder_identities.active": {"UPDATE"},
            "control.household_keys.retired_at": {"UPDATE"}, "control.household_keys.wrapped_key": {"UPDATE"},
            "control.signoffs.revoked_at": {"UPDATE"},
            "voice_clips.reviewed_by": {"UPDATE"}, "voice_clips.reviewed_at": {"UPDATE"},
            "voice_clips.receipt_ref": {"UPDATE"},
        },
        "seq": {"control.signoffs_id_seq": {"USAGE"}, "control.consent_events_id_seq": {"USAGE"}},
        "fn": {"control.audit_append(text,text,text,text,text,text,jsonb)": {"EXECUTE"},
               "control.rejected_digest(timestamp with time zone)": {"EXECUTE"}},
    },
    "saathi_readonly": {
        "table": {t: {"SELECT"} for t in _EVERY_TABLE},
        "column": {}, "seq": {}, "fn": {},
    },
    "saathi_scan": {
        "table": {
            **{t: {"SELECT"} for t in ("households inbox alert_events raw_text control.household_config"
                                       " elder_identities approvers rejected_inbound").split()},
            "control.heartbeats": {"SELECT", "INSERT", "UPDATE"},
        },
        "column": {f"inbox.{c}": {"UPDATE"} for c in ("state", "attempts", "lease_until", "lease_token")},
        "seq": {}, "fn": {},
    },
    "saathi_audit": {
        "table": {"audit_log": {"INSERT"}},
        "column": {"audit_log.seq": {"SELECT"}},
        "seq": {}, "fn": {},
    },
    "saathi_migrator": {"table": {}, "column": {}, "seq": {}, "fn": {}},  # the owner: nothing beyond ownership
}
# schema control (LLD grants 2): usage for every role that calls a function or reads a table there; model has none.
SCHEMA_USAGE = {"saathi_edge", "saathi_core", "saathi_owner", "saathi_readonly", "saathi_scan", "saathi_audit"}

_ACL_SQL = {
    "table": """SELECT CASE WHEN n.nspname = 'public' THEN c.relname ELSE n.nspname || '.' || c.relname END AS obj,
                       a.grantee, a.privilege_type, a.is_grantable
                  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
                 WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('public', 'control') AND a.grantee <> c.relowner""",
    "column": """SELECT CASE WHEN n.nspname = 'public' THEN c.relname ELSE n.nspname || '.' || c.relname END
                       || '.' || t.attname AS obj, a.grantee, a.privilege_type, a.is_grantable
                  FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(t.attacl) a
                 WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('public', 'control') AND NOT t.attisdropped""",
    "seq": """SELECT CASE WHEN n.nspname = 'public' THEN c.relname ELSE n.nspname || '.' || c.relname END AS obj,
                     a.grantee, a.privilege_type, a.is_grantable
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
               WHERE c.relkind = 'S' AND n.nspname IN ('public', 'control') AND a.grantee <> c.relowner""",
    "fn": """SELECT p.oid::regprocedure::text AS obj, a.grantee, a.privilege_type, a.is_grantable
               FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) a
              WHERE n.nspname IN ('public', 'control') AND a.grantee <> p.proowner""",
}


async def actual_grants(conn: asyncpg.Connection) -> dict[str, dict[str, dict[str, set[str]]]]:
    """Every explicit ACL entry on a table, column, sequence or function, by grantee name (PUBLIC included)."""
    names = {r["oid"]: r["rolname"] for r in await conn.fetch("SELECT oid, rolname FROM pg_roles")}
    names[0] = "PUBLIC"
    out: dict[str, dict[str, dict[str, set[str]]]] = {}
    for kind, sql in _ACL_SQL.items():
        for r in await conn.fetch(sql):
            who = names[r["grantee"]]
            priv = r["privilege_type"] + (" WITH GRANT OPTION" if r["is_grantable"] else "")
            out.setdefault(who, {k: {} for k in _ACL_SQL})[kind].setdefault(r["obj"], set()).add(priv)
    return out


def grant_diff(actual: dict, role: str) -> list[str]:
    """Differences between the hand-written matrix and the database for one role: `+` extra, `-` missing."""
    have = actual.get(role, {k: {} for k in _ACL_SQL})
    diffs: list[str] = []
    for kind in _ACL_SQL:
        want = EXPECTED[role][kind]
        for obj in sorted(set(want) | set(have[kind])):
            for priv in sorted(want.get(obj, set()) ^ have[kind].get(obj, set())):
                diffs.append(("+" if priv in have[kind].get(obj, set()) else "-") + f" {kind} {obj} {priv}")
    return diffs


async def grant_problems(conn: asyncpg.Connection) -> list[str]:
    actual = await actual_grants(conn)
    problems = [f"{role}: {d}" for role in EXPECTED for d in grant_diff(actual, role)]
    stray = sorted(set(actual) - set(EXPECTED))  # a grantee outside the eight roles, PUBLIC included
    problems += [f"unexpected grantee {who}: {actual[who]}" for who in stray]
    usage = {r["grantee"] for r in await conn.fetch(
        "SELECT pg_get_userbyid(a.grantee) AS grantee FROM pg_namespace n, aclexplode(n.nspacl) a"
        " WHERE n.nspname = 'control' AND a.privilege_type = 'USAGE' AND a.grantee <> n.nspowner")}
    create = await conn.fetch(
        "SELECT a.grantee FROM pg_namespace n, aclexplode(n.nspacl) a"
        " WHERE n.nspname = 'control' AND a.privilege_type = 'CREATE' AND a.grantee <> n.nspowner")
    if usage != SCHEMA_USAGE:
        problems.append(f"schema control USAGE: {sorted(usage ^ SCHEMA_USAGE)}")
    if create:
        problems.append("schema control CREATE held by a non-owner")
    return problems


async def test_grant_matrix_matches_the_lld(pg_db: PgDb):
    conn = await connect(pg_db)
    try:
        assert await grant_problems(conn) == []
        # the matrix is not vacuous: it covers every kind for several roles
        assert sum(len(EXPECTED[r][k]) for r in EXPECTED for k in _ACL_SQL) > 150
    finally:
        await conn.close()


# the seven extra grants of the review, plus one revoke (a missing grant must fail as well)
PLANTED_GRANTS = {
    "core_delete_requests": "GRANT DELETE ON requests TO saathi_core",
    "core_update_inbox_household_id": "GRANT UPDATE (household_id) ON inbox TO saathi_core",
    "model_select_raw_text": "GRANT SELECT ON raw_text TO saathi_model",
    "sequence_usage_to_wrong_role": "GRANT USAGE ON SEQUENCE approvals_id_seq TO saathi_edge",
    "sequence_update_to_core": "GRANT UPDATE ON SEQUENCE commits_id_seq TO saathi_core",
    "owner_execute_lease_next": "GRANT EXECUTE ON FUNCTION control.lease_next(timestamptz, int) TO saathi_owner",
    "readonly_insert_households": "GRANT INSERT ON households TO saathi_readonly",
    "revoke_edge_insert_inbox": "REVOKE INSERT ON inbox FROM saathi_edge",
}


@pytest.mark.parametrize("name", sorted(PLANTED_GRANTS))
async def test_planted_grant_fails(pg_db: PgDb, name: str):
    su = await connect(pg_db)  # the superuser grants as the object's owner
    try:
        assert await grant_problems(su) == []
        await su.execute(PLANTED_GRANTS[name])
        problems = await grant_problems(su)
        assert len(problems) == 1, problems
    finally:
        await su.close()


# --- every control function is SECURITY DEFINER with a pinned search_path ---------------------------------------------

DEFINER_FUNCTIONS = {
    "resolve_sender", "resolve_approver", "rejected_digest", "rate_state", "audit_append", "lease_next", "reap_inbox",
    "unacked_inbox", "due_alerts", "unscanned_audio", "beat",
}
PINNED = ["search_path=pg_catalog, public"]


async def function_attribute_problems(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT p.proname, p.prosecdef, p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'control' AND p.proname <> 'apply_tenant_rls' ORDER BY 1")
    problems = [f"{r['proname']}: not SECURITY DEFINER" for r in rows if not r["prosecdef"]]
    problems += [f"{r['proname']}: proconfig {r['proconfig']}" for r in rows if r["proconfig"] != PINNED]
    names = {r["proname"] for r in rows}
    if names != DEFINER_FUNCTIONS:
        problems.append(f"function set differs: {sorted(names ^ DEFINER_FUNCTIONS)}")
    return problems


async def test_control_functions_are_definer_with_pinned_search_path(pg_db: PgDb):
    conn = await connect(pg_db)
    try:
        assert await function_attribute_problems(conn) == []
    finally:
        await conn.close()


PLANTED_ATTRIBUTES = {
    "security_invoker": "ALTER FUNCTION control.resolve_sender(text) SECURITY INVOKER",
    "search_path_dropped": "ALTER FUNCTION control.lease_next(timestamptz, int) RESET search_path",
    "public_first": "ALTER FUNCTION control.audit_append(text, text, text, text, text, text, jsonb)"
                    " SET search_path = public, pg_catalog",
    "search_path_with_extra_schema": "ALTER FUNCTION control.beat(text, timestamptz, text)"
                                     " SET search_path = pg_catalog, public, control",
}


@pytest.mark.parametrize("name", sorted(PLANTED_ATTRIBUTES))
async def test_planted_function_attribute_fails(pg_db: PgDb, name: str):
    su = await connect(pg_db)
    try:
        assert await function_attribute_problems(su) == []
        await su.execute(PLANTED_ATTRIBUTES[name])
        assert len(await function_attribute_problems(su)) == 1
    finally:
        await su.close()


# --- a looping function gives the caller's household setting back, inside a transaction -------------------------------

LOOPING_CALLS = {
    "lease_next": "SELECT * FROM control.lease_next(now(), 60)",
    "reap_inbox": "SELECT * FROM control.reap_inbox(now())",
    "unacked_inbox": "SELECT * FROM control.unacked_inbox(now(), 10)",
    "due_alerts": "SELECT * FROM control.due_alerts(now(), 10)",
    "unscanned_audio": "SELECT * FROM control.unscanned_audio(now(), 60)",
}
_RESTORE = re.compile(r"PERFORM set_config\('app\.household_id', coalesce\(saved, ''\), true\);")


async def household_after(db: PgDb, call: str) -> str | None:
    """Set the household to h1 in a transaction, run the call, and read the setting in the same transaction.

    Only h2 has anything to lease, so a function that loops to h2 and forgets to restore leaves h2 behind.
    """
    su = await connect(db)
    try:
        await su.execute("UPDATE inbox SET state = 'dead' WHERE household_id = 'h1'")
    finally:
        await su.close()
    core = await connect(db, CORE)
    try:
        async with core.transaction():
            await core.execute("SELECT set_config('app.household_id', 'h1', false)")
            await core.fetch(call)
            return await core.fetchval("SELECT current_setting('app.household_id', true)")
    finally:
        await core.close()


@pytest.mark.parametrize("fn", sorted(LOOPING_CALLS))
async def test_looping_function_restores_the_household_setting(pg_db: PgDb, fn: str):
    await seed(pg_db)
    assert await household_after(pg_db, LOOPING_CALLS[fn]) == "h1"


@pytest.mark.parametrize("fn", sorted(LOOPING_CALLS))
async def test_function_without_the_restore_fails(pg_db: PgDb, fn: str):
    await seed(pg_db)
    su = await connect(pg_db)
    try:
        body = await su.fetchval(f"SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = '{fn}'")
        stripped, n = _RESTORE.subn("NULL;", body)
        assert n >= 1
        await su.execute(stripped)  # CREATE OR REPLACE keeps the owner and the grants
    finally:
        await su.close()
    assert await household_after(pg_db, LOOPING_CALLS[fn]) == "h2"  # the setting leaked from the last household


async def test_lease_next_without_its_early_return_restore_fails(pg_db: PgDb):
    await seed(pg_db)
    su = await connect(pg_db)
    try:
        body = await su.fetchval("SELECT pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.proname = 'lease_next'")
        early = body.replace(
            "PERFORM set_config('app.household_id', coalesce(saved, ''), true);\n        RETURN NEXT; RETURN;",
            "RETURN NEXT; RETURN;")
        assert early != body
        await su.execute(early)
    finally:
        await su.close()
    assert await household_after(pg_db, LOOPING_CALLS["lease_next"]) == "h2"
