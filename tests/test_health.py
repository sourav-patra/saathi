"""Health probes and the decision function (LT-30; RQ-104, TQ-66). Fakes only, plus one run on a real database."""

from __future__ import annotations

import asyncio
from collections import namedtuple
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import PgDb
from fakes import FakeStore
from store_helpers import open_store

from saathi.clock import FakeClock
from saathi.errors import StoreUnavailable
from saathi.health import (
    OK_BODY,
    ProbeResult,
    ProbeStatus,
    aggregate,
    beat_loop,
    beat_once,
    body_for,
    decide,
    disk_probe,
    edge_check,
    skew_probe,
)
from saathi.store import Role

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
Usage = namedtuple("Usage", "total used free")


def ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("beat_at", "status", "expected"),
    [
        (ago(0), "ok", 200),
        (ago(29.9), "ok", 200),
        (ago(30), "ok", 503),  # not "under 30 s old"
        (ago(31), "ok", 503),
        (ago(3600), "ok", 503),
        (None, "ok", 503),  # no core beat at all
        (None, "", 503),
        (ago(1), "unavailable", 503),
        (ago(1), "fail", 503),
        (ago(1), "OK", 503),
        (ago(1), "", 503),
        (NOW + timedelta(seconds=5), "ok", 200),  # tiny skew between two clocks
        (NOW + timedelta(seconds=300), "ok", 503),  # a beat from the future is not fresh
        (datetime(2026, 1, 1, 11, 59, 59), "ok", 503),  # naive time: cannot compare, fails closed
    ],
)
def test_decide_ok_only_when_fresh_and_ok(beat_at, status, expected):
    assert decide(beat_at, status, NOW) == expected


def test_decide_max_age_is_a_parameter():
    assert decide(ago(40), "ok", NOW, max_age_s=60) == 200
    assert decide(ago(40), "ok", NOW, max_age_s=30) == 503


def test_decide_naive_now_fails_closed():
    assert decide(NOW, "ok", datetime(2026, 1, 1, 12, 0)) == 503


def test_503_stale_heartbeat():
    assert decide(ago(31), "ok", NOW) == 503
    assert body_for(503) == ""


def test_body_is_ok_and_nothing_else():
    assert body_for(200) == OK_BODY == "ok"
    assert body_for(503) == ""
    for code in (200, 503):
        body = body_for(code)
        assert body in ("ok", "")
        assert not any(ch.isdigit() for ch in body)


async def test_503_full_disk(monkeypatch, tmp_path: Path):
    def fake(total: int, free: int):
        return lambda _p: Usage(total, total - free, free)

    monkeypatch.setattr("saathi.health.shutil.disk_usage", fake(1000, 95))  # 9.5% free
    full = await disk_probe(tmp_path)
    assert full.status is ProbeStatus.FAIL and full.gates_route
    assert aggregate([full]) == "fail"
    assert decide(NOW, aggregate([full]), NOW) == 503

    monkeypatch.setattr("saathi.health.shutil.disk_usage", fake(1000, 100))  # exactly 10% free: not under 10
    assert (await disk_probe(tmp_path)).status is ProbeStatus.WARN
    monkeypatch.setattr("saathi.health.shutil.disk_usage", fake(1000, 150))  # 15% free: alert, route stays ok
    warn = await disk_probe(tmp_path)
    assert warn.status is ProbeStatus.WARN and not warn.gates_route
    assert decide(NOW, aggregate([warn]), NOW) == 200
    monkeypatch.setattr("saathi.health.shutil.disk_usage", fake(1000, 200))  # 20% free: fine
    assert (await disk_probe(tmp_path)).status is ProbeStatus.OK


async def test_disk_probe_on_a_real_path_and_a_missing_path(tmp_path: Path):
    real = await disk_probe(tmp_path)
    assert real.status in (ProbeStatus.OK, ProbeStatus.WARN, ProbeStatus.FAIL) and real.value is not None
    missing = await disk_probe(tmp_path / "does-not-exist")
    assert missing.status is ProbeStatus.UNAVAILABLE


async def test_disk_probe_zero_size_is_unavailable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("saathi.health.shutil.disk_usage", lambda _p: Usage(0, 0, 0))
    assert (await disk_probe(tmp_path)).status is ProbeStatus.UNAVAILABLE


class DbClockTx:
    def __init__(self, db_now: datetime | None, error: BaseException | None) -> None:
        self._db_now, self._error = db_now, error
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.household = ""
        self.now = NOW

    async def execute(self, sql: str, *args: Any) -> str:
        self.statements.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Mapping[str, Any]]:
        self.statements.append((sql, args))
        if self._error:
            raise self._error
        return [{"db_now": self._db_now}]


class ClockStore:
    """A store whose Postgres `now()` is scripted."""

    def __init__(self, db_now: datetime | None = None, error: BaseException | None = None) -> None:
        self.db_now, self.error = db_now, error

    @asynccontextmanager
    async def tx(self, *, household: str, lock_household: bool = False) -> AsyncIterator[DbClockTx]:
        assert household
        yield DbClockTx(self.db_now, self.error)


async def test_skew_alert_and_refusal():
    clock = FakeClock(NOW)
    cases = [
        (0, ProbeStatus.OK, False),
        (5, ProbeStatus.OK, False),  # over 5 s alerts: exactly 5 does not
        (5.5, ProbeStatus.WARN, False),
        (-6, ProbeStatus.WARN, False),  # the database ahead of the app counts the same
        (15, ProbeStatus.WARN, False),  # over 15 s refuses: exactly 15 does not
        (15.5, ProbeStatus.FAIL, True),
        (-40, ProbeStatus.FAIL, True),
    ]
    for skew, status, refuses in cases:
        r = await skew_probe(ClockStore(NOW - timedelta(seconds=skew)), clock)  # type: ignore[arg-type]
        assert (r.status, r.refuses) == (status, refuses), skew
        assert r.value == abs(skew)
    # skew alerts the owner but is not a reason for the public route to answer 503
    r = await skew_probe(ClockStore(NOW - timedelta(seconds=40)), clock)  # type: ignore[arg-type]
    assert aggregate([r]) == "ok"


async def test_skew_probe_that_cannot_run_is_unavailable():
    clock = FakeClock(NOW)
    for store in (ClockStore(error=StoreUnavailable("down")), ClockStore(db_now=None)):
        r = await skew_probe(store, clock)  # type: ignore[arg-type]
        assert r.status is ProbeStatus.UNAVAILABLE and not r.refuses


DISK_FULL = ProbeResult("disk", ProbeStatus.FAIL, 4.0, gates_route=True)


def ok(name: str = "p") -> ProbeResult:
    return ProbeResult(name, ProbeStatus.OK)


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([], "unavailable"),  # nothing was run
        ([ok()], "ok"),
        ([ok("a"), ok("b")], "ok"),
        ([ok(), ProbeResult("x", ProbeStatus.WARN)], "ok"),
        ([ok(), ProbeResult("x", ProbeStatus.UNAVAILABLE)], "unavailable"),
        ([ProbeResult("x", ProbeStatus.UNAVAILABLE), DISK_FULL], "unavailable"),
        ([ok(), DISK_FULL], "fail"),
        ([ok(), ProbeResult("s", ProbeStatus.FAIL)], "ok"),
    ],
)
def test_aggregate_table(results, expected):
    assert aggregate(results) == expected


def beat_args(store: FakeStore) -> list[tuple[str, datetime, str]]:
    return [
        (args[0], args[1], args[2])
        for tx in store.transactions
        for sql, args in tx.statements
        if "control.beat" in sql
    ]


async def test_unrun_probe_not_ok():
    clock = FakeClock(NOW)
    store = FakeStore(clock)

    async def boom() -> ProbeResult:
        raise RuntimeError("no")

    async def fine() -> ProbeResult:
        return ok("fine")

    assert await beat_once(store, clock, [boom, fine]) == "unavailable"  # type: ignore[arg-type]
    assert await beat_once(store, clock, []) == "unavailable"  # type: ignore[arg-type]
    assert await beat_once(store, clock, [fine]) == "ok"  # type: ignore[arg-type]
    assert [b[2] for b in beat_args(store)] == ["unavailable", "unavailable", "ok"]
    assert {b[0] for b in beat_args(store)} == {"core"}


async def test_hanging_probe_is_unavailable():
    clock = FakeClock(NOW)
    store = FakeStore(clock)

    async def hang() -> ProbeResult:
        await asyncio.sleep(3600)
        return ok("hang")

    async def fine() -> ProbeResult:
        return ok("fine")

    status = await asyncio.wait_for(
        beat_once(store, clock, [hang, fine], probe_timeout_s=0.05),  # type: ignore[arg-type]
        timeout=2,
    )
    assert status == "unavailable"
    assert [b[2] for b in beat_args(store)] == ["unavailable"]  # the beat is still written


async def test_beat_write_failure_is_reported_not_raised():
    clock = FakeClock(NOW)
    store = FakeStore(clock)
    store.fail("tx", StoreUnavailable("down"))

    async def fine() -> ProbeResult:
        return ok()

    assert await beat_once(store, clock, [fine]) == "unavailable"  # type: ignore[arg-type]


async def test_alert_callback_gets_non_ok_results_and_cannot_stop_the_beat():
    clock = FakeClock(NOW)
    store = FakeStore(clock)
    seen: list[ProbeResult] = []
    warn = ProbeResult("disk", ProbeStatus.WARN, 15.0)

    async def warned() -> ProbeResult:
        return warn

    async def good() -> ProbeResult:
        return ok()

    async def alert(r: ProbeResult) -> None:
        seen.append(r)
        raise RuntimeError("chat down")

    assert await beat_once(store, clock, [warned, good], alert) == "ok"  # type: ignore[arg-type]
    assert seen == [warn]
    assert len(beat_args(store)) == 1


async def test_beat_loop_beats_every_interval_and_stops_on_cancel():
    clock = FakeClock(NOW)
    store = FakeStore(clock)

    async def fine() -> ProbeResult:
        return ok()

    task = asyncio.create_task(beat_loop(store, clock, [fine], every_s=10))  # type: ignore[arg-type]
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(beat_args(store)) == 1
    await clock.advance(10)
    for _ in range(5):
        await asyncio.sleep(0)
    await clock.advance(10)
    for _ in range(5):
        await asyncio.sleep(0)
    beats = beat_args(store)
    assert [b[1] for b in beats] == [NOW, NOW + timedelta(seconds=10), NOW + timedelta(seconds=20)]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class BeatStore:
    """Emulates control.beat: remembers the beats, returns the newest core beat. Optionally fails the write."""

    def __init__(self, core_at: datetime | None, core_status: str, fail: bool = False) -> None:
        self.core_at, self.core_status, self.fail = core_at, core_status, fail
        self.beats: list[str] = []

    @asynccontextmanager
    async def tx(self, *, household: str, lock_household: bool = False) -> AsyncIterator[BeatTx]:
        if self.fail:
            raise StoreUnavailable("down")
        yield BeatTx(self)


class BeatTx:
    household = ""
    now = NOW

    def __init__(self, store: BeatStore) -> None:
        self.store = store

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Mapping[str, Any]]:
        self.store.beats.append(args[0])
        return [{"core_at": self.store.core_at, "core_status": self.store.core_status}]


@pytest.mark.parametrize(
    ("core_at", "core_status", "write_fails", "expected"),
    [
        (ago(5), "ok", False, 200),
        (ago(45), "ok", False, 503),  # stale heartbeat
        (None, "", False, 503),  # the core never beat
        (ago(5), "fail", False, 503),  # full disk reported by the core
        (ago(5), "unavailable", False, 503),  # a probe could not run
        (ago(5), "ok", True, 503),  # the edge's own write failed
    ],
)
async def test_edge_check_route_answers(core_at, core_status, write_fails, expected):
    store = BeatStore(core_at, core_status, write_fails)
    code = await edge_check(store, FakeClock(NOW))  # type: ignore[arg-type]
    assert code == expected
    assert body_for(code) == ("ok" if expected == 200 else "")
    assert store.beats == ([] if write_fails else ["edge"])


async def test_edge_check_hanging_write_is_503():
    class Hang(BeatStore):
        @asynccontextmanager
        async def tx(self, *, household: str, lock_household: bool = False) -> AsyncIterator[BeatTx]:
            await asyncio.sleep(3600)
            yield BeatTx(self)

    store = Hang(ago(5), "ok")
    code = await asyncio.wait_for(
        edge_check(store, FakeClock(NOW), timeout_s=0.05),  # type: ignore[arg-type]
        timeout=2,
    )
    assert code == 503


async def test_edge_check_with_no_row_back_is_503():
    class Empty(BeatStore):
        pass

    store = Empty(None, "")

    async def no_rows(self, sql: str, *args: Any) -> list[Mapping[str, Any]]:
        return []

    BeatTx.fetch, original = no_rows, BeatTx.fetch  # type: ignore[method-assign]
    try:
        assert await edge_check(store, FakeClock(NOW)) == 503  # type: ignore[arg-type]
    finally:
        BeatTx.fetch = original  # type: ignore[method-assign]


# --- one run against a real database as the runtime roles (skips without SAATHI_TEST_DSN) ---


async def test_heartbeat_ok_then_503_on_a_real_database(pg_db: PgDb):
    clock = FakeClock(NOW)

    async def fine() -> ProbeResult:
        return ok("fine")

    async with open_store(pg_db, Role.CORE, clock=clock) as core, open_store(pg_db, Role.EDGE, clock=clock) as edge:
        assert await edge_check(edge, clock) == 503  # no core beat yet
        assert await beat_once(core, clock, [fine]) == "ok"
        assert await edge_check(edge, clock) == 200
        await clock.advance(31)
        assert await edge_check(edge, clock) == 503  # the core went quiet
        assert await beat_once(core, clock, []) == "unavailable"
        assert await edge_check(edge, clock) == 503  # fresh, but not ok
        assert await beat_once(core, clock, [fine]) == "ok"
        assert await edge_check(edge, clock) == 200
        # the app clock against the real Postgres now(): a test clock years behind refuses
        assert (await skew_probe(core, clock)).refuses
