"""LT-01: schema shape, row-level security and the migrator (M27; RQ-38, 109, 111a)."""

from __future__ import annotations

import re
from pathlib import Path

import asyncpg
import pytest
from conftest import ALL_ROLES, INIT_SCRIPT, SCHEMA_DIR, TEMPLATE_COMMENT, TEMPLATE_DB, PgDb, _dsn, assert_throwaway

from saathi.store.migrate import MigrationError, migrate

# LLD data part, rule 6: tables with no household column.
TENANTLESS = {"rejected_inbound", "voice_clips", "heartbeats", "schema_migrations"}

_TABLES = """
SELECT c.oid, n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity,
       EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'household_id'
                 AND NOT a.attisdropped) AS has_household
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('public', 'control')
 ORDER BY n.nspname, c.relname
"""


APPEND_ONLY = {"audit_log", "consent_events"}  # select and insert policies only (LLD data rule 2)
# The only policies a household table may carry besides its tenant policies: the scan and owner reads (LLD grants 2).
ALLOWED_ROLE_POLICIES = {
    ("households", "scan_hh"), ("households", "owner_hh"),
    ("elder_identities", "scan_ident"), ("approvers", "scan_appr"),
}

_POLICIES = """
SELECT p.polname, p.polcmd::text AS cmd, p.polpermissive, p.polroles = '{0}'::oid[] AS is_public,
       pg_get_expr(p.polqual, p.polrelid) AS using_expr, pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
  FROM pg_policy p WHERE p.polrelid = $1
"""


async def rls_violations(conn: asyncpg.Connection) -> list[str]:
    """Household tables (a household_id column, or `households` itself) without forced RLS or an exact tenant policy.

    Per command there must be exactly one permissive PUBLIC policy whose expression is
    `(<col> = current_setting('app.household_id'::text, true))` (USING for select, update and delete, WITH CHECK for
    insert and update). The command set is exact: {select, insert} for an append-only table, all four otherwise. Any
    other policy is a violation, except the four scan and owner read policies of LLD grants section 2.
    """
    bad: list[str] = []
    for t in await conn.fetch(_TABLES):
        if not (t["has_household"] or t["relname"] == "households"):
            continue
        col = "id" if t["relname"] == "households" else "household_id"
        want = f"({col} = current_setting('app.household_id'::text, true))"
        tenant: dict[str, list[asyncpg.Record]] = {}
        extra = False
        for p in await conn.fetch(_POLICIES, t["oid"]):
            if (t["relname"], p["polname"]) in ALLOWED_ROLE_POLICIES:
                extra = extra or p["is_public"] or p["cmd"] != "r" or p["using_expr"] != "true"
                continue
            tenant.setdefault(p["cmd"], []).append(p)
        need = {"r", "a"} if t["relname"] in APPEND_ONLY else {"r", "a", "w", "d"}
        ok = t["relrowsecurity"] and t["relforcerowsecurity"] and not extra and set(tenant) == need
        for cmd, ps in tenant.items():
            if len(ps) != 1:
                ok = False
                continue
            p = ps[0]
            using_ok = p["using_expr"] == want if cmd in ("r", "w", "d") else p["using_expr"] is None
            check_ok = p["check_expr"] == want if cmd in ("a", "w") else p["check_expr"] is None
            ok = ok and p["polpermissive"] and p["is_public"] and using_ok and check_ok
        if not ok:
            bad.append(f"{t['nspname']}.{t['relname']}")
    return bad


async def key_id_violations(conn: asyncpg.Connection) -> list[str]:
    """Encrypted columns (*_enc, *_sealed) and the audit keyed hash need a key_id column in the same table."""
    rows = await conn.fetch(
        """SELECT n.nspname, c.relname, a.attname,
                  EXISTS (SELECT 1 FROM pg_attribute k WHERE k.attrelid = c.oid AND k.attname = 'key_id'
                            AND NOT k.attisdropped) AS has_key
             FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('public', 'control') AND a.attnum > 0
              AND NOT a.attisdropped AND (a.attname ~ '(_enc|_sealed)$' OR a.attname = 'payload_hmac')"""
    )
    return [f"{r['nspname']}.{r['relname']}.{r['attname']}" for r in rows if not r["has_key"]]


async def _connect(db: PgDb, role: str | None = None) -> asyncpg.Connection:
    return await asyncpg.connect(db.dsn(role))


async def test_every_household_table_has_forced_rls(pg_db: PgDb):
    conn = await _connect(pg_db)
    try:
        assert await rls_violations(conn) == []
        names = {t["relname"] for t in await conn.fetch(_TABLES)}
        # the tables migration 002 creates or secures are all present and covered
        assert {"households", "elders", "inbox", "raw_text", "elder_identities", "requests", "quotes"} <= names
    finally:
        await conn.close()


async def test_planted_table_without_policy_fails(pg_db: PgDb):
    conn = await _connect(pg_db)
    try:
        await conn.execute("CREATE TABLE planted_open (id text PRIMARY KEY, household_id text NOT NULL)")
        assert await rls_violations(conn) == ["public.planted_open"]
        await conn.execute("ALTER TABLE planted_open ENABLE ROW LEVEL SECURITY")  # enabled, not forced, no policy
        assert await rls_violations(conn) == ["public.planted_open"]
        await conn.execute("SELECT control.apply_tenant_rls('planted_open')")
        assert await rls_violations(conn) == []
        await conn.execute("DROP POLICY tenant_del ON planted_open")  # one missing command is enough
        assert await rls_violations(conn) == ["public.planted_open"]
    finally:
        await conn.close()


_T = "current_setting('app.household_id', true)"
# name: (qualified table, policy to drop first or None, statement that plants the fault)
PLANTED_POLICIES = {
    "using_true": ("public.elders", "tenant_sel", "CREATE POLICY tenant_sel ON elders FOR SELECT USING (true)"),
    "wrong_column": (
        "public.elders", "tenant_sel", f"CREATE POLICY tenant_sel ON elders FOR SELECT USING (id = {_T})"),
    "extra_permissive": (
        "public.inbox", None, f"CREATE POLICY extra ON inbox FOR SELECT USING (household_id <> {_T})"),
    "role_scoped_true": (
        "public.requests", None, "CREATE POLICY sneaky ON requests FOR SELECT TO saathi_core USING (true)"),
    "update_on_consent_events": (
        "control.consent_events", None,
        f"CREATE POLICY upd ON control.consent_events FOR UPDATE USING (household_id = {_T})"),
    "tenant_policy_scoped_to_a_role": (
        "public.elders", "tenant_sel",
        f"CREATE POLICY tenant_sel ON elders FOR SELECT TO saathi_core USING (household_id = {_T})"),
    "restrictive_tenant_policy": (
        "public.elders", "tenant_sel",
        f"CREATE POLICY tenant_sel ON elders AS RESTRICTIVE FOR SELECT USING (household_id = {_T})"),
    "scan_policy_on_another_table": (
        "public.inbox", None, "CREATE POLICY scan_hh ON inbox FOR SELECT TO saathi_scan USING (true)"),
}


@pytest.mark.parametrize("name", sorted(PLANTED_POLICIES))
async def test_planted_policy_fails(pg_db: PgDb, name: str):
    table, drop, create = PLANTED_POLICIES[name]
    conn = await asyncpg.connect(pg_db.dsn("saathi_migrator"))
    try:
        assert await rls_violations(conn) == []
        if drop:
            await conn.execute(f"DROP POLICY {drop} ON {table}")
        await conn.execute(create)
        assert await rls_violations(conn) == [table]
    finally:
        await conn.close()


async def test_role_policies_are_the_allowlisted_set(pg_db: PgDb):
    conn = await _connect(pg_db)
    try:
        rows = await conn.fetch(
            "SELECT c.relname, p.polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid"
            " WHERE p.polroles <> '{0}'::oid[] ORDER BY 1, 2")
        assert {(r["relname"], r["polname"]) for r in rows} == ALLOWED_ROLE_POLICIES | {("rejected_inbound", "rej_dig")}
    finally:
        await conn.close()


async def test_key_id_beside_every_encrypted_column(pg_db: PgDb):
    conn = await _connect(pg_db)
    try:
        assert await key_id_violations(conn) == []
        # the check does see the columns it should: person_ref_enc and body_sealed exist with their key_id
        cols = {r["attname"] for r in await conn.fetch(
            "SELECT attname FROM pg_attribute WHERE attrelid IN ('elder_identities'::regclass, 'raw_text'::regclass)")}
        assert {"person_ref_enc", "body_sealed", "key_id"} <= cols
        await conn.execute("CREATE TABLE planted_enc (id text PRIMARY KEY, secret_sealed bytea)")
        assert await key_id_violations(conn) == ["public.planted_enc.secret_sealed"]
    finally:
        await conn.close()


async def test_tenantless_list_is_exact(pg_db: PgDb):
    conn = await _connect(pg_db)
    try:
        found = {
            t["relname"] for t in await conn.fetch(_TABLES) if not t["has_household"] and t["relname"] != "households"
        }
        assert found == TENANTLESS
    finally:
        await conn.close()


async def test_migrations_apply_in_order(pg_blank_db: PgDb, tmp_path: Path):
    dsn = pg_blank_db.dsn("saathi_migrator")
    for name, body in (
        ("010_b.sql", "INSERT INTO order_log VALUES (2);"),
        ("002_a.sql", "INSERT INTO order_log VALUES (1);"),
        ("001_a.sql", "CREATE TABLE order_log (n int);"),
        ("README.txt", "not a migration"),
    ):
        (tmp_path / name).write_text(body)
    assert await migrate(dsn, tmp_path) == ["001_a", "002_a", "010_b"]
    assert await migrate(dsn, tmp_path) == []  # idempotent
    conn = await asyncpg.connect(dsn)
    try:
        assert [r["n"] for r in await conn.fetch("SELECT n FROM order_log ORDER BY n")] == [1, 2]
        rows = await conn.fetch("SELECT version, sha256 FROM schema_migrations ORDER BY version")
        assert [r["version"] for r in rows] == ["001_a", "002_a", "010_b"]
        assert all(re.fullmatch(r"[0-9a-f]{64}", r["sha256"]) for r in rows)
    finally:
        await conn.close()


async def test_failed_migration_rolls_back_only_itself(pg_blank_db: PgDb, tmp_path: Path):
    dsn = pg_blank_db.dsn("saathi_migrator")
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE keep (n int);")
    (tmp_path / "002_bad.sql").write_text("CREATE TABLE half (n int); SELECT 1/0;")
    with pytest.raises(asyncpg.PostgresError):
        await migrate(dsn, tmp_path)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT to_regclass('keep')") is not None
        assert await conn.fetchval("SELECT to_regclass('half')") is None
        assert [r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")] == ["001_ok"]
    finally:
        await conn.close()


async def test_real_files_apply_to_an_empty_database(pg_blank_db: PgDb):
    applied = await migrate(pg_blank_db.dsn("saathi_migrator"), SCHEMA_DIR)
    assert applied == sorted(applied) and applied[:2] == ["001_init", "002_inbox"]


async def test_changed_applied_file_refused(pg_blank_db: PgDb, tmp_path: Path):
    dsn = pg_blank_db.dsn("saathi_migrator")
    first = tmp_path / "001_a.sql"
    first.write_text("CREATE TABLE t1 (n int);")
    await migrate(dsn, tmp_path)
    first.write_text("CREATE TABLE t1 (n int, extra int);")
    (tmp_path / "002_new.sql").write_text("CREATE TABLE t2 (n int);")
    with pytest.raises(MigrationError, match="001_a"):
        await migrate(dsn, tmp_path)
    conn = await asyncpg.connect(dsn)
    try:  # refused before anything new ran
        assert await conn.fetchval("SELECT to_regclass('t2')") is None
    finally:
        await conn.close()


async def _applied_one(db: PgDb, tmp_path: Path) -> str:
    dsn = db.dsn("saathi_migrator")
    (tmp_path / "001_a.sql").write_text("CREATE TABLE t1 (n int);")
    await migrate(dsn, tmp_path)
    return dsn


async def test_renamed_applied_file_refused(pg_blank_db: PgDb, tmp_path: Path):
    dsn = await _applied_one(pg_blank_db, tmp_path)
    (tmp_path / "001_a.sql").rename(tmp_path / "001_renamed.sql")
    with pytest.raises(MigrationError, match="001_a"):
        await migrate(dsn, tmp_path)
    conn = await asyncpg.connect(dsn)
    try:  # nothing was applied a second time
        assert [r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")] == ["001_a"]
    finally:
        await conn.close()


async def test_deleted_applied_file_refused(pg_blank_db: PgDb, tmp_path: Path):
    dsn = await _applied_one(pg_blank_db, tmp_path)
    (tmp_path / "001_a.sql").unlink()
    (tmp_path / "002_b.sql").write_text("CREATE TABLE t2 (n int);")
    with pytest.raises(MigrationError, match="001_a.*missing"):
        await migrate(dsn, tmp_path)
    conn = await asyncpg.connect(dsn)
    try:
        assert await conn.fetchval("SELECT to_regclass('t2')") is None
    finally:
        await conn.close()


@pytest.mark.parametrize("bad", ["01_short.sql", "0001_long.sql", "001-dash.sql", "001_a b.sql", "init.sql"])
async def test_misnamed_sql_file_refused(pg_blank_db: PgDb, tmp_path: Path, bad: str):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE t1 (n int);")
    (tmp_path / bad).write_text("CREATE TABLE t2 (n int);")
    with pytest.raises(MigrationError, match="not named NNN_name.sql"):
        await migrate(pg_blank_db.dsn("saathi_migrator"), tmp_path)
    conn = await asyncpg.connect(pg_blank_db.dsn("saathi_migrator"))
    try:
        assert await conn.fetchval("SELECT to_regclass('t1')") is None  # refused before anything ran
    finally:
        await conn.close()


async def test_superuser_dsn_refused(pg_blank_db: PgDb, tmp_path: Path):
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="superuser"):
        await migrate(pg_blank_db.dsn(), tmp_path)


_ROLE_DDL = re.compile(r"\b(CREATE|ALTER|DROP)\s+(ROLE|USER|GROUP)\b", re.I)


def role_ddl_in(sql: str) -> list[str]:
    stripped = re.sub(r"--[^\n]*", "", sql)
    return [m.group(0) for m in _ROLE_DDL.finditer(stripped)]


def test_no_migration_creates_a_role():
    files = sorted(SCHEMA_DIR.glob("*.sql"))
    assert files, "no migration files found"
    for f in files:
        assert role_ddl_in(f.read_text()) == [], f.name
    # the scanner does flag a planted role statement, and ignores a comment
    assert role_ddl_in("create role x;") and role_ddl_in("ALTER   USER y SET search_path = z;")
    assert role_ddl_in("-- CREATE ROLE only in a comment\nSELECT 1;") == []


async def test_migrating_does_not_change_the_roles(pg_blank_db: PgDb, pg_superuser_dsn: str):
    su = await asyncpg.connect(pg_superuser_dsn)
    try:
        before = await su.fetch("SELECT rolname FROM pg_roles ORDER BY 1")
        await migrate(pg_blank_db.dsn("saathi_migrator"), SCHEMA_DIR)
        assert await su.fetch("SELECT rolname FROM pg_roles ORDER BY 1") == before
    finally:
        await su.close()


def test_init_script_lists_no_schema_run_and_every_secret():
    text = INIT_SCRIPT.read_text()
    assert "001_init" not in text and "-f /docker-entrypoint-initdb.d/schema" not in text
    for r in ("MIGRATOR", "EDGE", "CORE", "MODEL", "OWNER", "READONLY"):
        assert "${SAATHI_DB_PASSWORD_" + r + "}" in text


async def test_init_script_creates_every_role(pg_db: PgDb):
    conn = await _connect(pg_db)
    try:
        fetched = await conn.fetch(
            "SELECT rolname, rolsuper, rolcreaterole, rolbypassrls, rolcanlogin FROM pg_roles"
            " WHERE rolname LIKE 'saathi\\_%'"
        )
        rows = {r["rolname"]: r for r in fetched}
        assert set(rows) == set(ALL_ROLES)
        assert [n for n, r in rows.items() if r["rolsuper"] or r["rolcreaterole"]] == []
        assert [n for n, r in rows.items() if r["rolbypassrls"]] == ["saathi_readonly"]
        assert {n for n, r in rows.items() if not r["rolcanlogin"]} == {"saathi_scan", "saathi_audit"}
        owner = await conn.fetchval(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
        )
        assert owner == "saathi_migrator"
        # no runtime role owns a table
        owners = {r["tableowner"] for r in await conn.fetch(
            "SELECT tableowner FROM pg_tables WHERE schemaname IN ('public', 'control')")}
        assert owners == {"saathi_migrator"}
    finally:
        await conn.close()


async def test_init_script_grants(pg_superuser_dsn: str):
    """The ACL and memberships the init script made on the `saathi` database itself, not on a clone."""
    conn = await asyncpg.connect(_dsn(pg_superuser_dsn, dbname=TEMPLATE_DB))
    try:
        # PUBLIC: an explicit ACL exists (NULL would mean the defaults) and gives PUBLIC nothing
        assert await conn.fetchval("SELECT datacl IS NOT NULL FROM pg_database WHERE datname = 'saathi'")
        public = await conn.fetch(
            "SELECT a.privilege_type FROM pg_database d, aclexplode(d.datacl) a"
            " WHERE d.datname = 'saathi' AND a.grantee = 0"
        )
        assert public == []
        runtime = ("saathi_edge", "saathi_core", "saathi_model", "saathi_owner", "saathi_readonly")
        for role in runtime:
            assert await conn.fetchval("SELECT has_database_privilege($1, 'saathi', 'CONNECT')", role), role
        for role in ("saathi_scan", "saathi_audit"):
            for priv in ("CONNECT", "TEMP"):
                assert not await conn.fetchval("SELECT has_database_privilege($1, 'saathi', $2)", role, priv), role
        members = await conn.fetch(
            """SELECT pg_get_userbyid(roleid) AS role, inherit_option, set_option, admin_option
                 FROM pg_auth_members WHERE pg_get_userbyid(member) = 'saathi_migrator'
                  AND pg_get_userbyid(roleid) IN ('saathi_scan', 'saathi_audit') ORDER BY 1"""
        )
        assert [(m["role"], m["inherit_option"], m["set_option"], m["admin_option"]) for m in members] == [
            ("saathi_audit", False, True, False),
            ("saathi_scan", False, True, False),
        ]
    finally:
        await conn.close()


async def test_cloned_database_has_the_templates_acl(pg_db: PgDb):
    conn = await _connect(pg_db)
    try:
        for role in ("saathi_edge", "saathi_core", "saathi_model", "saathi_owner", "saathi_readonly"):
            assert await conn.fetchval("SELECT has_database_privilege($1, current_database(), 'CONNECT')", role)
        for role in ("saathi_scan", "saathi_audit"):
            assert not await conn.fetchval("SELECT has_database_privilege($1, current_database(), 'CONNECT')", role)
        assert await conn.fetchval(
            "SELECT count(*) FROM pg_database d, aclexplode(d.datacl) a"
            " WHERE d.datname = current_database() AND a.grantee = 0"
        ) == 0
    finally:
        await conn.close()


async def test_guard_refuses_an_unmarked_or_foreign_cluster(pg_superuser_dsn: str):
    conn = await asyncpg.connect(pg_superuser_dsn)
    try:
        await assert_throwaway(conn, template="saathi_guard_absent")  # absent: allowed
        await conn.execute('CREATE DATABASE saathi_guard_probe')
        try:
            with pytest.raises(RuntimeError, match="not marked"):  # exists, no comment
                await assert_throwaway(conn, template="saathi_guard_probe")
            await conn.execute("COMMENT ON DATABASE saathi_guard_probe IS 'something else'")
            with pytest.raises(RuntimeError, match="not marked"):  # exists, wrong comment
                await assert_throwaway(conn, template="saathi_guard_probe")
            await conn.execute(f"COMMENT ON DATABASE saathi_guard_probe IS '{TEMPLATE_COMMENT}'")
            await assert_throwaway(conn, template="saathi_guard_probe")  # marked: allowed
            with pytest.raises(RuntimeError, match="not a throwaway"):  # a database of the real stack
                await assert_throwaway(conn, template="saathi_guard_probe", forbidden=("saathi_guard_probe",))
        finally:
            await conn.execute("DROP DATABASE saathi_guard_probe WITH (FORCE)")
        # the real template database carries the mark after the fixture built it
        assert await conn.fetchval(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1", TEMPLATE_DB
        ) == TEMPLATE_COMMENT
    finally:
        await conn.close()
