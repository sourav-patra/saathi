"""LT-09: the lease helper, the renewer, the ack pass and dead rows (M12, M04; RQ-79; TQ-03, 06). Synthetic data."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from conftest import PgDb
from fakes import FakeClock
from store_helpers import open_store
from worker_helpers import NOW, Rig, Role, add_inbox, audio, provision, rows

from saathi.errors import ChannelDown
from saathi.ids import IdemKey, PersonRef
from saathi.store import Store
from saathi.store.repos import InboxRepo
from saathi.worker import LeaseLost, LeaseRenewer, Worker, WorkerConfig, ack_pass, process

H = "h1"


async def lease(store: Store, clock: FakeClock, secs: int = 60):
    return await store.scan.lease_next(clock.now(), secs)


async def test_oldest_first_per_elder(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    for i, mins in (("m_new", 3), ("m_old", 1), ("m_mid", 2)):  # inserted out of order
        await add_inbox(pg_db, ring, pub, H, i, text="x", received_at=NOW - timedelta(minutes=10 - mins))
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        first = await lease(store, clock)
        assert first is not None and first.inbox_id == "m_old"
        assert await lease(store, clock) is None  # one row of an elder is leased at a time
        async with store.tx(household=first.household_id) as tx:
            assert await InboxRepo.settle(tx, first, "processed")
        second = await lease(store, clock)
        assert second is not None and second.inbox_id == "m_mid"


async def test_not_before_row_does_not_block(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    await add_inbox(pg_db, ring, pub, H, "m_old", text="x", received_at=NOW - timedelta(minutes=2),
                    not_before=NOW + timedelta(minutes=5))  # fmt: skip
    await add_inbox(pg_db, ring, pub, H, "m_new", text="x", received_at=NOW - timedelta(minutes=1))
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        got = await lease(store, clock)
        assert got is not None and got.inbox_id == "m_new"  # the rate-limited row waits; the next one goes
        assert await lease(store, clock) is None
        await clock.advance(6 * 60)  # past not_before, and the lease of m_new is gone
        again = await lease(store, clock)
        assert again is not None and again.inbox_id in {"m_old", "m_new"}


async def test_renewal_keeps_slow_step(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    await add_inbox(pg_db, ring, pub, H, "m1", text="x")
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        got = await lease(store, clock, 2)
        assert got is not None
        async with LeaseRenewer(store, got, clock, lease_s=2, interval_s=0.02) as held:
            assert held == got
            for _ in range(5):  # a step far slower than the lease: 5 x 1 s of clock time
                await clock.advance(1)
                await asyncio.sleep(0.15)
                assert await lease(store, clock, 2) is None  # nobody can take the row
        await clock.advance(10)  # no renewer now: the lease expires
        stolen = await lease(store, clock, 2)
        assert stolen is not None and stolen.token != got.token


async def test_renewer_notices_a_lost_lease(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    await add_inbox(pg_db, ring, pub, H, "m1", text="x")
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        mine = await lease(store, clock, 60)
        assert mine is not None
        await clock.advance(61)
        assert await lease(store, clock, 60) is not None  # a second worker took the row
        renewer = LeaseRenewer(store, mine, clock, interval_s=0.02)
        async with renewer:
            await asyncio.sleep(0.15)
    assert renewer.lost is True and renewer.renewals == 0  # the refused renewal is recorded, nothing is extended


async def test_stalled_worker_writes_nothing(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    rig = Rig()
    rig.asr.script.append("mujhe chest pain hai")
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with open_store(pg_db, Role.CORE, clock=clock) as store_a, open_store(pg_db, Role.CORE, clock=clock) as b:
        deps = rig.deps(store_a, ring, clock)
        mine = await lease(store_a, clock)
        assert mine is not None
        thief: list = []

        real = rig.asr.transcribe

        async def stall(audio_blob, *, hint="hi"):  # the worker stalls in ASR; its lease runs out; another takes it
            await clock.advance(61)
            thief.append(await lease(b, clock))
            return await real(audio_blob, hint=hint)

        rig.asr.transcribe = stall  # type: ignore[method-assign]
        try:
            await process(mine, deps)
            raise AssertionError("a stalled worker must not finish")
        except LeaseLost:
            pass
    assert thief and thief[0] is not None and thief[0].token != mine.token
    assert await rows(pg_db, "SELECT 1 FROM raw_text WHERE inbox_id = 'm1' AND part = 'transcript'") == []
    assert await rows(pg_db, "SELECT 1 FROM alert_events") == []  # no alert written after the lease was lost
    assert rig.caregiver.sent == [] and rig.elder.sent == []  # and no effect sent
    state = await rows(pg_db, "SELECT state, lease_token FROM inbox WHERE id = 'm1'")
    assert state[0]["state"] == "leased" and state[0]["lease_token"] == thief[0].token  # the thief's lease is intact


async def test_fifth_failure_dead_and_alert(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    rig = Rig()
    rig.elder.fail("fetch_media", ChannelDown, times=10)
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        worker = Worker(store, rig.deps(store, ring, clock), clock)
        for _ in range(5):  # five attempts, each failing and each leaving a lease that runs out
            assert await worker.run_once() is True
            await clock.advance(61)
        assert await worker.run_once() is False  # the sixth round reaps instead of leasing
        assert await lease(store, clock) is None
    [row] = await rows(pg_db, "SELECT state, attempts FROM inbox WHERE id = 'm1'")
    assert (row["state"], row["attempts"]) == ("dead", 5)
    assert [e.event for e in rig.audit.events if e.event == "inbox_dead"] == ["inbox_dead"]
    assert rig.owner_alerts.count("inbox_dead") == 1  # the owner is told once
    [alert] = await rows(pg_db, "SELECT kind, origin FROM alert_events WHERE inbox_id = 'm1'")
    assert (alert["kind"], alert["origin"]) == ("unscanned", "unscanned_overdue")  # a human hears of the voice note


async def test_two_workers_skip_locked(pg_db: PgDb):
    ring_pubs = {}
    for h in ("h1", "h2", "h3", "h4"):
        ring_pubs[h] = await provision(pg_db, h)
        await add_inbox(pg_db, *ring_pubs[h], h, "m" + h, text="x")
    clock = FakeClock(NOW)
    async with (
        open_store(pg_db, Role.CORE, clock=clock) as s1,
        open_store(pg_db, Role.CORE, clock=clock) as s2,
        open_store(pg_db, Role.CORE, clock=clock) as s3,
        open_store(pg_db, Role.CORE, clock=clock) as s4,
    ):
        got = await asyncio.gather(*(lease(s, clock) for s in (s1, s2, s3, s4)))
    assert all(g is not None for g in got)
    assert len({g.inbox_id for g in got if g}) == 4  # four workers, four different rows


async def test_two_workers_never_hold_two_rows_of_one_elder(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    for i in range(6):
        await add_inbox(pg_db, ring, pub, H, f"m{i}", text="x", received_at=NOW - timedelta(minutes=10 - i))
    async with open_store(pg_db, Role.CORE, clock=clock) as s1, open_store(pg_db, Role.CORE, clock=clock) as s2:
        for _ in range(3):
            got = await asyncio.gather(lease(s1, clock), lease(s2, clock))
            leased = [g for g in got if g is not None]
            assert len(leased) <= 1  # the advisory lock on the elder lets one worker in
            held = await rows(pg_db, "SELECT 1 FROM inbox WHERE state = 'leased' AND lease_until >= $1", clock.now())
            assert len(held) <= 1
            await clock.advance(61)


async def test_ack_pass_within_10s(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="x", received_at=NOW)
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        deps = rig.deps(store, ring, clock, WorkerConfig())
        await clock.advance(5)  # 5 s after it arrived
        assert await ack_pass(store, deps, clock) == 1
        assert await ack_pass(store, deps, clock) == 0  # acked once
    assert [(m, k) for m, _to, _p, k in rig.elder.sent] == [("send_text", IdemKey("m1:ack:text"))]
    [row] = await rows(pg_db, "SELECT acked_at FROM inbox WHERE id = 'm1'")
    assert NOW <= row["acked_at"] <= NOW + timedelta(seconds=10)
    assert rig.caregiver.sent == []  # an acknowledgement is not a summary, and it makes no request
    assert await rows(pg_db, "SELECT 1 FROM requests") == []


async def test_ack_after_a_crash_between_send_and_mark_sends_once(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    clock = FakeClock(NOW)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="x")
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        deps = rig.deps(store, ring, clock)
        await rig.elder.send_text(PersonRef("ref-h1"), "ok", key=IdemKey("m1:ack:text"))  # then the worker died
        assert await ack_pass(store, deps, clock) == 1  # the repeat key counts as delivered
    assert len([1 for m, *_ in rig.elder.sent if m == "send_text"]) == 1
    assert (await rows(pg_db, "SELECT acked_at FROM inbox WHERE id = 'm1'"))[0]["acked_at"] is not None
