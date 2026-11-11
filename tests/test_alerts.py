"""LT-19: the alert service against Postgres, with a fake clock and fake platforms (M14; RQ-18, 16; TQ-09, 10, 11).

Every case drives `AlertService.tick` while the fake clock moves. Stage a is the real Telegram escalator over the fake
caregiver channel; stages b and c are fakes. Synthetic values only.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta

import asyncpg
import pytest
from alert_helpers import AUDIO_HANDLE, AlertRig, B, H, P, blob, make_rig, open_store
from conftest import PgDb
from fakes import FakeASR, FakeClassifier, FakeEscalator, FakeGateway
from markers import rule
from store_helpers import NOW
from worker_helpers import Rig as WorkerRig
from worker_helpers import add_inbox, grant

from saathi import alerts as alerts_pkg
from saathi.alerts import AlertConfig
from saathi.alerts.service import attempt_key
from saathi.domain import ApproverUpdate, AttemptResult, ChannelHealth
from saathi.errors import AuditUnavailable, ChannelDown, SendRejected
from saathi.ids import ApproverId, HouseholdId, idem_key
from saathi.store import Role
from saathi.store.repos import UpdateRepo
from saathi.voice.store import ReplyKey
from saathi.worker import Worker, WorkerConfig, unscanned_sweep

HH = HouseholdId(H)


@pytest.fixture
async def rig(pg_db: PgDb) -> AlertRig:
    return await make_rig(pg_db)


def stage_sends(rig: AlertRig) -> dict[str, int]:
    return {"a": len(rig.cards()), "b": len(rig.stage_b.notified), "c": len(rig.stage_c.notified)}


# --- stop on acknowledgement, at each stage -------------------------------------------------------


@pytest.mark.parametrize(("stage", "at_s"), [("a", 70), ("b", 130), ("c", 250)])
async def test_stop_on_tap_each_stage(rig: AlertRig, stage: str, at_s: int):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, at_s)
        assert (await rig.state(event))["stage"] == stage  # the stage the event had reached
        before = stage_sends(rig)
        assert await svc.acknowledge(HH, event, ApproverId(B), "tap") is True
        row = await rig.state(event)
        assert (row["state"], row["acked_by"]) == ("acknowledged", B)
        await rig.run(svc, 6000, every=60)  # nothing more goes out at any later time
        assert stage_sends(rig) == before
        statuses = {a["status"] for a in await rig.attempts(event)}
        assert "due" not in statuses  # every unsent attempt was cancelled
        assert [e.event for e in rig.audit.events if e.event == "ack_tap"] == ["ack_tap"]


@pytest.mark.parametrize("who", ["not-an-approver", "ap9h1"])
async def test_a_tap_by_anyone_else_stops_nothing(rig: AlertRig, who: str):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        assert await svc.acknowledge(HH, event, ApproverId(who), "tap") is False
        assert (await rig.state(event))["state"] == "open"
        deny = [c for c in rig.audit.calls_of("append") if c[0][0].event == "deny"]
        assert len(deny) == 1 and deny[0][1]["tx"] is None  # a denial is kept outside any transaction


async def test_the_key_press_cannot_stop_an_event_before_stage_c(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        assert await svc.acknowledge(HH, event, ApproverId(P), "dtmf") is False  # A12 needs stage c
        assert (await rig.state(event))["state"] == "open"


# --- stage c: calls and key presses ---------------------------------------------------------------


async def test_answered_call_without_key_does_not_stop(rig: AlertRig):
    rig.stage_c.will_return(AttemptResult.ANSWERED_NO_ACK, AttemptResult.VOICEMAIL)
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 320)
        assert (await rig.state(event))["state"] == "open"  # an answered call, a voicemail: not an acknowledgement
        calls = await rig.attempts(event, "c")
        assert [c["status"] for c in calls[:2]] == ["answered_no_ack", "voicemail"]
        await rig.run(svc, 300)
        assert len(rig.stage_c.notified) > 2  # the cadence went on
        no_ack = [c for c in rig.audit.calls_of("append") if c[0][0].event == "call_no_ack"]
        assert len(no_ack) == 2 and all(c[1]["tx"] is None for c in no_ack)


async def test_key_press_1_acks_and_names_approver(pg_db: PgDb):
    rig = await make_rig(pg_db)
    event = await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 250)
        assert rig.stage_c.keys, "the primary's call was placed"
        key = rig.stage_c.keys[0]
        assert key == attempt_key(event, "c", P, 1)
        cb = FakeEscalator.callback(rig.stage_c.call_id_of(key))
        assert await svc.on_voice_callback(HH, event, cb) is True
        row = await rig.state(event)
        assert (row["state"], row["acked_by"]) == ("acknowledged", P)  # the approver the call was made to
        voice = [e for e in rig.audit.events if e.event == "ack_voice"]
        assert [(e.actor, e.subject) for e in voice] == [(f"approver:{P}", event)]
        before = stage_sends(rig)
        await rig.run(svc, 2000, every=100)
        assert stage_sends(rig) == before


async def test_forged_or_replayed_callback_changes_nothing(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 250)
        call = rig.stage_c.call_id_of(rig.stage_c.keys[0])
        for bad in (
            FakeEscalator.callback(call, signature="forged"),  # a forged signature
            FakeEscalator.callback("call-unknown"),  # a call id that no attempt of this event holds
            FakeEscalator.callback(call, digit="2"),  # another key
        ):
            assert await svc.on_voice_callback(HH, event, bad) is False
            assert (await rig.state(event))["state"] == "open"
        deny = [e for e in rig.audit.events if e.event == "deny"]
        assert len(deny) == 3
        assert await svc.on_voice_callback(HH, event, FakeEscalator.callback(call)) is True  # the real press
        acked = await rig.state(event)
        assert await svc.on_voice_callback(HH, event, FakeEscalator.callback(call)) is False  # a replay
        assert (await rig.state(event))["acked_by"] == acked["acked_by"]
        assert len([e for e in rig.audit.events if e.event == "ack_voice"]) == 1


async def test_replayed_update_id_ignored(rig: AlertRig):
    """The callback router (M15) asks `first_seen` before it acts; a replayed update id acts on nothing."""
    event = await rig.event()
    caregiver_update = ApproverUpdate(update_id=77, approver_id=ApproverId(P), kind="callback", data=f"ack:{event}")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        handled = 0
        for _ in range(3):  # the same update delivered three times
            async with store.tx(household=HH) as tx:
                fresh = await UpdateRepo.first_seen(tx, "caregiver", caregiver_update.update_id, "callback")
            if fresh:
                handled += await svc.acknowledge(HH, event, caregiver_update.approver_id, "tap")
        assert handled == 1
        acked = await rig.state(event)
        # a later tap of another approver is logged and changes nothing
        assert await svc.acknowledge(HH, event, ApproverId(B), "tap") is False
        assert (await rig.state(event))["acked_by"] == acked["acked_by"] == P
        late = [e for e in rig.audit.events if e.event == "ack_tap" and e.detail.get("late")]
        assert len(late) == 1


async def test_acknowledge_single_winner(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock, max_size=6) as store:
        svc = rig.service(store)
        await svc.tick()
        wins = await asyncio.gather(*[svc.acknowledge(HH, event, ApproverId(a), "tap") for a in (P, B, P, B, P)])
        assert sum(wins) == 1  # one compare-and-set on `open`
        row = await rig.state(event)
        assert row["state"] == "acknowledged" and row["acked_by"] in (P, B)
        assert len([e for e in rig.audit.events if e.event == "ack_tap" and not e.detail.get("late")]) == 1


# --- restart, outages -----------------------------------------------------------------------------


async def test_restart_mid_stage_resumes(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        first = rig.service(store)
        await first.tick()
        await rig.run(first, 130)
    sent = [k for *_x, k in rig.caregiver.sent]
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:  # a new process: nothing in memory
        second = rig.service(store)
        await second.tick()  # nothing is due yet, and nothing is sent again
        assert [k for *_x, k in rig.caregiver.sent] == sent
        await rig.run(second, 200)
    keys = [str(k) for *_x, k in rig.caregiver.sent]
    assert len(keys) == len(set(keys))  # no key twice, and the sends went on where the first stopped
    assert str(attempt_key(event, "a", P, 4)) in keys and str(attempt_key(event, "a", P, 5)) in keys
    assert len(rig.stage_b.notified) >= 2 and len(rig.stage_c.notified) >= 1  # b and c resumed on their own times


async def test_a_long_outage_sends_the_newest_slot_not_a_burst(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.clock.advance(250)  # the service was down for 250 s: slots 2 to 5 are all past
        await svc.tick()
        sends = [k for _to, _card, k in rig.cards(P)]
        assert sends == [str(attempt_key(event, "a", P, 1)), str(attempt_key(event, "a", P, 5))]
        past = [a["status"] for a in await rig.attempts(event, "a") if a["approver_id"] == P]
        assert past == ["delivered", "cancelled", "cancelled", "cancelled", "delivered"]


async def test_telegram_down_goes_to_b_and_c(rig: AlertRig):
    rig.caregiver.fail("send_card", ChannelDown, times=1000)
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 250)
        assert {a["status"] for a in await rig.attempts(event, "a")} == {"failed"}  # nothing reached Telegram
        assert len(rig.stage_b.notified) >= 2 and len(rig.stage_c.notified) >= 1  # and b and c still went
        assert (await rig.state(event))["state"] == "open"


async def test_stage_b_and_c_follow_the_plan_and_c_is_independent_of_whatsapp(rig: AlertRig):
    """TQ-11: stages a and c use no WhatsApp. The elder channel is down and its window closed; the humans are told."""
    rig.elder.channel_health = ChannelHealth.DOWN
    rig.elder.window_open = False
    rig.elder.fail("send_text", ChannelDown, times=100)
    rig.elder.fail("send_audio", ChannelDown, times=100)
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 250)
        assert len(rig.cards()) >= 10  # stage a, both approvers
        assert len(rig.stage_c.notified) >= 1  # stage c
        assert rig.stage_b.notified == []  # b is left out while WhatsApp is down
        assert (await rig.state(event))["stage"] == "c"


@rule("PE-30")
async def test_stage_b_and_c_times_and_caps_end_to_end(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 125, every=5)
        assert len(rig.stage_b.notified) == 2  # one template per approver at 120 s
        assert rig.stage_c.notified == []  # no call before 240 s
        await rig.run(svc, 125, every=5)  # to 250 s
        assert len(rig.stage_c.notified) == 1 and rig.stage_c.calls_to(ApproverId(P)) == 1  # the primary first
        await rig.run(svc, 60, every=5)  # to 310 s: the backup rings 60 s later
        assert rig.stage_c.calls_to(ApproverId(B)) == 1
        await rig.run(svc, 1000, every=20)
        assert rig.stage_b.calls_to(ApproverId(P)) == 3 and rig.stage_b.calls_to(ApproverId(B)) == 3
        assert len(rig.stage_c.notified) == 6  # voice_calls_per_event
        assert (await rig.state(event))["state"] == "open"  # stage a still has sends left


async def test_voice_calls_per_event_is_configurable(pg_db: PgDb):
    rig = await make_rig(pg_db)
    rig.cfg = AlertConfig(voice_calls_per_event=4)
    await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 1500, every=30)
        assert len(rig.stage_c.notified) == 4  # the fifth call is not placed


# --- the sign-off, caps ---------------------------------------------------------------------------


async def test_so1_expired_silences_b_c_and_alerts_owner(pg_db: PgDb):
    rig = await make_rig(pg_db, so1=False)
    su = await asyncpg.connect(pg_db.dsn())
    try:  # a sign-off that ended a day ago
        await su.execute(
            "INSERT INTO control.signoffs (household_id, scope, granter, granted_at, expires_at)"
            " VALUES ($1, 'SO-1', 'owner', $2, $3)", H, NOW - timedelta(days=91), NOW - timedelta(days=1),
        )  # fmt: skip
    finally:
        await su.close()
    event = await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 700)
        assert rig.stage_b.notified == [] and rig.stage_c.notified == []  # nothing went out
        assert rig.owner_alerts == ["signoff_expired"]  # the owner heard once, however many ticks ran
        expired = [c for c in rig.audit.calls_of("append") if c[0][0].event == "signoff_expired"]
        assert len(expired) == 1 and expired[0][1]["tx"] is None
        assert len(rig.cards(P)) >= 5 and (await rig.state(event))["state"] == "open"  # stage a runs on
        used = [e for e in rig.audit.events if e.event == "signoff_used"]
        assert used == []  # an expired sign-off is never used
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store2:  # a restart does not tell the owner again
        svc2 = rig.service(store2)
        await rig.run(svc2, 200)
        assert rig.owner_alerts == ["signoff_expired"]


async def test_no_signoff_row_behaves_as_off(pg_db: PgDb):
    rig = await make_rig(pg_db, so1=False)
    await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 400)
        assert rig.stage_b.notified == [] and rig.stage_c.notified == []
        assert rig.owner_alerts == ["signoff_expired"]


async def test_a_signoff_that_ends_mid_event_stops_b_and_c_from_then_on(pg_db: PgDb):
    rig = await make_rig(pg_db)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await su.execute("UPDATE control.signoffs SET expires_at = $1", NOW + timedelta(seconds=300))  # ends at 300 s
    finally:
        await su.close()
    await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 290, every=10)
        placed = (len(rig.stage_b.notified), len(rig.stage_c.notified))
        assert placed[0] >= 2 and placed[1] >= 1
        await rig.run(svc, 1500, every=30)  # revoked at 300 s
        assert (len(rig.stage_b.notified), len(rig.stage_c.notified)) == placed


async def test_audit_down_withholds_stage_b_and_c_but_not_stage_a(pg_db: PgDb):
    """Each use of SO-1 needs its audit row; without it no b or c send goes out, and the humans still get stage a."""
    rig = await make_rig(pg_db)
    rig.audit.fail("append", AuditUnavailable, times=1000)
    await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 400, every=10)
        assert rig.stage_b.notified == [] and rig.stage_c.notified == []
        assert len(rig.cards(P)) >= 5
    rig.audit.clear_failures()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        await rig.run(rig.service(store), 60, every=10)
        assert rig.stage_b.notified  # the audit is back: the waiting attempts go out


async def test_all_caps_spent_blocked_no_human_reached(rig: AlertRig):
    event = await rig.event("m1", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 4700, every=20)
        assert (await rig.state(event))["state"] == "open"  # the last stage a send is at 4740 s
        await rig.run(svc, 100, every=20)
        row = await rig.state(event)
        assert (row["state"], row["blocked_reason"]) == ("blocked", "no_human_reached")
        assert len(rig.cards(P)) == 20 and len(rig.cards(B)) == 20  # the stage a cap
        assert (len(rig.stage_b.notified), len(rig.stage_c.notified)) == (6, 6)
        assert rig.owner_alerts[-1] == "no_human_reached"  # the ops record
        blocked = [c for c in rig.audit.calls_of("append") if c[0][0].event == "alert_blocked"]
        assert len(blocked) == 1 and blocked[0][1]["tx"] is None
        before = stage_sends(rig)
        await rig.run(svc, 1000, every=100)
        assert stage_sends(rig) == before  # a blocked event sends nothing more


async def test_scam_alert_is_stage_a_only_and_ends_blocked(rig: AlertRig):
    event = await rig.event("m2", "scam")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 5000, every=60)
        assert rig.stage_b.notified == [] and rig.stage_c.notified == []
        assert len(rig.cards(P)) == 20
        assert (await rig.state(event))["state"] == "blocked"
        used = [e for e in rig.audit.events if e.event == "signoff_used"]
        assert used == []


# --- the elder's clip, the audio ------------------------------------------------------------------


async def test_the_elder_hears_the_clip_at_t0_and_again_at_ten_minutes(rig: AlertRig):
    await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        sent = [(m, str(k)) for m, _to, _p, k in rig.elder.sent]
        assert sent == [("send_audio", "m1:reply:audio"), ("send_text", "m1:reply:text")]
        await rig.run(svc, 590)
        assert len(rig.elder.sent) == 2
        await rig.run(svc, 20)
        sent = [(m, str(k)) for m, _to, _p, k in rig.elder.sent]
        assert sent[2:] == [("send_audio", "m1:reply:repeat:audio"), ("send_text", "m1:reply:repeat:text")]
        await rig.run(svc, 2000, every=100)
        assert len(rig.elder.sent) == 4  # twice in all: at t0 and at t0 + 10 min


async def test_a_scam_clip_is_sent_once_and_not_repeated(rig: AlertRig):
    await rig.event("m2", "scam")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 1500, every=60)
        assert len(rig.elder.sent) == 2  # the clip and its text, once


async def test_worker_and_alert_service_share_the_clip_key(pg_db: PgDb):
    """WU-15 ruling 5: whichever sends the emergency clip first, the elder hears it once."""
    rig = await make_rig(pg_db)
    ring, pub = rig.ring, rig.pub
    for order in ("worker first", "alert first"):
        inbox = "m-" + order.split()[0]
        await add_inbox(pg_db, ring, pub, H, inbox, text="mujhe chest pain hai", received_at=rig.clock.now())
        workers = WorkerRig()
        workers.elder, workers.caregiver, workers.voice = rig.elder, rig.caregiver, rig.voice
        for reply in (ReplyKey.ACK, ReplyKey.TOLD_CAREGIVER):
            rig.voice.add(reply, b"synthetic-" + reply.value.encode(), "hi")
        async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
            worker = Worker(store, workers.deps(store, ring, rig.clock, WorkerConfig()), rig.clock)
            svc = rig.service(store)
            if order == "alert first":
                await rig.event(inbox, "emergency", state="received")
                await svc.tick()
            while await worker.run_once():
                pass
            if order == "worker first":
                await rig.event(inbox, "emergency", state="processed")
                await svc.tick()
        keys = [str(k) for _m, _to, _p, k in rig.elder.sent if str(k).startswith(f"{inbox}:reply")]
        assert sorted(keys) == [f"{inbox}:reply:audio", f"{inbox}:reply:text"], (order, keys)  # once each
        await rig.clock.advance(5)


async def test_audio_attached_only_with_consent(pg_db: PgDb):
    # with consent: the first stage a send to each approver carries the original audio
    rig = await make_rig(pg_db, consents=True)
    rig.elder.media[AUDIO_HANDLE] = blob(b"synthetic-opus-1")
    await rig.event("m3", "unscanned", inbox_kind="audio", media_ref=AUDIO_HANDLE, origin="unscanned_edge")
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 70)
        audio = [(str(to), str(k)) for m, to, _p, k in rig.caregiver.sent if m == "send_audio"]
        assert [to for to, _k in audio] == [P, B]  # each approver once, on the first send only
        assert len(rig.elder.calls_of("fetch_media")) == 1  # fetched once
        assert [e for e in rig.audit.events if e.event == "consent_denied"] == []


async def test_audio_follows_the_first_send_that_goes_out_after_an_outage(pg_db: PgDb):
    rig = await make_rig(pg_db)
    rig.elder.media[AUDIO_HANDLE] = blob()
    await rig.event("m3", "unscanned", inbox_kind="audio", media_ref=AUDIO_HANDLE, origin="unscanned_edge")
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await rig.clock.advance(250)  # the service was not running: slots 1 to 4 are cancelled, slot 5 is sent
        await svc.tick()
        assert [str(to) for m, to, _p, _k in rig.caregiver.sent if m == "send_audio"] == [P, B]
        await rig.run(svc, 400)
        assert len([1 for m, *_x in rig.caregiver.sent if m == "send_audio"]) == 2  # and no more later


async def test_audio_is_not_attached_when_the_gate_is_closed(pg_db: PgDb):
    rig = await make_rig(pg_db, consents=False)  # no row: the gate fails closed
    rig.elder.media[AUDIO_HANDLE] = blob()
    await rig.event("m3", "unscanned", inbox_kind="audio", media_ref=AUDIO_HANDLE, origin="unscanned_edge")
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 70)
        assert [m for m, *_x in rig.caregiver.sent if m == "send_audio"] == []
        assert rig.elder.calls_of("fetch_media") == []  # the media was not even fetched
        assert len(rig.cards(P)) == 2  # the humans still got the alert
        denied = [c for c in rig.audit.calls_of("append") if c[0][0].event == "consent_denied"]
        assert len(denied) == 2 and all(c[1]["tx"] is None for c in denied)  # a denial is audited (both approvers)


async def test_withdrawn_consent_closes_the_gate(pg_db: PgDb):
    rig = await make_rig(pg_db, consents=True)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await grant(su, H, "alert_audio", "telegram_caregiver", event="withdraw")
    finally:
        await su.close()
    rig.elder.media[AUDIO_HANDLE] = blob()
    await rig.event("m3", "unscanned", inbox_kind="audio", media_ref=AUDIO_HANDLE, origin="unscanned_edge")
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        await rig.service(store).tick()
        assert [m for m, *_x in rig.caregiver.sent if m == "send_audio"] == []


async def test_lost_media_does_not_stop_the_card(rig: AlertRig):
    await rig.event("m3", "unscanned", inbox_kind="audio", media_ref="gone", origin="unscanned_edge")  # not fetchable
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        await rig.service(store).tick()
        assert len(rig.cards()) == 2 and [m for m, *_x in rig.caregiver.sent if m == "send_audio"] == []


async def test_unscanned_audio_event_after_120s(pg_db: PgDb):
    """The worker's sweep opens the event for a voice note nobody transcribed; the alert service tells the humans."""
    rig = await make_rig(pg_db)
    await add_inbox(pg_db, rig.ring, rig.pub, H, "voice1", kind="audio", media_ref=AUDIO_HANDLE, received_at=NOW)
    rig.elder.media[AUDIO_HANDLE] = blob()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        assert await unscanned_sweep(store, rig.clock, max_s=120) == 0  # not yet
        await rig.clock.advance(121)
        assert await unscanned_sweep(store, rig.clock, max_s=120) == 1
        assert await unscanned_sweep(store, rig.clock, max_s=120) == 0  # a repeat opens nothing new
        [ev] = await rig.sql("SELECT id, kind, origin FROM alert_events")
        assert (ev["kind"], ev["origin"]) == ("unscanned", "unscanned_overdue")
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 700, every=20)
        assert len(rig.cards(P)) >= 3 and rig.stage_b.notified == [] and rig.stage_c.notified == []  # stage a only
        assert rig.elder.sent == []  # nothing was heard, so no clip is played
        assert (await rig.state(ev["id"]))["stage"] == "a"


# --- no model, no worker --------------------------------------------------------------------------


async def test_all_models_down_still_alerts_and_plays_clip(rig: AlertRig):
    """TQ-11: the service takes no gateway, no speech to text and no classifier, so their outage cannot reach it."""
    params = set(inspect.signature(alerts_pkg.AlertService.__init__).parameters)
    assert not params & {"gateway", "asr", "classifier", "worker", "tts", "vision"}
    down = (FakeGateway(), FakeASR(), FakeClassifier())
    for fake in down:
        for method in ("complete", "transcribe", "flags"):
            fake.fail(method, ChannelDown, times=1000)
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        assert len(rig.cards()) == 2  # both approvers told at t0
        assert [(m, str(k)) for m, _to, _p, k in rig.elder.sent][0] == ("send_audio", "m1:reply:audio")  # clip played
        assert all(f.calls == [] for f in down)  # and nothing asked a model
        assert (await rig.state(event))["last_elder_clip_at"] is not None


def test_no_module_of_the_service_imports_a_model_asr_whatsapp_or_the_worker_loop():
    import ast

    import saathi.alerts.plan as plan_mod
    import saathi.alerts.service as service_mod
    import saathi.alerts.stages as stages_mod

    banned = ("saathi.llm", "saathi.perception", "saathi.understand", "saathi.channels.whatsapp",
              "saathi.worker", "saathi.guards")  # fmt: skip
    for mod in (plan_mod, service_mod, stages_mod):
        imported: set[str] = set()
        for node in ast.walk(ast.parse(inspect.getsource(mod))):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported |= {n.name for n in node.names}
        assert not [m for m in imported if m.startswith(banned)], (mod.__name__, imported)


# --- crash ----------------------------------------------------------------------------------------


async def test_kill_between_send_and_mark_repeats_same_key(rig: AlertRig, monkeypatch: pytest.MonkeyPatch):
    """In process: the service dies after its sends and before its marks. The next service sends the same keys; the
    platform refuses the repeats; each attempt is then marked. (The subprocess version is in test_inbox_crash.)"""
    from saathi.alerts import service as service_mod

    class Killed(BaseException):
        pass

    def die(point: str) -> None:
        raise Killed(point)

    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        monkeypatch.setattr(service_mod, "fault", die)
        svc = rig.service(store)
        with pytest.raises(Killed, match="alert.between_send_and_mark"):
            await svc.step(HH, event)
        assert {a["status"] for a in await rig.attempts(event)} == {"due"}  # sent, not marked
        sent_once = [str(k) for *_x, k in rig.caregiver.sent]
        monkeypatch.undo()
        again = rig.service(store)
        await again.tick()
        repeats = [c for c in rig.caregiver.calls if c[0] == "send_card"]
        assert len(repeats) == 4  # two sends before the kill, the same two after
        assert [str(k) for *_x, k in rig.caregiver.sent] == sent_once  # the platform took each key once
        assert {a["status"] for a in await rig.attempts(event)} == {"delivered"}
        assert len(rig.elder.sent) == 2  # the clip: once


async def test_a_send_rejected_for_another_reason_is_a_failed_attempt(rig: AlertRig):
    rig.caregiver.fail("send_card", SendRejected("chat_not_found"), times=1)
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        await rig.service(store).tick()
        assert sorted(a["status"] for a in await rig.attempts(event, "a")) == ["delivered", "failed"]


# --- the false-alarm counter against the store ----------------------------------------------------


@rule("PE-30")
async def test_third_false_alarm_after_stage_b_opens_one_review(pg_db: PgDb):
    rig = await make_rig(pg_db)
    events = []
    for i in range(1, 5):
        events.append(await rig.event(f"fa{i}", "emergency"))
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 130, every=10)  # stage b has started on every event
        reviews = []
        for ev in events:
            assert await svc.acknowledge(HH, ev, ApproverId(P), "tap")
            assert await svc.false_alarm(HH, ev, ApproverId(B)) is True
            reviews.append(rig.owner_alerts.count("false_alarm_review"))
        assert reviews == [0, 0, 1, 1]  # the third opens one review, the fourth does not open another
        flags = await rig.sql("SELECT false_alarm, state FROM alert_events")
        assert {(f["false_alarm"], f["state"]) for f in flags} == {(True, "acknowledged")}  # the state did not change
        assert await svc.false_alarm(HH, events[0], ApproverId(B)) is False  # a repeat flag changes nothing
        assert rig.owner_alerts.count("false_alarm_review") == 1
        counted = [e.detail for e in rig.audit.events if e.event == "false_alarm"]
        assert [d["count"] for d in counted[:4]] == [1, 2, 3, 4] and all(d["counted"] for d in counted)


@rule("PE-30")
async def test_false_alarm_before_stage_b_does_not_count(pg_db: PgDb):
    rig = await make_rig(pg_db)
    event = await rig.event()
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        await rig.run(svc, 90, every=10)  # stage b starts at 120 s
        await svc.acknowledge(HH, event, ApproverId(P), "tap")
        assert await svc.false_alarm(HH, event, ApproverId(P)) is True
        [e] = [e for e in rig.audit.events if e.event == "false_alarm"]
        assert (e.detail["counted"], e.detail["count"]) == (False, 0)


async def test_false_alarm_needs_an_acknowledged_event_and_a_registered_approver(rig: AlertRig):
    event = await rig.event()
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        svc = rig.service(store)
        await svc.tick()
        assert await svc.false_alarm(HH, event, ApproverId(P)) is False  # still open: nothing to flag
        await svc.acknowledge(HH, event, ApproverId(P), "tap")
        assert await svc.false_alarm(HH, event, ApproverId("stranger")) is False
        assert (await rig.state(event))["false_alarm"] is False


# --- cards ----------------------------------------------------------------------------------------


async def test_the_card_holds_slots_and_two_buttons_never_the_message(pg_db: PgDb):
    rig = await make_rig(pg_db)
    await add_inbox(pg_db, rig.ring, rig.pub, H, "mx", text="canary-text-should-not-appear", received_at=NOW)
    event = await rig.event("mx", "emergency", state="processed")
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        await rig.service(store).tick()
    [(_to, card, key), _second] = rig.cards()
    assert key == str(attempt_key(event, "a", P, 1))
    text = " ".join([card.title, *card.lines])  # type: ignore[attr-defined]
    assert "canary" not in text
    assert [(b.text, b.data) for b in card.buttons] == [  # type: ignore[attr-defined]
        ("Acknowledge", f"ack:{event}"), ("False alarm", f"fa:{event}"),
    ]  # fmt: skip


def test_the_attempt_key_has_the_documented_shape():
    assert attempt_key("al_x", "b", "ap1", 3) == idem_key("alert", "al_x", "b", "ap1", "3")
    assert str(attempt_key("al_x", "b", "ap1", 3)) == "alert:al_x:b:ap1:3"
