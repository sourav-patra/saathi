"""The store: one transaction per household, on one pooled connection (M04; RQ-109, RQ-38).

`Store.tx` opens a transaction and runs ``set_config('app.household_id', $1, true)`` first. The setting is local to the
transaction, so it is gone at commit or rollback and never reaches the next user of a pooled connection. An empty
household raises `TenantNotSet` before any statement. asyncpg is used here, in `scan.py`, in `migrate.py` and
nowhere else (repos see only `Tx`).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import asyncpg

from saathi.errors import StoreUnavailable, TenantNotSet
from saathi.ids import HouseholdId
from saathi.ports import Clock

if TYPE_CHECKING:
    from saathi.store.scan import ScanFns

# Connection-level failures only: a body that raises anything else keeps its own error.
CONN_ERRORS = (asyncpg.PostgresConnectionError, asyncpg.InterfaceError, asyncpg.CannotConnectNowError, ConnectionError)


class Role(StrEnum):
    EDGE = "edge"
    CORE = "core"
    MODEL = "model"
    OWNER = "owner"
    MIGRATOR = "migrator"


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """A pool whose connections decode jsonb to Python values. Connect as the runtime role, never a superuser."""
    try:
        return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init_connection)
    except CONN_ERRORS as exc:
        raise StoreUnavailable(type(exc).__name__) from exc


class PgTx:
    """Implements `ports.Tx`: one transaction on one connection, bound to one household."""

    def __init__(self, conn: asyncpg.Connection, household: HouseholdId, now: datetime) -> None:
        self.conn = conn
        self.household = household
        self.now = now

    async def execute(self, sql: str, *args: Any) -> str:
        try:
            return await self.conn.execute(sql, *args)
        except CONN_ERRORS as exc:
            raise StoreUnavailable(type(exc).__name__) from exc

    async def fetch(self, sql: str, *args: Any) -> list[Mapping[str, Any]]:
        try:
            return list(await self.conn.fetch(sql, *args))
        except CONN_ERRORS as exc:
            raise StoreUnavailable(type(exc).__name__) from exc


class Store:
    def __init__(self, pool: asyncpg.Pool, role: Role, clock: Clock) -> None:
        self._pool = pool
        self._role = role
        self._clock = clock

    @property
    def role(self) -> Role:
        return self._role

    @asynccontextmanager
    async def tx(self, *, household: HouseholdId, lock_household: bool = False) -> AsyncIterator[PgTx]:
        if not household or not household.strip():
            raise TenantNotSet()
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute("SELECT set_config('app.household_id', $1, true)", household)
                if lock_household:
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 1))", household)
                yield PgTx(conn, household, self._clock.now())
        except CONN_ERRORS as exc:
            raise StoreUnavailable(type(exc).__name__) from exc

    @property
    def scan(self) -> ScanFns:
        """The security-definer functions this role may call; `PermissionError` for a role with none."""
        from saathi.store.scan import SCAN_GRANTS, ScanFns

        if self._role not in SCAN_GRANTS:
            raise PermissionError(f"role {self._role.value} has no scan grants")
        return ScanFns(self._pool, self._role)

    async def close(self) -> None:
        await self._pool.close()

