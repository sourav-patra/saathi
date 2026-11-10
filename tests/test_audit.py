"""LT-21: the audit sink (M17; RQ-56a, 95a; PL-07; TQ-39).

Seeding uses the superuser (it bypasses row-level security); the sink runs as real runtime roles.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

import asyncpg
import pytest
from conftest import PgDb
from fakes import FakeAudit
from pydantic import ValidationError
from store_helpers import open_store

from saathi.audit import AuditName, PgAuditSink
from saathi.domain import AuditEvent
from saathi.errors import AuditUnavailable, StoreUnavailable
from saathi.ids import HouseholdId, RunId
from saathi.ports import AuditSink
from saathi.store import Role
from saathi.store.keys import KeyRing, generate_secret

KEK = os.urandom(32)
H = HouseholdId("h1")
PHONE = "+91 0000 000000"
AUDIT_KEY = generate_secret()


async def seed(db: PgDb, *, audit_key: bool = True) -> None:
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute("INSERT INTO households (id, name) VALUES ('h1', 'Household h1')")
        if audit_key:
            wrapped = KeyRing(KEK).wrap(AUDIT_KEY, household=H, purpose="audit", key_id="a1")
            await su.execute(
                "INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
                " VALUES ('h1', 'audit', 'a1', $1)",
                wrapped,
            )
    finally:
        await su.close()


async def rows(db: PgDb) -> list[asyncpg.Record]:
    su = await asyncpg.connect(db.dsn())
    try:
        return await su.fetch("SELECT * FROM audit_log ORDER BY seq")
    finally:
        await su.close()


def ev(event: str = AuditName.DENY, **kw: object) -> AuditEvent:
    base: dict[str, object] = {"household": H, "run_id": RunId("run-1"), "actor": "system:worker", "event": event}
    return AuditEvent(**(base | kw))  # type: ignore[arg-type]


def test_the_fake_and_the_sink_satisfy_the_port():
    assert isinstance(FakeAudit(), AuditSink)
    assert isinstance(PgAuditSink.__new__(PgAuditSink), AuditSink)
    assert {n.value for n in AuditName} >= {"deny", "signoff_used", "signoff_expired", "key_rotated"}


async def test_core_update_delete_denied(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        ref = await PgAuditSink(store, KeyRing(KEK)).append(ev())
    assert int(ref.id) > 0
    core = await asyncpg.connect(pg_db.dsn("saathi_core"))
    try:
        await core.execute("SELECT set_config('app.household_id', 'h1', false)")
        for sql in ("UPDATE audit_log SET event = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await core.execute(sql)
    finally:
        await core.close()
    assert len(await rows(pg_db)) == 1


async def test_phone_number_in_no_row(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        sink = PgAuditSink(store, KeyRing(KEK))
        await sink.append(ev(AuditName.SHOW_RAW, subject=PHONE, sensitive={"phone": PHONE, "addr": "12 Example Lane"}))
        await sink.append(ev(AuditName.CALL_NO_ACK, detail={"stage": "c", "attempt": 2, "ok": False}))
    got = await rows(pg_db)
    dump = "\n".join(str(dict(r)) for r in got)
    assert PHONE not in dump and "Example Lane" not in dump and "0000 000000" not in dump
    first = got[0]
    assert first["subject"] == hmac.new(AUDIT_KEY, f"subject|{PHONE}".encode(), hashlib.sha256).hexdigest()
    assert first["key_id"] == "a1" and first["payload_hmac"]
    detail = json.loads(first["detail"])
    assert detail["phone_hmac"] == hmac.new(AUDIT_KEY, f"field.phone|{PHONE}".encode(), hashlib.sha256).hexdigest()
    assert got[1]["subject"] is None


async def test_actor_and_run_required(pg_db: PgDb):
    await seed(pg_db)
    for bad in ("robot", "", "agent:", "approver:a b"):
        with pytest.raises(ValidationError):
            ev(actor=bad)
    with pytest.raises(ValidationError):
        AuditEvent(household=H, event="deny", actor="elder")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AuditEvent(household=H, run_id=RunId("r"), event="deny")  # type: ignore[call-arg]
    async with open_store(pg_db, Role.CORE) as store:
        sink = PgAuditSink(store, KeyRing(KEK))
        with pytest.raises(ValueError):
            await sink.append(ev(run_id=RunId("  ")))
        for actor in ("elder", "approver:a-1", "system:worker", "agent:vision"):
            await sink.append(ev(actor=actor))
    assert [r["actor"] for r in await rows(pg_db)] == ["elder", "approver:a-1", "system:worker", "agent:vision"]
    assert {r["run_id"] for r in await rows(pg_db)} == {"run-1"}


async def test_denials_and_failures_kept(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        sink = PgAuditSink(store, KeyRing(KEK))
        await sink.append(ev(AuditName.DENY, detail={"reason": "cap_exceeded", "ok": False}))
        await sink.append(ev(AuditName.ALERT_BLOCKED, detail={"reason": "no_human_reached"}))
        # a denial written in its own transaction survives the rollback of the work it refused
        with pytest.raises(RuntimeError):
            async with store.tx(household=H) as work:
                await work.execute("SELECT 1")
                await sink.append(ev(AuditName.CONSENT_DENIED))
                raise RuntimeError("the refused work fails")
        # written in the caller's own transaction, it goes with that transaction
        with pytest.raises(RuntimeError):
            async with store.tx(household=H) as work:
                await sink.append(ev(AuditName.CLIP_ERROR), tx=work)
                raise RuntimeError("rolled back")
        async with store.tx(household=H) as work:
            await sink.append(ev(AuditName.SIGNOFF_USED, detail={"signoff": 7}), tx=work)
    assert [r["event"] for r in await rows(pg_db)] == ["deny", "alert_blocked", "consent_denied", "signoff_used"]


async def test_tx_of_another_household_is_refused(pg_db: PgDb):
    await seed(pg_db)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await su.execute("INSERT INTO households (id, name) VALUES ('h2', 'Household h2')")
    finally:
        await su.close()
    async with open_store(pg_db, Role.CORE) as store:
        async with store.tx(household=HouseholdId("h2")) as other:
            with pytest.raises(ValueError):
                await PgAuditSink(store, KeyRing(KEK)).append(ev(), tx=other)
    assert await rows(pg_db) == []


async def test_unavailable_fails_closed(pg_db: PgDb):
    await seed(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        # no KEK: the keyed hash cannot be made, so nothing is written
        with pytest.raises(AuditUnavailable):
            await PgAuditSink(store, KeyRing(None)).append(ev())
        # a different KEK cannot unwrap the audit key
        with pytest.raises(AuditUnavailable):
            await PgAuditSink(store, KeyRing(os.urandom(32))).append(ev())
    # the model role may not write the audit trail at all
    async with open_store(pg_db, Role.MODEL) as store:
        with pytest.raises(AuditUnavailable):
            await PgAuditSink(store, KeyRing(KEK)).append(ev())
    # the store is gone
    async with open_store(pg_db, Role.CORE) as store:
        await store.close()
        with pytest.raises(AuditUnavailable):
            await PgAuditSink(store, KeyRing(KEK)).append(ev())
    assert await rows(pg_db) == []


async def test_household_without_audit_key_fails_closed(pg_db: PgDb):
    await seed(pg_db, audit_key=False)
    async with open_store(pg_db, Role.CORE) as store:
        with pytest.raises(AuditUnavailable) as err:
            await PgAuditSink(store, KeyRing(KEK)).append(ev(sensitive={"phone": PHONE}))
    assert PHONE not in str(err.value) and err.value.__cause__ is None
    assert await rows(pg_db) == []


async def test_store_error_names_a_code_only():
    class Down:
        def tx(self, **_: object):
            raise StoreUnavailable("PostgresConnectionError")

    with pytest.raises(AuditUnavailable) as err:
        await PgAuditSink(Down(), KeyRing(KEK)).append(ev())  # type: ignore[arg-type]
    assert str(err.value) == "StoreUnavailable"


def test_detail_rejects_long_string():
    with pytest.raises(ValidationError):
        ev(detail={"note": "x" * 65})
    with pytest.raises(ValidationError):
        ev(detail={"note": "free text with spaces"})
    with pytest.raises(ValidationError):
        ev(detail={"note": PHONE})
    with pytest.raises(ValidationError):
        ev(detail={"note": 1.5})
    assert ev(detail={"note": "x" * 64, "n": 3, "ok": True}).detail["n"] == 3


async def test_caller_cannot_supply_or_overwrite_a_fingerprint():
    """WU-10 review F1: a sensitive key may not collide with a detail key ending in _hmac."""
    from saathi.audit.sink import PgAuditSink

    sink = PgAuditSink(store=object(), keys=object())  # type: ignore[arg-type]
    e = ev(sensitive={"phone": "x"}, detail={"phone_hmac": "forged"})
    with pytest.raises(ValueError, match="fingerprint"):
        await sink.append(e)
