"""WU-17 review fixes F1 to F7: a failed send reaches the owner, the follow-up at 15 minutes, owner notices that
cannot be lost. Fakes only; synthetic values."""

from __future__ import annotations

import inspect
import logging
from typing import Any

import asyncpg
import pytest
from alert_helpers import AlertRig, B, H, P, make_rig, open_store
from conftest import PgDb

from saathi.alerts import AlertService, TelegramStageA, UnavailableEscalator
from saathi.alerts.plan import FOLLOWUP_S
from saathi.domain import Source
from saathi.errors import AuditUnavailable, ChannelDown
from saathi.ids import ApproverId, HouseholdId
from saathi.store import Role
from saathi.store.repos import SignoffRepo

HH = HouseholdId(H)


@pytest.fixture
async def rig(pg_db: PgDb) -> AlertRig:
    return await make_rig(pg_db)


def service(rig: AlertRig, store: Any, *, stages: Any = None, owner_alert: Any = None, audit: Any = None):
    return AlertService(
        store, rig.caregiver, {Source.WHATSAPP: rig.elder}, rig.voice,
        stages or {"a": TelegramStageA(rig.caregiver), "b": rig.stage_b, "c": rig.stage_c},
        audit or rig.audit, SignoffRepo, rig.clock, rig.cfg, owner_alert=owner_alert or rig.owner_alert,
    )  # fmt: skip


def unavailable(rig: AlertRig) -> dict[str, Any]:
    return {"a": TelegramStageA(rig.caregiver), "b": UnavailableEscalator("b"), "c": UnavailableEscalator("c")}


def texts(rig: AlertRig, to: str) -> list[tuple[str, str]]:
    return [(p, str(k)) for m, t, p, k in rig.caregiver.sent if m == "send_text" and t == to]


# --- F2 ----------------------------------------------------------------------------------------------------------


def test_owner_alert_is_a_required_keyword():
    p = inspect.signature(AlertService.__init__).parameters["owner_alert"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty


# --- F1 ----------------------------------------------------------------------------------------------------------


async def test_unavailable_b_and_c_tell_the_owner_once_each(rig: AlertRig):
    await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store, stages=unavailable(rig))
        await svc.tick()
        await rig.run(svc, 130, every=10)
        assert rig.owner_alerts == ["alert_send_failed:b"]  # told at the first failed b send, long before 4740 s
        await rig.run(svc, 200, every=10)
        assert sorted(rig.owner_alerts) == ["alert_send_failed:b", "alert_send_failed:c"]
        await rig.run(svc, 1200, every=20)
        assert sorted(rig.owner_alerts) == ["alert_send_failed:b", "alert_send_failed:c"]  # once per stage
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store2:  # a restart finds the marker
        await rig.run(service(rig, store2, stages=unavailable(rig)), 600, every=20)
        assert sorted(rig.owner_alerts) == ["alert_send_failed:b", "alert_send_failed:c"]


async def test_telegram_down_tells_the_owner_at_the_first_failed_send(rig: AlertRig):
    rig.caregiver.fail("send_card", ChannelDown, times=1000)
    await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store, stages=unavailable(rig))
        await svc.tick()
        assert rig.owner_alerts == ["alert_send_failed:a"]
        await rig.run(svc, 400, every=10)
        assert rig.owner_alerts.count("alert_send_failed:a") == 1
        assert {"alert_send_failed:b", "alert_send_failed:c"} <= set(rig.owner_alerts)


async def test_a_failed_owner_alert_is_tried_again_at_the_next_failed_send(rig: AlertRig):
    told: list[str] = []
    calls = 0

    async def flaky(what: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ChannelDown()
        told.append(what)

    rig.caregiver.fail("send_card", ChannelDown, times=1000)
    await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store, owner_alert=flaky)
        await svc.tick()
        # the first notice failed and wrote no marker; the second approver's failed send tried again and got through
        assert calls == 2 and told == ["alert_send_failed:a"]
        await rig.run(svc, 400, every=10)
        assert calls == 2 and told == ["alert_send_failed:a"]  # and then once, not at every failed send


# --- F3 ----------------------------------------------------------------------------------------------------------


async def test_followup_goes_once_to_the_approver_who_acknowledged_at_15_minutes(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store)
        await svc.tick()
        await rig.clock.advance(30)
        assert await svc.acknowledge(HH, event, ApproverId(B), "tap")
        before = len(texts(rig, B))
        await rig.run(svc, FOLLOWUP_S - 60, every=30)
        assert texts(rig, B) == [] and texts(rig, P) == [] and before == 0  # not before 15 minutes
        await rig.run(svc, 90, every=30)
        [(body, key)] = texts(rig, B)
        assert key == f"alert:{event}:followup" and body and texts(rig, P) == []
        await rig.run(svc, 1800, every=60)
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store2:
        await rig.run(service(rig, store2), 600, every=60)  # nor after a restart
    assert len(texts(rig, B)) == 1


async def test_a_false_press_is_recovered_by_the_followup(rig: AlertRig):
    """T-33: a bystander or a pocket presses Acknowledge. Nobody acts, the escalation has stopped, and the follow-up
    asks the approver at 15 minutes whether the elder is safe; it says to call them if not."""
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store)
        await svc.tick()
        assert await svc.acknowledge(HH, event, ApproverId(P), "tap")
        sends = len(rig.cards())
        await rig.run(svc, 800, every=40)
        assert len(rig.cards()) == sends and rig.stage_b.notified == []  # the escalation stayed stopped
        await rig.run(svc, 200, every=40)
        [(body, _key)] = texts(rig, P)
        assert "call" in body.lower() and "resolved" in body.lower()
        state = await rig.state(event)
        assert state["state"] == "acknowledged"


async def test_a_failed_followup_is_tried_again_and_the_owner_hears_once(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store)
        await svc.tick()
        assert await svc.acknowledge(HH, event, ApproverId(P), "tap")
        rig.caregiver.fail("send_text", ChannelDown, times=2)
        await rig.run(svc, FOLLOWUP_S + 10, every=10)
        assert texts(rig, P) == [] and rig.owner_alerts == ["alert_send_failed:followup"]
        await rig.run(svc, 200, every=10)
        assert len(texts(rig, P)) == 1 and rig.owner_alerts == ["alert_send_failed:followup"]


async def test_the_followup_is_only_for_acknowledged_events(rig: AlertRig):
    event = await rig.event("m2", "scam")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store)
        await rig.run(svc, 2000, every=60)
        assert (await rig.state(event))["state"] == "open"
        assert texts(rig, P) == [] and texts(rig, B) == []


# --- F4, F5 ------------------------------------------------------------------------------------------------------


async def test_the_owner_hears_of_a_missing_signoff_even_when_the_audit_is_down(pg_db: PgDb):
    rig = await make_rig(pg_db, so1=False)
    rig.audit.fail("append", AuditUnavailable, times=1000)
    await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store)
        await svc.tick()
        await rig.run(svc, 200, every=10)
        assert rig.owner_alerts == ["signoff_expired"]  # told once, with the audit still down
        assert [e for e in rig.audit.events if e.event == "signoff_expired"] == []
        rig.audit.clear_failures()
        await rig.run(svc, 60, every=10)
        assert [e.event for e in rig.audit.events if e.event == "signoff_expired"] == ["signoff_expired"]
        await rig.run(svc, 300, every=10)
        assert rig.owner_alerts == ["signoff_expired"]
        assert len([e for e in rig.audit.events if e.event == "signoff_expired"]) == 1


async def test_a_failing_owner_alert_is_logged_at_error_and_the_signoff_notice_retried(
    pg_db: PgDb, caplog: pytest.LogCaptureFixture
):
    rig = await make_rig(pg_db, so1=False)
    told: list[str] = []
    fails = [True]

    async def owner(what: str) -> None:
        if fails:
            fails.pop()
            raise ChannelDown()
        told.append(what)

    await rig.event()
    caplog.set_level(logging.ERROR)
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store, owner_alert=owner)
        await svc.tick()
        await rig.run(svc, 400, every=10)
    assert told == ["signoff_expired"]  # the notice that failed was sent on a later tick, once
    assert any("signoff_expired" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


async def test_the_blocked_notice_is_retried(rig: AlertRig, caplog: pytest.LogCaptureFixture):
    told: list[str] = []
    fails = [True]

    async def owner(what: str) -> None:
        if fails:
            fails.pop()
            raise ChannelDown()
        told.append(what)

    caplog.set_level(logging.ERROR)
    await rig.event("m3", "scam")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store, owner_alert=owner)
        await svc.tick()
        await rig.run(svc, 6200, every=100)
    assert told == ["no_human_reached"]
    assert any("no_human_reached" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


async def test_a_sign_off_notice_that_fails_is_logged_and_not_raised(pg_db: PgDb, caplog: pytest.LogCaptureFixture):
    rig = await make_rig(pg_db, so1=False)

    async def broken(_what: str) -> None:
        raise ChannelDown()

    caplog.set_level(logging.ERROR)
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store, owner_alert=broken)
        assert await svc.signoff_check(HH) == "expired"
        assert await svc.signoff_check(HH) == "expired"  # the notice that was not told is tried again
    assert [r.levelno for r in caplog.records if "signoff_expired" in r.getMessage()] == [logging.ERROR] * 2


# --- F6 ----------------------------------------------------------------------------------------------------------


async def test_no_household_config_tells_the_owner(rig: AlertRig):
    await rig.event()
    su = await asyncpg.connect(rig.db.dsn())
    try:
        await su.execute("UPDATE control.household_config SET values = '[]'::jsonb WHERE household_id = $1", H)
    finally:
        await su.close()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store)
        await svc.tick()
        await rig.run(svc, 100, every=10)
    assert rig.owner_alerts == ["alert_no_config"]  # once, however many ticks failed


# --- F7 ----------------------------------------------------------------------------------------------------------


async def test_a_repeated_false_alarm_tap_changes_nothing(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = service(rig, store)
        await svc.tick()
        await rig.run(svc, 130, every=10)
        assert await svc.acknowledge(HH, event, ApproverId(P), "tap")
        assert await svc.false_alarm(HH, event, ApproverId(B)) is True
        rows = len([e for e in rig.audit.events if e.event == "false_alarm"])
        assert await svc.false_alarm(HH, event, ApproverId(B)) is False
        assert len([e for e in rig.audit.events if e.event == "false_alarm"]) == rows
