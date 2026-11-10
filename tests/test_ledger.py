"""LT-27: the Postgres ledger (M28; RQ-48). Synthetic amounts only; the cap in each test is a test value."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest
from conftest import PgDb
from pydantic import BaseModel
from store_helpers import NOW, open_store, seed_household

from saathi.clock import FakeClock
from saathi.config import Settings
from saathi.domain import Grade, Message, ModelRequest
from saathi.errors import BudgetExceeded, NoAcceptedResult
from saathi.ids import HouseholdId, RunId
from saathi.llm.gateway import Gateway, LedgerPort
from saathi.llm.ledger import PgLedger, SpendAlert, month_of
from saathi.llm.roster import MediaClass, Receipt, Roster, RosterEntry, Tier, TierSpec
from saathi.store import Role

H = HouseholdId("h1")
RUN = RunId("run-ledger-1")
D = Decimal


class Out(BaseModel):
    answer: str
    confidence: float = 1.0


async def rows(db: PgDb, sql: str, *args: object) -> list[asyncpg.Record]:
    su = await asyncpg.connect(db.dsn())
    try:
        return await su.fetch(sql, *args)
    finally:
        await su.close()


async def states(db: PgDb) -> list[str]:
    return [r["state"] for r in await rows(db, "SELECT state FROM llm_reservations ORDER BY id")]


# ------------------------------------------------------------------ the port
def test_pg_ledger_fits_the_gateway_port():
    led: LedgerPort = PgLedger.__new__(PgLedger)
    assert led is not None
    assert month_of(datetime(2026, 12, 31, 23, 0, tzinfo=UTC), "UTC") == "2026-12"
    assert month_of(datetime(2026, 12, 31, 23, 0, tzinfo=UTC) + timedelta(hours=2), "UTC") == "2027-01"


async def test_unset_cap_disables(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        for cap in (None, D(0), D(-1)):
            with pytest.raises(BudgetExceeded):
                await led.reserve(RUN, "t4", D(1), cap)
    assert await states(pg_db) == []


async def test_reserve_settle_release_and_total(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        a = await led.reserve(RUN, "t4", D("10"), D("100"))
        b = await led.reserve(RUN, "t3", D("20"), D("100"))
        assert await led.month_total("2026-01") == D("30")
        await led.settle(a, D("4.5"))
        await led.release(b)
        assert await led.month_total("2026-01") == D("4.5")
        for call in (led.settle(a, D(1)), led.release(a), led.release(b), led.settle(999_999, D(1))):
            with pytest.raises(ValueError):  # a closed or foreign reservation is a caller bug
                await call
        with pytest.raises(ValueError):
            await led.settle(await led.reserve(RUN, "t4", D(1), D(100)), D(-1))
    assert await states(pg_db) == ["settled", "released", "reserved"]
    spend = await rows(pg_db, "SELECT household_id, tier, cost_inr, reservation_id FROM llm_spend")
    got = [(r["household_id"], r["tier"], r["cost_inr"], r["reservation_id"]) for r in spend]
    assert got == [("h1", "t4", D("4.5"), a)]


async def test_reservation_past_the_cap_is_refused_and_not_stored(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        await led.reserve(RUN, "t4", D("60"), D("100"))
        await led.reserve(RUN, "t4", D("40"), D("100"))  # exactly the cap is allowed
        with pytest.raises(BudgetExceeded):
            await led.reserve(RUN, "t4", D("0.0001"), D("100"))
    assert await states(pg_db) == ["reserved", "reserved"]


async def test_month_rolls_over(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    clock = FakeClock(NOW)
    async with open_store(pg_db, Role.MODEL, clock=clock) as store:
        led = PgLedger(store, H)
        await led.reserve(RUN, "t4", D("100"), D("100"))
        with pytest.raises(BudgetExceeded):
            await led.reserve(RUN, "t4", D(1), D("100"))
        await clock.advance(40 * 86400)
        await led.reserve(RUN, "t4", D("100"), D("100"))  # a new month starts at zero
    assert [r["month"] for r in await rows(pg_db, "SELECT month FROM llm_reservations ORDER BY id")] == [
        "2026-01", "2026-02",
    ]  # fmt: skip


async def test_households_do_not_share_a_cap(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_household(pg_db, "h2")
    async with open_store(pg_db, Role.MODEL) as store:
        one = PgLedger(store, H, scope="household")
        two = PgLedger(store, HouseholdId("h2"), scope="household")
        mine = await one.reserve(RUN, "t4", D("100"), D("100"))
        await two.reserve(RUN, "t4", D("100"), D("100"))
        with pytest.raises(ValueError):  # h2 cannot see or close the reservation of h1
            await two.release(mine)
        assert await one.month_total("2026-01") == D("100")


# ------------------------------------------------------------------ concurrency, restart
async def test_concurrent_reservations_stay_under_cap(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    cap = D("100")

    async def attempt(led: PgLedger) -> bool:
        try:
            await led.reserve(RUN, "t4", D("7"), cap)
            return True
        except BudgetExceeded:
            return False

    async with open_store(pg_db, Role.MODEL, max_size=8) as store:
        led = PgLedger(store, H)
        won = await asyncio.gather(*(attempt(led) for _ in range(40)))
        total = await led.month_total("2026-01")
    assert sum(won) == 14  # 14 * 7 = 98 fits; a 15th would reach 105
    assert total == D("98") <= cap
    assert await states(pg_db) == ["reserved"] * 14


async def test_restart_keeps_reservations(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as before:
        first = await PgLedger(before, H).reserve(RUN, "t4", D("90"), D("100"))
    async with open_store(pg_db, Role.MODEL) as after:  # a new pool and a new ledger object: the rows are the state
        led = PgLedger(after, H)
        assert await led.month_total("2026-01") == D("90")
        with pytest.raises(BudgetExceeded):
            await led.reserve(RUN, "t4", D("11"), D("100"))
        await led.settle(first, D("12"))
        assert await led.month_total("2026-01") == D("12")


# ------------------------------------------------------------------ the 80% flag
async def test_80_percent_flag(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    seen: list[SpendAlert] = []

    async def sink(alert: SpendAlert) -> None:
        seen.append(alert)

    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H, on_alert=sink)
        assert (await led.reserve_checked(RUN, "t4", D("79"), D("100"))).alert is None
        crossing = await led.reserve_checked(RUN, "t4", D("1"), D("100"))  # exactly 80
        assert crossing.alert == SpendAlert(H, "2026-01", D("80"), D("100"))
        assert (await led.reserve_checked(RUN, "t4", D("5"), D("100"))).alert is None  # only the crossing reports
        assert seen == []  # reserve_checked leaves the sink to the caller


async def test_80_percent_flag_reaches_the_sink_through_the_port(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    seen: list[SpendAlert] = []

    async def sink(alert: SpendAlert) -> None:
        seen.append(alert)

    async def broken(alert: SpendAlert) -> None:
        raise RuntimeError("notice failed")

    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H, on_alert=sink)
        await led.reserve(RUN, "t4", D("50"), D("100"))
        assert seen == []
        rid = await led.reserve(RUN, "t4", D("35"), D("100"))
        assert isinstance(rid, int) and [a.total_inr for a in seen] == [D("85")]
        stubborn = PgLedger(store, H, on_alert=broken)
        await stubborn.release(rid)  # 50; now cross again with a sink that fails
        again = await stubborn.reserve(RUN, "t4", D("35"), D("100"))
        assert isinstance(again, int)  # the reservation stands although the notice failed
    assert await states(pg_db) == ["reserved", "released", "reserved"]


# ------------------------------------------------------------------ through the gateway
def _roster(*grades: str, price: bool = True) -> Roster:
    entries = []
    for g in grades:
        tier, grade = {"G1": (Tier.T1, Grade.G1), "G4": (Tier.T4, Grade.G4)}[g]
        paid = tier is Tier.T4 and price
        spec = TierSpec(
            entry_id=f"e-{g}", tier=tier, grade=grade, model=f"example-{g.lower()}",
            base_url=f"http://{g.lower()}.invalid/v1",
            usd_per_m_input=D(1) if paid else None, usd_per_m_output=D(5) if paid else None,
        )  # fmt: skip
        rc = Receipt(
            licence="example", training="example", retention="example", classes=frozenset(MediaClass),
            read_on=datetime(2000, 1, 1).date(), read_by="owner",
        )  # fmt: skip
        entries.append(RosterEntry(spec=spec, wants=rc.classes, receipt=rc))
    return Roster.load(entries)


def _gateway(roster: Roster, handler, ledger: PgLedger | None, cap: Decimal | None) -> Gateway:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Gateway(roster, client=client, ledger=ledger, cap_inr=cap, usd_to_inr=D(100))


def _req(step: str) -> ModelRequest[Out]:
    return ModelRequest(step=step, messages=[Message(role="user", content="hello")], schema_=Out, run=RUN)


def _ok(cost: float | None = None) -> httpx.Response:
    usage: dict = {"prompt_tokens": 10, "completion_tokens": 5}
    if cost is not None:
        usage["cost"] = cost
    body = json.dumps({"answer": "a", "confidence": 1.0})
    return httpx.Response(200, json={"choices": [{"message": {"content": body}}], "usage": usage})


async def test_missing_price_disables(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    unpriced = _roster("G4", price=False)
    calls: list[httpx.Request] = []

    def net(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return _ok()

    async with open_store(pg_db, Role.MODEL) as store:
        with pytest.raises(BudgetExceeded):
            await _gateway(unpriced, net, PgLedger(store, H), D(100)).complete(_req("debunk_verdict"))
    assert calls == [] and await states(pg_db) == []


async def test_failed_call_releases(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        g = _gateway(_roster("G4"), lambda r: httpx.Response(503), led, D(100))
        with pytest.raises(NoAcceptedResult):
            await g.complete(_req("debunk_verdict"))
        assert await led.month_total("2026-01") == 0
    assert await states(pg_db) == ["released"]


async def test_paid_call_settles_to_the_actual_cost(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        g = _gateway(_roster("G4"), lambda r: _ok(cost=0.1), led, D(100))  # 0.1 USD at 100 = 10
        res = await g.complete(_req("debunk_verdict"))
        assert res.cost_inr == D(10) and await led.month_total("2026-01") == D(10)
    assert await states(pg_db) == ["settled"]


async def test_g0_paths_run_over_cap(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    calls: list[httpx.Request] = []

    def net(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return _ok()

    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        await led.reserve(RUN, "t4", D(100), D(100))  # the month is at the cap
        g = _gateway(_roster("G1", "G4"), net, led, D(100))
        res = await g.complete(_req("intent"))  # a free tier ignores the cap
        assert res.value is not None and len(calls) == 1
        with pytest.raises(BudgetExceeded):  # the paid floor is blocked
            await g.complete(_req("debunk_verdict"))
    assert len(calls) == 1


# ------------------------------------------------------------------ review fixes: rounding, finiteness
async def test_cap_attack_with_a_cap_finer_than_the_column(pg_db: PgDb):
    """F1: the column keeps 4 places and rounds half up; the check must use the stored figure, rounded up."""
    await seed_household(pg_db, "h1")
    cap = D("1.00005")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        await led.reserve(RUN, "t4", D("0.9999"), cap)
        with pytest.raises(BudgetExceeded):  # unrounded 0.99995 fits; stored 0.0002 would make 1.0001
            await led.reserve(RUN, "t4", D("0.00015"), cap)
        await led.reserve(RUN, "t4", D("0.00005"), cap)  # stored as 0.0001: exactly 1.0000, within the cap
        assert await led.month_total("2026-01") == D("1.0000")
    assert await states(pg_db) == ["reserved", "reserved"]


async def test_settle_rounds_the_actual_cost_up(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        await led.settle(await led.reserve(RUN, "t4", D("1"), D("100")), D("0.00011"))
        assert await led.month_total("2026-01") == D("0.0002")
    assert [r["cost_inr"] for r in await rows(pg_db, "SELECT cost_inr FROM llm_spend")] == [D("0.0002")]


@pytest.mark.parametrize("bad", [D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity")])
async def test_a_non_finite_cap_is_budget_exceeded(pg_db: PgDb, bad: Decimal):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        with pytest.raises(BudgetExceeded):
            await PgLedger(store, H).reserve(RUN, "t4", D(1), bad)
    assert await states(pg_db) == []


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("-Infinity")])
async def test_a_non_finite_amount_is_a_value_error(pg_db: PgDb, bad: Decimal):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.MODEL) as store:
        led = PgLedger(store, H)
        with pytest.raises(ValueError):
            await led.reserve(RUN, "t4", bad, D(100))
        held = await led.reserve(RUN, "t4", D(1), D(100))
        with pytest.raises(ValueError):
            await led.settle(held, bad)
    assert await states(pg_db) == ["reserved"]
    assert await rows(pg_db, "SELECT 1 FROM llm_spend") == []


# ------------------------------------------------------------------ ruling 4: scope and time zone
def test_settings_default_to_global_scope_and_india_time(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SAATHI_LEDGER_SCOPE", raising=False)
    monkeypatch.delenv("SAATHI_LEDGER_TZ", raising=False)
    s = Settings(_env_file=None)
    assert s.ledger_scope == "global"
    assert s.ledger_tz == "Asia/Kolkata"
    assert Settings(_env_file=None, ledger_scope="household").ledger_scope == "household"
    with pytest.raises(ValueError):
        Settings(_env_file=None, ledger_scope="nearby")
    with pytest.raises(ValueError):
        Settings(_env_file=None, ledger_tz="Nowhere/Land")


def test_env_example_lists_the_ledger_settings():
    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    assert "SAATHI_LEDGER_SCOPE=global" in text
    assert "SAATHI_LEDGER_TZ=Asia/Kolkata" in text


def test_month_follows_the_ledger_time_zone_near_midnight_ist():
    last_minute = datetime(2026, 1, 31, 18, 29, 59, tzinfo=UTC)  # 23:59:59 on 31 January in India
    first_minute = last_minute + timedelta(seconds=1)  # 00:00:00 on 1 February in India
    assert month_of(last_minute) == "2026-01"
    assert month_of(first_minute) == "2026-02"
    assert month_of(last_minute, "Asia/Kolkata") == "2026-01"
    assert month_of(first_minute, "UTC") == "2026-01"  # the same instant is still January in UTC
    assert month_of(datetime(2026, 12, 31, 20, 0, tzinfo=UTC)) == "2027-01"


async def test_month_boundary_is_midnight_ist(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    clock = FakeClock(datetime(2026, 1, 31, 18, 29, tzinfo=UTC))  # 23:59 IST
    async with open_store(pg_db, Role.MODEL, clock=clock) as store:
        led = PgLedger(store, H)
        await led.reserve(RUN, "t4", D("100"), D("100"))
        with pytest.raises(BudgetExceeded):
            await led.reserve(RUN, "t4", D(1), D("100"))
        await clock.advance(120)  # 00:01 IST on 1 February; still 31 January in UTC
        await led.reserve(RUN, "t4", D("100"), D("100"))
    assert [r["month"] for r in await rows(pg_db, "SELECT month FROM llm_reservations ORDER BY id")] == [
        "2026-01", "2026-02",
    ]  # fmt: skip


async def test_a_ledger_in_utc_keeps_the_utc_month(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    clock = FakeClock(datetime(2026, 1, 31, 18, 31, tzinfo=UTC))
    async with open_store(pg_db, Role.MODEL, clock=clock) as store:
        await PgLedger(store, H, tz="UTC").reserve(RUN, "t4", D(1), D(100))
    assert [r["month"] for r in await rows(pg_db, "SELECT month FROM llm_reservations")] == ["2026-01"]


def test_an_unknown_time_zone_or_scope_is_refused_at_construction():
    with pytest.raises(ValueError):
        PgLedger(None, H, tz="Nowhere/Land")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PgLedger(None, H, scope="nearby")  # type: ignore[arg-type]


async def test_global_scope_is_one_cap_across_households(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_household(pg_db, "h2")
    h2 = HouseholdId("h2")
    async with open_store(pg_db, Role.MODEL) as store:
        one, two = PgLedger(store, H), PgLedger(store, h2)  # the default scope is global
        await one.reserve(RUN, "t4", D("60"), D("100"))
        with pytest.raises(BudgetExceeded):  # 60 + 50 is over the one cap, though h2 has spent nothing
            await two.reserve(RUN, "t4", D("50"), D("100"))
        await two.reserve(RUN, "t4", D("40"), D("100"))
        with pytest.raises(BudgetExceeded):
            await one.reserve(RUN, "t4", D("0.0001"), D("100"))
        assert await one.month_total("2026-01") == D("100") == await two.month_total("2026-01")
    assert await states(pg_db) == ["reserved", "reserved"]


async def test_global_scope_settled_spend_counts_and_other_months_do_not(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_household(pg_db, "h2")
    clock = FakeClock(NOW)
    async with open_store(pg_db, Role.MODEL, clock=clock) as store:
        one, two = PgLedger(store, H), PgLedger(store, HouseholdId("h2"))
        await one.settle(await one.reserve(RUN, "t4", D("30"), D("100")), D("25"))
        assert await two.month_total("2026-01") == D("25")
        await two.reserve(RUN, "t4", D("75"), D("100"))
        await clock.advance(40 * 86400)
        await one.reserve(RUN, "t4", D("100"), D("100"))  # February starts at zero for everyone


async def test_global_scope_concurrent_reservers_of_two_households(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_household(pg_db, "h2")
    gate = asyncio.Barrier(80)  # every reserver is released together, so the two households really overlap

    async def attempt(led: PgLedger) -> bool:
        await gate.wait()
        try:
            await led.reserve(RUN, "t4", D("5"), D("100"))
            return True
        except BudgetExceeded:
            return False

    async with open_store(pg_db, Role.MODEL, max_size=16) as store:
        leds = [PgLedger(store, H), PgLedger(store, HouseholdId("h2"))]
        won = await asyncio.gather(*(attempt(leds[i % 2]) for i in range(80)))
        total = await leds[0].month_total("2026-01")
    assert sum(won) == 20 and total == D("100")


async def test_global_scope_takes_the_one_global_lock_for_every_household(pg_db: PgDb):
    """Holding the global key blocks a reserver of either household; the household key does not block global scope."""
    from saathi.llm.ledger import _GLOBAL_LOCK

    await seed_household(pg_db, "h1")
    await seed_household(pg_db, "h2")
    h2 = HouseholdId("h2")
    async with open_store(pg_db, Role.MODEL) as store:
        for key, blocked in ((_GLOBAL_LOCK, True), (str(h2), False)):
            holder = await asyncpg.connect(pg_db.dsn())
            try:
                async with holder.transaction():
                    await holder.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 2))", key)
                    task = asyncio.ensure_future(PgLedger(store, h2).reserve(RUN, "t4", D(1), D("100")))
                    done, _ = await asyncio.wait({task}, timeout=1.0)
                    assert (not done) is blocked
            finally:
                await holder.close()
            await task


async def test_household_scope_keeps_one_cap_each(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_household(pg_db, "h2")
    async with open_store(pg_db, Role.MODEL) as store:
        one = PgLedger(store, H, scope="household")
        two = PgLedger(store, HouseholdId("h2"), scope="household")
        await one.reserve(RUN, "t4", D("100"), D("100"))
        await two.reserve(RUN, "t4", D("100"), D("100"))
        with pytest.raises(BudgetExceeded):
            await one.reserve(RUN, "t4", D(1), D("100"))
        assert await one.month_total("2026-01") == D("100")


async def test_the_total_function_sums_all_households_and_restores_the_setting(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_household(pg_db, "h2")
    async with open_store(pg_db, Role.MODEL) as store:
        await PgLedger(store, H, scope="household").reserve(RUN, "t4", D("3"), D("100"))
        await PgLedger(store, HouseholdId("h2"), scope="household").reserve(RUN, "t4", D("4"), D("100"))
        async with store.tx(household=H) as tx:
            assert [r["household_id"] for r in await tx.fetch("SELECT household_id FROM llm_reservations")] == ["h1"]
            assert (await tx.fetch("SELECT control.ledger_month_total('2026-01') AS t"))[0]["t"] == D("7")
            assert (await tx.fetch("SELECT current_setting('app.household_id') AS h"))[0]["h"] == "h1"
