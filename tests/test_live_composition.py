"""WU-26 live composition (WU-18 ruling 1, RQ-48, WU-19 carry): the caregiver bot and the worker entry points compose
the approver directory, the alert service, the key opener, the audit sink and the trace exporter; `require_secrets`
runs first; the ledger's 80% and cap notices are wired. Fakes stand in for every platform; all values are synthetic."""

from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alert_helpers import B, H, P, make_rig
from conftest import PgDb
from fakes import FakeCaregiverChannel, FakeClock
from store_helpers import NOW, open_store

from saathi.channels import worker_main as wm
from saathi.channels.telegram import CallbackRouter, TelegramCaregiverChannel
from saathi.config import ConfigError, Settings
from saathi.errors import BudgetExceeded
from saathi.ids import ApproverId, EventId, HouseholdId, RunId
from saathi.llm.ledger import CAP_SUMMARY, PgLedger, SpendAlert, SpendNotices, store_approvers
from saathi.store import Role
from saathi.trace import JsonlExporter
from saathi.worker.scope import ADDRESS_SCOPE, acting_for

SECRET = "s" * 40  # a synthetic approval secret of the required length
BOT_TOKEN = "123:synthetic-bot"


def settings(tmp_path: Path, **kw: object) -> Settings:
    kek, bot, elder = tmp_path / "kek", tmp_path / "tg_caregiver_token", tmp_path / "tg_elder_token"
    kek.write_bytes(b"k" * 32)
    bot.write_text(BOT_TOKEN + "\n")
    elder.write_text("1:synthetic-elder\n")
    base: dict[str, Any] = {
        "kek_file": str(kek), "tg_caregiver_token_file": str(bot), "tg_elder_token_file": str(elder),
        "approval_secret": SECRET, "media_dir": str(tmp_path / "media"), "trace_dir": str(tmp_path / "traces"),
    }  # fmt: skip
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


def test_bot_client_goes_through_the_proxy(tmp_path: Path):
    bot = wm.build_bot_client(settings(tmp_path, egress_url="http://egress:8081/"))
    assert bot.session.api.base.startswith("http://egress:8081/telegram/bot")
    assert BOT_TOKEN not in repr(bot)


def test_bot_client_needs_the_caregiver_token(tmp_path: Path):
    with pytest.raises(ConfigError, match="caregiver bot token"):
        wm.build_bot_client(settings(tmp_path, tg_caregiver_token_file=""))


async def test_scoped_households_follow_the_step_scope():
    assert await wm._scoped_households() == []
    with acting_for(HouseholdId("h9")):
        assert await wm._scoped_households() == ["h9"]


async def test_alert_service_steps_inside_the_household_scope(monkeypatch: pytest.MonkeyPatch):
    seen: list[object] = []

    async def fake_step(self: object, household: HouseholdId, event: EventId) -> None:
        seen.append(ADDRESS_SCOPE.get())

    async def fake_ack(self: object, household: HouseholdId, *a: object) -> bool:
        seen.append(ADDRESS_SCOPE.get())
        return True

    monkeypatch.setattr(wm.AlertService, "step", fake_step)
    monkeypatch.setattr(wm.AlertService, "acknowledge", fake_ack)
    monkeypatch.setattr(wm.AlertService, "false_alarm", fake_ack)
    svc = object.__new__(wm.ScopedAlertService)
    await svc.step(HouseholdId("h1"), EventId("e1"))
    await svc.acknowledge(HouseholdId("h2"), EventId("e1"), ApproverId("a"), "tap")
    await svc.false_alarm(HouseholdId("h3"), EventId("e1"), ApproverId("a"))
    assert seen == ["h1", "h2", "h3"] and ADDRESS_SCOPE.get() is None


async def test_serve_bot_checks_the_secrets_before_composing(tmp_path: Path):
    built: list[str] = []

    async def build(_s: Settings) -> wm.BotParts:
        built.append("x")
        raise AssertionError("must not be reached")

    with pytest.raises(ConfigError):
        await wm.serve_bot(settings(tmp_path, approval_secret="short"), build)
    assert built == []


async def test_serve_bot_polls_beside_the_alert_loop_and_closes_the_store(tmp_path: Path, monkeypatch):
    events: list[str] = []
    closed = asyncio.Event()

    class Alerts:
        async def run_forever(self) -> None:
            events.append("alerts")
            await asyncio.Event().wait()

    class StoreStub:
        @contextlib.asynccontextmanager
        async def singleton(self, key: int):
            assert key == wm.BOT_LOCK_KEY
            yield True

        async def close(self) -> None:
            closed.set()

    async def fake_serve(s: Settings, build: Any) -> None:
        channel, router = build(s)
        events.append(f"poll:{channel}:{router}")
        await asyncio.sleep(0)

    async def build(_s: Settings) -> wm.BotParts:
        return wm.BotParts(StoreStub(), "ch", "rt", Alerts())  # type: ignore[arg-type]

    monkeypatch.setattr(wm.caregiver_bot, "serve", fake_serve)
    await wm.serve_bot(settings(tmp_path), build)
    assert "poll:ch:rt" in events and "alerts" in events and closed.is_set()


async def test_serve_all_refuses_a_short_secret_before_anything_starts(tmp_path: Path):
    with pytest.raises(ConfigError):
        await wm.serve_all(settings(tmp_path, approval_secret=""))


async def test_spend_notices_tell_the_owner_at_80_and_each_approver_once_at_the_cap():
    out = FakeCaregiverChannel()
    told: list[str] = []

    async def approvers(_h: HouseholdId) -> list[ApproverId]:
        return [ApproverId("a1"), ApproverId("a2")]

    async def owner(what: str) -> None:
        told.append(what)

    notices = SpendNotices(out, approvers, owner)
    await notices.on_alert(SpendAlert(HouseholdId("h1"), "2026-11", Decimal("777"), Decimal("971")))
    await notices.on_cap(HouseholdId("h1"), "2026-11")
    await notices.on_cap(HouseholdId("h1"), "2026-11")  # once a month, not at every refused reservation
    assert [(m, to, text) for m, to, text, _k in out.sent] == [
        ("send_text", "a1", CAP_SUMMARY), ("send_text", "a2", CAP_SUMMARY),
    ]  # fmt: skip
    assert told == ["spend_80pct_2026-11", "spend_cap_2026-11"]
    assert all(c not in " ".join(told) + CAP_SUMMARY for c in ("777", "971"))  # no figure of the cap leaves the ledger


async def test_ledger_sends_the_cap_summary_and_still_refuses(monkeypatch: pytest.MonkeyPatch):
    got: list[tuple[str, str]] = []

    async def on_cap(h: HouseholdId, month: str) -> None:
        got.append((h, month))
        raise RuntimeError("a failing notice")

    ledger = PgLedger(SimpleNamespace(), HouseholdId("h1"), on_cap=on_cap)  # type: ignore[arg-type]

    async def refuse(*_a: object) -> None:
        ledger._cap_month = "2026-11"
        raise BudgetExceeded("paid cap reached")

    monkeypatch.setattr(ledger, "reserve_checked", refuse)
    with pytest.raises(BudgetExceeded):
        await ledger.reserve(RunId("r1"), "paid", Decimal("1"), Decimal("10"))
    assert got == [("h1", "2026-11")]


async def test_unset_cap_sends_no_cap_summary():
    got: list[object] = []

    async def on_cap(*a: object) -> None:
        got.append(a)

    ledger = PgLedger(SimpleNamespace(), HouseholdId("h1"), on_cap=on_cap)  # type: ignore[arg-type]
    with pytest.raises(BudgetExceeded):
        await ledger.reserve(RunId("r1"), "paid", Decimal("1"), None)
    assert got == []


def test_build_ledger_wires_both_notices_and_the_settings(tmp_path: Path):
    s = settings(tmp_path, ledger_scope="household", ledger_tz="UTC")
    ledger = wm.build_ledger(s, SimpleNamespace(), HouseholdId("h1"), FakeCaregiverChannel())  # type: ignore[arg-type]
    assert ledger._scope == "household" and ledger._tz == "UTC"
    assert ledger._on_alert is not None and ledger._on_cap is not None


async def test_build_bot_composes_the_live_parts(pg_db: PgDb, tmp_path: Path):
    s = settings(tmp_path, db_url_core=pg_db.dsn("saathi_core"))
    parts = await wm.build_bot(s)
    try:
        assert isinstance(parts.channel, TelegramCaregiverChannel)
        assert isinstance(parts.router, CallbackRouter) and isinstance(parts.alerts, wm.ScopedAlertService)
        assert parts.store.role is Role.CORE
        assert isinstance(parts.router._trace, JsonlExporter) and isinstance(parts.alerts._trace, JsonlExporter)
        assert parts.router._keys is not None and parts.router._audit is not None
        assert BOT_TOKEN not in repr(parts)
    finally:
        await parts.store.close()


async def test_build_bot_names_what_is_missing(tmp_path: Path):
    with pytest.raises(ConfigError, match="kek_file"):
        await wm.build_bot(settings(tmp_path, kek_file=""))
    with pytest.raises(ConfigError, match="caregiver bot token"):
        await wm.build_bot(settings(tmp_path, tg_caregiver_token_file="", db_url_core="postgresql://x@localhost:1/n"))


async def test_directory_reads_a_chat_only_in_scope_and_approvers_in_order(pg_db: PgDb, tmp_path: Path):
    await make_rig(pg_db)
    async with open_store(pg_db, Role.CORE, clock=FakeClock(NOW)) as store:
        directory = wm.PgApproverDirectory(store.scan, store, wm._scoped_households)
        with pytest.raises(KeyError):
            await directory.chat_of(ApproverId(P))  # no household in scope: nothing is read
        with acting_for(HouseholdId(H)):
            assert await directory.chat_of(ApproverId(B)) == 3002
        assert set(await store_approvers(store)(HouseholdId(H))) >= {P, B}


async def test_serve_bot_refuses_to_start_when_another_poller_holds_the_lock(tmp_path: Path, monkeypatch):
    closed: list[str] = []

    class HeldStore:
        @contextlib.asynccontextmanager
        async def singleton(self, key: int):
            yield False

        async def close(self) -> None:
            closed.append("closed")

    async def must_not_poll(s: Settings, build: Any) -> None:
        raise AssertionError("a second poller must not start")

    class Alerts:
        async def run_forever(self) -> None:
            raise AssertionError("no alert loop without the lock")

    async def build(_s: Settings) -> wm.BotParts:
        return wm.BotParts(HeldStore(), "ch", "rt", Alerts())  # type: ignore[arg-type]

    monkeypatch.setattr(wm.caregiver_bot, "serve", must_not_poll)
    with pytest.raises(wm.BotAlreadyRunning):
        await wm.serve_bot(settings(tmp_path), build)
    assert closed == ["closed"]


async def test_bot_advisory_lock_admits_one_poller(pg_db: PgDb):
    async with open_store(pg_db, Role.CORE) as a, open_store(pg_db, Role.CORE) as b:
        async with a.singleton(wm.BOT_LOCK_KEY) as first:
            assert first is True
            async with b.singleton(wm.BOT_LOCK_KEY) as second:
                assert second is False
        async with b.singleton(wm.BOT_LOCK_KEY) as after:
            assert after is True  # released when the first poller stops


async def test_a_bot_crash_loop_does_not_stop_the_elder_worker():
    bot_starts: list[int] = []
    worker_ticks: list[int] = []
    done = asyncio.Event()

    async def bot() -> None:
        bot_starts.append(1)
        raise ConfigError("synthetic bad caregiver token")

    async def worker() -> None:
        for i in range(5):
            worker_ticks.append(i)
            await asyncio.sleep(0.01)
        done.set()

    await asyncio.wait_for(wm.supervise(worker, bot, restart_after=0.001), timeout=5)
    assert done.is_set() and worker_ticks == [0, 1, 2, 3, 4] and len(bot_starts) >= 2


async def test_supervise_cancels_the_bot_when_the_worker_fails():
    cancelled = asyncio.Event()

    async def bot() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def worker() -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("synthetic worker failure")

    with pytest.raises(RuntimeError):
        await wm.supervise(worker, bot)
    assert cancelled.is_set()
