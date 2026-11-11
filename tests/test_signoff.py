"""LT-20: the standing sign-off SO-1 and its use by the alert service (M14, M24; RQ-95a; TQ-10, 41).

A sign-off is a row the agent roles cannot write. An expired or revoked row behaves as off. Each use writes an audit row
that names the sign-off id, and the owner hears 14 days before it ends. Real Postgres; synthetic values only.
"""

from __future__ import annotations

import json
from datetime import timedelta

import asyncpg
import pytest
from alert_helpers import AlertRig, B, H, P, make_rig, open_store
from conftest import PgDb
from fakes import FakeEscalator
from store_helpers import NOW

from saathi.audit import PgAuditSink
from saathi.ids import ApproverId, HouseholdId
from saathi.store import Role
from saathi.store.keys import generate_secret
from saathi.store.repos import SignoffRepo

HH = HouseholdId(H)


async def grant(db: PgDb, *, expires_in: timedelta, revoked: bool = False) -> int:
    su = await asyncpg.connect(db.dsn())
    try:
        sid = int(
            await su.fetchval(
                "INSERT INTO control.signoffs (household_id, scope, granter, granted_at, expires_at, revoked_at)"
                " VALUES ($1, 'SO-1', 'owner', $2, $3, $4) RETURNING id",
                H, NOW - timedelta(days=1), NOW + expires_in, NOW if revoked else None,
            )  # fmt: skip
        )
    finally:
        await su.close()
    return sid


@pytest.fixture
async def bare(pg_db: PgDb) -> AlertRig:
    """The household with no sign-off row yet."""
    return await make_rig(pg_db, so1=False)


@pytest.mark.parametrize("role", ["core", "edge", "model"])
async def test_agent_role_write_denied(pg_db: PgDb, role: str):
    await make_rig(pg_db, so1=True)
    conn = await asyncpg.connect(pg_db.dsn(f"saathi_{role}"))
    try:
        for sql in (
            "INSERT INTO control.signoffs (household_id, scope, granter, expires_at)"
            " VALUES ('h1', 'SO-1', 'agent', now() + interval '90 days')",
            "UPDATE control.signoffs SET expires_at = now() + interval '900 days'",
            "UPDATE control.signoffs SET revoked_at = NULL",
            "DELETE FROM control.signoffs",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
    finally:
        await conn.close()


async def test_the_alert_service_never_writes_a_signoff():
    """The service takes the repo to read with (`valid`); nothing in the package calls the owner-only writers."""
    import inspect

    import saathi.alerts.service as service_mod

    text = inspect.getsource(service_mod)
    assert "signoffs.grant" not in text and "signoffs.revoke" not in text and "SignoffRepo.grant" not in text


async def test_expiry_behaves_as_off(bare: AlertRig):
    sid = await grant(bare.db, expires_in=timedelta(days=90))
    async with open_store(bare.db, Role.CORE, clock=bare.clock) as store:
        async with store.tx(household=HH) as tx:
            on = await SignoffRepo.valid(tx, "SO-1", NOW)
            ends = await SignoffRepo.valid(tx, "SO-1", NOW + timedelta(days=90))  # the moment it ends
            after = await SignoffRepo.valid(tx, "SO-1", NOW + timedelta(days=91))
        assert (on.valid, on.id) == (True, sid)
        assert not ends.valid and not after.valid  # off at expiry, and after it
        assert after.id == sid and not after.revoked

        # the service agrees: a clock past expiry places no stage b or c attempt
        await bare.event()
        svc = bare.service(store)
        await svc.tick()
        await bare.clock.advance(90 * 86400 + 300)  # an event that began after the sign-off ended
    async with open_store(bare.db, Role.CORE, clock=bare.clock) as store:
        late = await bare.event("late")
        svc = bare.service(store)
        await svc.tick()
        await bare.run(svc, 400)
        assert bare.stage_b.notified == [] and bare.stage_c.notified == []
        assert (await bare.state(late))["state"] == "open"


async def test_revoked_behaves_as_off_and_no_row_is_off(bare: AlertRig):
    async with open_store(bare.db, Role.CORE, clock=bare.clock) as store:
        async with store.tx(household=HH) as tx:
            none = await SignoffRepo.valid(tx, "SO-1", NOW)
        assert not none.valid and none.id is None
    await grant(bare.db, expires_in=timedelta(days=90), revoked=True)
    async with open_store(bare.db, Role.CORE, clock=bare.clock) as store, store.tx(household=HH) as tx:
        gone = await SignoffRepo.valid(tx, "SO-1", NOW)
    assert not gone.valid and gone.revoked


async def test_14_day_pre_expiry_alert(bare: AlertRig):
    sid = await grant(bare.db, expires_in=timedelta(days=90))
    async with open_store(bare.db, Role.CORE, clock=bare.clock) as store:
        svc = bare.service(store)
        assert await svc.signoff_check(HH) == "ok"  # 90 days left
        await bare.clock.advance(75 * 86400)  # 15 days left
        assert await svc.signoff_check(HH) == "ok"
        assert bare.owner_alerts == []
        await bare.clock.advance(86400)  # 14 days left: the notice
        assert await svc.signoff_check(HH) == "expiring"
        assert bare.owner_alerts == ["signoff_expiring"]
        await bare.clock.advance(86400)
        assert await svc.signoff_check(HH) == "expiring"
        assert bare.owner_alerts == ["signoff_expiring"]  # once per sign-off, not once a day
        await bare.clock.advance(14 * 86400)  # it has ended: the owner is told at once
        assert await svc.signoff_check(HH) == "expired"
        assert bare.owner_alerts == ["signoff_expiring", "signoff_expired"]
        assert await svc.signoff_check(HH) == "expired"
        assert bare.owner_alerts == ["signoff_expiring", "signoff_expired"]
    assert sid  # the row the notices were about


async def test_no_signoff_row_alerts_the_owner_at_once(bare: AlertRig):
    async with open_store(bare.db, Role.CORE, clock=bare.clock) as store:
        assert await bare.service(store).signoff_check(HH) == "expired"
        assert bare.owner_alerts == ["signoff_expired"]


async def test_each_use_writes_audit(pg_db: PgDb):
    """Each stage b or c send under SO-1 writes one audit row that names the sign-off id, in the real audit log."""
    rig = await make_rig(pg_db, so1=False)
    sid = await grant(pg_db, expires_in=timedelta(days=90))
    su = await asyncpg.connect(pg_db.dsn())
    try:
        wrapped = rig.ring.wrap(generate_secret(), household=HH, purpose="audit", key_id="a1")
        await su.execute(
            "INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
            " VALUES ($1, 'audit', 'a1', $2)", H, wrapped,
        )  # fmt: skip
    finally:
        await su.close()
    await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store, audit=PgAuditSink(store, rig.ring))
        await svc.tick()
        await rig.run(svc, 250, every=10)
        used = await rig.sql("SELECT actor, event, subject, detail FROM audit_log WHERE event = 'signoff_used'")
        placed = len(rig.stage_b.notified) + len(rig.stage_c.notified)
        assert placed == 5  # two templates at 120 s, two at 240 s, the primary's call at 240 s
        assert len(used) == placed  # one row per use
        assert {r["actor"] for r in used} == {"system:alerts"}
        assert {json.loads(r["detail"])["signoff_id"] for r in used} == {sid}  # the row names the sign-off
        details = [json.loads(r["detail"]) for r in used]
        assert sorted((d["stage"], d["n"]) for d in details).count(("b", 1)) == 2
        assert all(r["subject"] and r["subject"] != str(sid) for r in used)  # the subject is a keyed hash
        assert await rig.sql("SELECT 1 FROM audit_log WHERE event = 'signoff_expired'") == []


async def test_the_acknowledgement_row_names_the_approver_who_was_called(pg_db: PgDb):
    rig = await make_rig(pg_db)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        wrapped = rig.ring.wrap(generate_secret(), household=HH, purpose="audit", key_id="a1")
        await su.execute(
            "INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
            " VALUES ($1, 'audit', 'a1', $2)", H, wrapped,
        )  # fmt: skip
    finally:
        await su.close()
    event = await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store, audit=PgAuditSink(store, rig.ring))
        await svc.tick()
        await rig.run(svc, 250, every=10)
        call = rig.stage_c.call_id_of(rig.stage_c.keys[0])
        assert await svc.on_voice_callback(HH, event, FakeEscalator.callback(call)) is True
        [row] = await rig.sql("SELECT actor, event FROM audit_log WHERE event = 'ack_voice'")
        assert (row["actor"], row["event"]) == (f"approver:{P}", "ack_voice")
        assert await svc.acknowledge(HH, event, ApproverId(B), "tap") is False  # later: logged, nothing changes
        late = await rig.sql("SELECT detail FROM audit_log WHERE event = 'ack_tap'")
        assert [json.loads(r["detail"]).get("late") for r in late] == [True]
