"""Ride SQL shares row transactions and savepoints. All fixtures are synthetic."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from time import monotonic
from types import SimpleNamespace

import asyncpg
import pytest
from fakes import FakeClock
from fakes.store import FakeTx
from test_rides_db import harness, ride_count, seed_places
from worker_helpers import NOW, Rig, add_inbox, household_config, provision, rows

from saathi.domain import MediaKind
from saathi.errors import StoreUnavailable
from saathi.guards.radar import RadarResult
from saathi.ids import HouseholdId, InboxId
from saathi.rides.place import Place
from saathi.rides.repo import PlaceRepo, optional
from saathi.worker import process, steps


class RecordingTx(FakeTx):
    async def fetch(self, sql, *args):
        result = await super().fetch(sql, *args)
        if "current_setting('lock_timeout')" in sql:
            return [{"lock_timeout": "2s", "statement_timeout": "3s"}]
        return result


@pytest.fixture
def worker_spy(monkeypatch):
    """Run process and real sends with SQL recording and stubbed required repositories."""
    transactions = []

    class Store:
        @asynccontextmanager
        async def tx(self, **_kwargs):
            tx = RecordingTx(HouseholdId("h1"), NOW)
            transactions.append(tx)
            yield tx

    ctx = SimpleNamespace(
        household=HouseholdId("h1"), inbox=InboxId("m1"), elder="eh1", kind=MediaKind.TEXT,
        consent=True, audio_consent=False, media=None, parts={"text": "cab to Clinic A"},
        cfg=household_config("ap1"), person="ref-h1", last_inbound=NOW, request="rq1",
    )  # fmt: skip

    async def load(*_args):
        return ctx

    async def held(tx, *_args):
        await tx.execute("required holds")

    async def radar_at(tx, *_args):
        await tx.execute("required radar")

    async def alert(tx, *_args):
        await tx.execute("required alert")

    async def transition(tx, *_args, **_kwargs):
        await tx.execute("required transition")

    async def settle(tx, *_args):
        await tx.execute("required settle")
        return True

    monkeypatch.setattr(steps, "_load", load)
    monkeypatch.setattr(steps, "holds", held)
    monkeypatch.setattr(steps.InboxRepo, "set_radar_at", radar_at)
    monkeypatch.setattr(steps.AlertRepo, "open", alert)
    monkeypatch.setattr(steps.RequestRepo, "transition", transition)
    monkeypatch.setattr(steps.InboxRepo, "settle", settle)
    rig = Rig()
    deps = rig.deps(Store(), None, FakeClock(NOW))
    lease = SimpleNamespace(inbox_id=ctx.inbox, elder_id=ctx.elder)
    return SimpleNamespace(rig=rig, deps=deps, lease=lease, transactions=transactions)


async def test_worker_uses_only_row_transactions_after_required_sql(worker_spy, monkeypatch):
    spy = worker_spy
    seen = []

    async def places(tx):
        seen.append(("places", tx))
        await tx.execute("ride places")
        return [Place("p1", "Clinic A", "2 Test Road, Sample Town")]

    async def count(tx, *_args, **_kwargs):
        seen.append(("count", tx))
        await tx.execute("ride count")
        return True

    monkeypatch.setattr(PlaceRepo, "all", places)
    monkeypatch.setattr(PlaceRepo, "count_once", count)
    assert (await process(spy.lease, spy.deps)).inbox_state == "processed"
    assert len(spy.transactions) == 3  # _load stubbed: TX-W2, TX-W3, TX-W4 only
    assert seen == [("places", spy.transactions[0]), ("count", spy.transactions[2])]
    for tx, required, ride in (
        (spy.transactions[0], "required radar", "ride places"),
        (spy.transactions[2], "required settle", "ride count"),
    ):
        sql = [s for s, _args in tx.statements]
        assert sql.index(required) < sql.index("SAVEPOINT ride") < sql.index(ride)
        assert "SELECT set_config('lock_timeout', '200ms', true)" in sql
        assert "SELECT set_config('statement_timeout', '500ms', true)" in sql
        assert not any(s.startswith("required") for s in sql[sql.index(ride) + 1 :])
        assert tx.statements[-2:] == [
            ("SELECT set_config('lock_timeout', $1, true)", ("2s",)),
            ("SELECT set_config('statement_timeout', $1, true)", ("3s",)),
        ]


@pytest.mark.parametrize("guard", ["emergency", "scam", "radar_failed"])
async def test_unsafe_rows_execute_no_ride_sql(worker_spy, monkeypatch, guard):
    spy = worker_spy

    async def radar(*_args, **_kwargs):
        if guard == "radar_failed":
            raise RuntimeError("synthetic radar failure")
        return RadarResult(**{guard: True})

    monkeypatch.setattr(steps.radar, "combine", radar)
    await process(spy.lease, spy.deps)
    sql = [s for tx in spy.transactions for s, _args in tx.statements]
    assert "required alert" in sql and "required settle" in sql
    assert not any("ride" in s or "addresses" in s or "ride_counts" in s for s in sql)
    assert [str(key) for *_rest, key in spy.rig.caregiver.sent] == ["m1:summary:ap1"]
    assert len(spy.rig.elder.sent) == 1


@pytest.mark.parametrize("error", [StoreUnavailable, asyncio.CancelledError])
async def test_connection_failure_and_cancel_propagate(error):
    tx = RecordingTx(HouseholdId("h1"), NOW)

    async def work():
        raise error()

    with pytest.raises(error):
        await optional(tx, work, inbox=InboxId("m1"))
    assert not any("ROLLBACK TO" in sql for sql, _args in tx.statements)


async def test_optional_failure_rolls_back_and_logs_only_id_and_class(caplog):
    tx = RecordingTx(HouseholdId("h1"), NOW)

    async def work():
        raise ValueError("Clinic A, 2 Test Road, Sample Town")

    assert await optional(tx, work, inbox=InboxId("m1")) is None
    assert [sql for sql, _args in tx.statements][-2:] == [
        "ROLLBACK TO SAVEPOINT ride",
        "RELEASE SAVEPOINT ride",
    ]
    assert caplog.messages == ["ride database failed for m1: ValueError"]


async def test_count_lock_timeout_settles_and_keeps_idle_pool_connections(pg_db, monkeypatch, caplog):
    ring, pub = await provision(pg_db)
    await seed_places(pg_db)
    rig = Rig()
    locker = await asyncpg.connect(pg_db.dsn())
    try:
        async with locker.transaction():
            await locker.execute("SELECT id FROM addresses WHERE id = 'pl_clinic' FOR UPDATE")
            async with harness(pg_db, rig, ring) as (store, deps, clock):
                idle = store._pool.get_idle_size()
                timings = []
                real = PlaceRepo.count_once

                async def timed(*args, **kwargs):
                    started = monotonic()
                    try:
                        return await real(*args, **kwargs)
                    finally:
                        timings.append(monotonic() - started)

                monkeypatch.setattr(PlaceRepo, "count_once", timed)
                for inbox in ("m1", "m2", "m3"):
                    await add_inbox(pg_db, ring, pub, "h1", inbox, text="cab to Clinic A")
                    lease = await store.scan.lease_next(clock.now(), 60)
                    assert lease is not None and lease.inbox_id == inbox
                    assert (await asyncio.wait_for(process(lease, deps), 3)).inbox_state == "processed"
                    assert store._pool.get_idle_size() == idle
                    assert store._pool.get_size() == idle  # no borrowed connection remains
                    state = await rows(pg_db, "SELECT state FROM inbox WHERE id = $1", inbox)
                    assert state[0]["state"] == "processed"
                assert len(timings) == 3 and all(0.15 <= elapsed < 0.9 for elapsed in timings)
    finally:
        await locker.close()
    assert await ride_count(pg_db, "pl_clinic") == 0
    assert await rows(pg_db, "SELECT 1 FROM ride_counts") == []  # claims rolled back too
    assert len(rig.elder.sent) == 3
    assert len([key for *_rest, key in rig.caregiver.sent if ":summary:" in key]) == 3
    assert "ride database failed for m1: LockNotAvailableError" in caplog.messages


@pytest.mark.parametrize("failure", ["missing", "count_raises", "places_raises"])
async def test_optional_failure_preserves_settlement_summary_and_existing_alert(pg_db, monkeypatch, failure):
    ring, pub = await provision(pg_db)
    await seed_places(pg_db)
    await add_inbox(pg_db, ring, pub, "h1", "m1", text="cab to Clinic A")
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await su.execute(
            "INSERT INTO alert_events (id, household_id, inbox_id, elder_id, kind, origin, t0, next_due_at)"
            " VALUES ('a1', 'h1', 'm1', 'eh1', 'unscanned', 'unscanned_overdue', $1, $1)",
            NOW,
        )
        before = await su.fetch("SELECT * FROM alert_events")
    finally:
        await su.close()
    real = PlaceRepo.count_once
    radar_txs, settle_txs, ride_txs = [], [], []
    real_radar, real_settle = steps.InboxRepo.set_radar_at, steps.InboxRepo.settle

    async def radar_at(tx, *args):
        radar_txs.append(tx)
        return await real_radar(tx, *args)

    async def settle(tx, *args):
        settle_txs.append(tx)
        return await real_settle(tx, *args)

    monkeypatch.setattr(steps.InboxRepo, "set_radar_at", radar_at)
    monkeypatch.setattr(steps.InboxRepo, "settle", settle)

    async def failed_count(tx, *args, **kwargs):
        ride_txs.append(tx)
        if failure == "missing":
            conn = await asyncpg.connect(pg_db.dsn())
            try:
                await conn.execute("DELETE FROM addresses WHERE id = 'pl_clinic'")
            finally:
                await conn.close()
            return await real(tx, *args, **kwargs)
        await real(tx, *args, **kwargs)  # prove both claim and increment roll back
        raise ValueError("synthetic failure")

    async def failed_places(tx):
        ride_txs.append(tx)
        await tx.execute("SELECT 1 / 0")  # an aborted transaction must recover before commit

    monkeypatch.setattr(PlaceRepo, "count_once", failed_count)
    if failure == "places_raises":
        monkeypatch.setattr(PlaceRepo, "all", failed_places)
    rig = Rig()
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        lease = await store.scan.lease_next(clock.now(), 60)
        assert lease is not None
        assert (await process(lease, deps)).inbox_state == "processed"
    assert ride_txs == (radar_txs if failure == "places_raises" else settle_txs)
    row = (await rows(pg_db, "SELECT state, radar_at FROM inbox WHERE id = 'm1'"))[0]
    assert row["state"] == "processed" and row["radar_at"] is not None
    assert await rows(pg_db, "SELECT * FROM alert_events") == before
    assert len([key for *_rest, key in rig.caregiver.sent if ":summary:" in key]) == 1
    assert len(rig.elder.sent) == 1 and await rows(pg_db, "SELECT 1 FROM ride_counts") == []
    if failure != "missing":
        assert await ride_count(pg_db, "pl_clinic") == 0


async def test_lost_settle_skips_count_then_retry_claims_once(pg_db, monkeypatch):
    ring, pub = await provision(pg_db)
    await seed_places(pg_db)
    await add_inbox(pg_db, ring, pub, "h1", "m1", text="cab to Clinic A")
    real = steps.InboxRepo.settle
    calls = []
    real_count = PlaceRepo.count_once

    async def count(*args, **kwargs):
        calls.append(1)
        return await real_count(*args, **kwargs)

    async def lost(*_args):
        return False

    monkeypatch.setattr(PlaceRepo, "count_once", count)
    monkeypatch.setattr(steps.InboxRepo, "settle", lost)
    rig = Rig()
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        lease = await store.scan.lease_next(clock.now(), 60)
        assert lease is not None
        await process(lease, deps)
        assert calls == [] and await rows(pg_db, "SELECT 1 FROM ride_counts") == []
        assert (await rows(pg_db, "SELECT state FROM inbox WHERE id = 'm1'"))[0]["state"] == "leased"
        monkeypatch.setattr(steps.InboxRepo, "settle", real)
        await process(lease, deps)
        assert calls == [1] and await ride_count(pg_db, "pl_clinic") == 1
        async with store.tx(household=HouseholdId("h1")) as tx:
            assert not await optional(
                tx,
                lambda: real_count(tx, "pl_clinic", source="inbox", source_id="m1"),
                inbox=InboxId("m1"),
            )  # ON CONFLICT replay does not increment
    assert await ride_count(pg_db, "pl_clinic") == 1
    assert len(await rows(pg_db, "SELECT 1 FROM ride_counts")) == 1
    assert len([key for *_rest, key in rig.caregiver.sent if ":ride:" in key]) == 1


@pytest.mark.parametrize("stall", [False, True])
async def test_savepoint_restores_settings_before_later_required_statement(pg_db, stall):
    ring, _pub = await provision(pg_db)
    async with harness(pg_db, Rig(), ring) as (store, _deps, _clock):
        async with store.tx(household=HouseholdId("h1")) as tx:
            await tx.execute("SELECT set_config('lock_timeout', '2s', true)")
            await tx.execute("SELECT set_config('statement_timeout', '3s', true)")

            async def work():
                assert (await tx.fetch("SHOW lock_timeout"))[0]["lock_timeout"] == "200ms"
                assert (await tx.fetch("SHOW statement_timeout"))[0]["statement_timeout"] == "500ms"
                if stall:
                    await tx.execute("SELECT pg_sleep(2)")
                return True

            started = monotonic()
            result = await optional(tx, work, inbox=InboxId("m1"))
            assert result is (None if stall else True)
            if stall:
                assert 0.4 <= monotonic() - started < 1.2
            assert (await tx.fetch("SHOW lock_timeout"))[0]["lock_timeout"] == "2s"
            assert (await tx.fetch("SHOW statement_timeout"))[0]["statement_timeout"] == "3s"
            await tx.execute("SELECT pg_sleep(0.65)")  # needs the caller's restored timeout
