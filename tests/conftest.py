"""Shared Postgres fixtures (LLD tests part, section 1 rule 2).

SAATHI_TEST_DSN is a superuser DSN of a throwaway cluster. The session fixture runs the role section of
infra/sql/00-create-databases.sh with fixed test words for the secrets, builds one template database by
``migrate()`` as ``saathi_migrator`` and each test clones it with ``CREATE DATABASE ... TEMPLATE``.
Without the DSN a Postgres test skips with a stated reason; under CI=true a missing DSN fails the test.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg
import pytest

from saathi.store.migrate import migrate

ROOT = Path(__file__).resolve().parent.parent
INIT_SCRIPT = ROOT / "infra" / "sql" / "00-create-databases.sh"
SCHEMA_DIR = ROOT / "infra" / "sql" / "schema"

TEMPLATE_DB = "saathi"
TEMPLATE_COMMENT = "saathi-test-template"  # marks a `saathi` database this fixture built, so it may replace it
FORBIDDEN_DBS = ("langfuse",)  # a database of the real stack: its presence means the DSN is not a throwaway cluster
LOGIN_ROLES = ("migrator", "edge", "core", "model", "owner", "readonly")
ALL_ROLES = tuple(f"saathi_{r}" for r in (*LOGIN_ROLES, "scan", "audit"))
# Synthetic, fixed test words in place of the secrets (never real values).
TEST_PASSWORDS = {f"saathi_{r}": f"test-pass-{r}" for r in LOGIN_ROLES}


def role_section_sql() -> str:
    """The SQL between the BEGIN and END markers of the init script, with the secrets filled in."""
    text = INIT_SCRIPT.read_text()
    m = re.search(r"^-- BEGIN roles\n(.*?)^-- END roles$", text, re.S | re.M)
    assert m, "init script lost its BEGIN/END roles markers"
    sql = m.group(1)
    for r in LOGIN_ROLES:
        sql = sql.replace("${SAATHI_DB_PASSWORD_" + r.upper() + "}", TEST_PASSWORDS[f"saathi_{r}"])
    assert "${" not in sql, "an init script variable has no test word"
    return sql


def role_statements() -> list[str]:
    stripped = re.sub(r"--[^\n]*", "", role_section_sql())
    return [s.strip() for s in stripped.split(";") if s.strip()]


def _dsn(base: str, *, user: str | None = None, password: str | None = None, dbname: str | None = None) -> str:
    parts = urlsplit(base)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    u = quote(user) if user is not None else (parts.username or "")
    pw = quote(password) if password is not None else (parts.password or "")
    cred = u + (f":{pw}" if pw else "")
    netloc = (f"{cred}@" if cred else "") + host + port
    path = f"/{dbname}" if dbname is not None else parts.path
    return urlunsplit((parts.scheme, netloc, path, parts.query, ""))


def _need_dsn() -> str:
    dsn = os.environ.get("SAATHI_TEST_DSN")
    if dsn:
        return dsn
    if os.environ.get("CI") == "true":
        pytest.fail("SAATHI_TEST_DSN is not set under CI=true: the Postgres service is a prerequisite, never a skip")
    pytest.skip("SAATHI_TEST_DSN is not set (needs a superuser DSN of a throwaway Postgres 16)")


async def assert_throwaway(
    conn: asyncpg.Connection, template: str = TEMPLATE_DB, forbidden: tuple[str, ...] = FORBIDDEN_DBS
) -> None:
    """Refuse a cluster that holds real data: a `template` database not built by this fixture, or a forbidden one."""
    for name in forbidden:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            raise RuntimeError(f"refusing to run: database {name!r} exists, so this is not a throwaway cluster")
    row = await conn.fetchrow(
        "SELECT shobj_description(oid, 'pg_database') AS comment FROM pg_database WHERE datname = $1", template
    )
    if row is not None and row["comment"] != TEMPLATE_COMMENT:
        raise RuntimeError(
            f"refusing to run: database {template!r} exists and is not marked {TEMPLATE_COMMENT!r}; "
            "SAATHI_TEST_DSN must name a throwaway cluster"
        )


async def _drop_all(su: str) -> None:
    conn = await asyncpg.connect(su)
    try:
        for (name,) in await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname = $1 OR datname LIKE 'saathi\\_t\\_%'", TEMPLATE_DB
        ):
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for role in ALL_ROLES:
            await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
    finally:
        await conn.close()


async def _build_template(su: str) -> None:
    conn = await asyncpg.connect(su)
    try:
        await assert_throwaway(conn)
    finally:
        await conn.close()
    await _drop_all(su)
    conn = await asyncpg.connect(su)
    try:
        for stmt in role_statements():  # one by one: CREATE DATABASE cannot share a transaction
            await conn.execute(stmt)
        await conn.execute(f"COMMENT ON DATABASE {TEMPLATE_DB} IS '{TEMPLATE_COMMENT}'")
    finally:
        await conn.close()
    migrator = _dsn(su, user="saathi_migrator", password=TEST_PASSWORDS["saathi_migrator"], dbname=TEMPLATE_DB)
    await migrate(migrator, SCHEMA_DIR)


@pytest.fixture(scope="session")
def pg_superuser_dsn() -> Iterator[str]:
    su = _need_dsn()
    asyncio.run(_build_template(su))
    yield su
    asyncio.run(_drop_all(su))


@dataclass(frozen=True)
class PgDb:
    """One cloned database. ``dsn(role)`` connects as that role; ``dsn()`` as the superuser (seeding bypasses RLS)."""

    name: str
    superuser_dsn: str

    def dsn(self, role: str | None = None) -> str:
        if role is None:
            return _dsn(self.superuser_dsn, dbname=self.name)
        return _dsn(self.superuser_dsn, user=role, password=TEST_PASSWORDS[role], dbname=self.name)


_counter = itertools.count()


_ACL = """
SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE format('%I', pg_get_userbyid(a.grantee)) END AS grantee,
       a.privilege_type, a.is_grantable
  FROM pg_database d, aclexplode(d.datacl) a
 WHERE d.datname = $1 AND a.grantee <> d.datdba
"""


async def _clone(su: str, name: str, template: str) -> None:
    conn = await asyncpg.connect(su)
    try:
        await conn.execute(f'CREATE DATABASE "{name}" OWNER saathi_migrator TEMPLATE {template}')
        # A clone does not carry the template's ACL: replay the ACL of the database the init script made.
        acl = await conn.fetch(_ACL, TEMPLATE_DB)
        if acl:
            await conn.execute(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')
            for g in acl:
                grant = "WITH GRANT OPTION" if g["is_grantable"] else ""
                await conn.execute(f'GRANT {g["privilege_type"]} ON DATABASE "{name}" TO {g["grantee"]} {grant}')
    finally:
        await conn.close()


async def _drop(su: str, name: str) -> None:
    conn = await asyncpg.connect(su)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


@pytest.fixture
def pg_db(pg_superuser_dsn: str) -> Iterator[PgDb]:
    """A fresh clone of the fully migrated template, dropped after the test."""
    name = f"saathi_t_{os.getpid()}_{next(_counter)}"
    asyncio.run(_clone(pg_superuser_dsn, name, TEMPLATE_DB))
    yield PgDb(name, pg_superuser_dsn)
    asyncio.run(_drop(pg_superuser_dsn, name))


@pytest.fixture
def pg_blank_db(pg_superuser_dsn: str) -> Iterator[PgDb]:
    """An empty database owned by the migrator (no schema), for tests of the migrator itself."""
    name = f"saathi_t_{os.getpid()}_{next(_counter)}"
    asyncio.run(_clone(pg_superuser_dsn, name, "template0"))
    yield PgDb(name, pg_superuser_dsn)
    asyncio.run(_drop(pg_superuser_dsn, name))
