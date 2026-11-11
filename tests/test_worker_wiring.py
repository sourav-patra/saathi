"""The real worker composition (M12; RQ-105, PL-14, RQ-102): settings to `WorkerDeps`, the person directory, and the
household scope the worker sets. Synthetic values only; the database tests need `SAATHI_TEST_DSN`."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest
from conftest import PgDb
from fakes import FakeClock
from store_helpers import open_store
from worker_helpers import NOW, Rig, add_inbox, provision

from saathi.channels.telegram_elder import TelegramElderChannel
from saathi.channels.whatsapp import WhatsAppElderChannel
from saathi.channels.worker_main import build_elder_channel, build_owner_alert, build_worker
from saathi.clock import SystemClock
from saathi.config import ConfigError, Settings
from saathi.domain import Card
from saathi.errors import ChannelDown
from saathi.ids import ApproverId, HouseholdId, IdemKey, PersonRef
from saathi.perception.asr import LocalAsrClient
from saathi.ports import ChannelHealth, ElderChannel
from saathi.store import Role
from saathi.store.keys import KeyRing
from saathi.store.repos._util import aad
from saathi.voice.codec import OpusCodec
from saathi.worker import Worker, WorkerConfig
from saathi.worker.scope import ADDRESS_SCOPE, acting_for
from saathi.worker.wiring import CoreDirectory, PendingCaregiverChannel

H = "h1"
NUMBER = "+910000000001"


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_elder_channel_follows_the_credentials(tmp_path: Path):
    people = object()
    token = tmp_path / "wa"
    token.write_text("synthetic-token\n")
    wa = build_elder_channel(
        settings(wa_phone_number_id="1", wa_access_token_file=str(token)), people, SystemClock()  # type: ignore[arg-type]
    )
    assert isinstance(wa, WhatsAppElderChannel) and wa.kind == "whatsapp"
    tg_token = tmp_path / "tg"
    tg_token.write_text("000000:synthetic\n")
    tg = build_elder_channel(settings(tg_elder_token_file=str(tg_token)), people, SystemClock())  # type: ignore[arg-type]
    assert isinstance(tg, TelegramElderChannel) and isinstance(tg, ElderChannel)
    with pytest.raises(ConfigError):
        build_elder_channel(settings(), people, SystemClock())  # type: ignore[arg-type]


def test_platform_calls_go_through_the_proxy_when_it_is_set(tmp_path: Path):
    token = tmp_path / "wa"
    token.write_text("synthetic-token")
    ch = build_elder_channel(
        settings(wa_phone_number_id="1", wa_access_token_file=str(token), egress_url="http://egress:8081/"),
        object(), SystemClock(),  # type: ignore[arg-type]
    )  # fmt: skip
    assert ch._client.base == "http://egress:8081/whatsapp/v23.0"  # type: ignore[attr-defined]
    direct = build_elder_channel(
        settings(wa_phone_number_id="1", wa_access_token_file=str(token)), object(), SystemClock()  # type: ignore[arg-type]
    )
    assert direct._client.base == "https://graph.facebook.com/v23.0"  # type: ignore[attr-defined]


async def test_owner_alert_is_logged_when_there_is_no_ops_chat(caplog: pytest.LogCaptureFixture):
    alert = build_owner_alert(settings())
    with caplog.at_level("WARNING"):
        await alert("inbox_dead")
    assert "owner alert: inbox_dead" in caplog.text


async def test_the_caregiver_stand_in_fails_loudly_and_sends_nothing():
    ch = PendingCaregiverChannel()
    with pytest.raises(ChannelDown):
        await ch.send_text(ApproverId("a1"), "x", key=IdemKey("k"))
    with pytest.raises(ChannelDown):
        await ch.send_card(ApproverId("a1"), Card(title="t", lines=[], buttons=[]), key=IdemKey("k"))  # type: ignore[call-arg]
    assert await ch.health() is ChannelHealth.DOWN


async def test_build_worker_composes_the_concrete_adapters(pg_db: PgDb, tmp_path: Path):
    kek, token = tmp_path / "kek", tmp_path / "wa_access_token"
    kek.write_bytes(b"k" * 32)
    token.write_text("synthetic-token\n")
    s = settings(
        kek_file=str(kek), wa_phone_number_id="1", wa_access_token_file=str(token),
        db_url_core=pg_db.dsn("saathi_core"), media_dir=str(tmp_path / "media"),
        whispercpp_url="http://127.0.0.1:1", egress_url="http://egress:8081",
    )  # fmt: skip
    deps = await build_worker(s)
    try:
        assert isinstance(deps.asr, LocalAsrClient) and isinstance(deps.codec, OpusCodec)
        assert deps.store.role is Role.CORE
        assert deps.elder.kind == "whatsapp" and isinstance(deps.elder._inner, WhatsAppElderChannel)  # type: ignore[attr-defined]
        assert isinstance(deps.caregiver._inner, PendingCaregiverChannel)  # type: ignore[attr-defined]
        assert deps.elder._inner._client.base == "http://egress:8081/whatsapp/v23.0"  # type: ignore[attr-defined]
        assert "synthetic-token" not in repr(deps)
    finally:
        await deps.store.close()


async def test_build_worker_names_what_is_missing(tmp_path: Path):
    with pytest.raises(ConfigError, match="kek_file"):
        await build_worker(settings())
    kek = tmp_path / "kek"
    kek.write_bytes(b"k" * 32)
    with pytest.raises(ConfigError, match="elder channel"):  # a key but no channel: nothing is opened first
        await build_worker(settings(kek_file=str(kek), db_url_core="postgresql://x@localhost:1/none"))


async def _seal_address(db: PgDb, ring: KeyRing, pub: bytes) -> None:
    sealed = ring.seal(pub, NUMBER.encode(), aad=aad(H, "i" + H, "ref"))  # type: ignore[arg-type]
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute("UPDATE elder_identities SET person_ref_enc = $1 WHERE id = $2", sealed, "i" + H)
    finally:
        await su.close()


async def test_core_directory_opens_the_sealed_address_in_scope_only(pg_db: PgDb):
    ring, pub = await provision(pg_db, H)
    await provision(pg_db, "h2", keys=ring)
    await _seal_address(pg_db, ring, bytes(pub))
    async with open_store(pg_db, Role.CORE) as store:
        directory = CoreDirectory(store, KeyRing(kek=ring._kek))  # type: ignore[attr-defined]
        with acting_for(HouseholdId(H)):
            assert await directory.address(PersonRef("ref-h1"), "whatsapp") == NUMBER
        with pytest.raises(LookupError):  # no household in scope: nothing opens
            await directory.address(PersonRef("ref-h1"), "whatsapp")
        with acting_for(HouseholdId("h2")), pytest.raises(LookupError):  # another household cannot see it
            await directory.address(PersonRef("ref-h1"), "whatsapp")
    assert ADDRESS_SCOPE.get() is None  # the scope was reset on exit


async def test_the_worker_sets_the_household_scope_for_every_send(pg_db: PgDb):
    from fakes import FakeElderChannel

    seen: list[HouseholdId | None] = []

    class ScopedElder(FakeElderChannel):
        def _send(self, method, to, payload, key, *, free_form):  # type: ignore[no-untyped-def]
            seen.append(ADDRESS_SCOPE.get())
            return super()._send(method, to, payload, key, free_form=free_form)

    ring, pub = await provision(pg_db, H)
    rig = Rig(elder=ScopedElder())
    rig.voice.add("ack", b"synthetic-clip", "hi")
    rig.voice.add("told_caregiver", b"synthetic-clip", "hi")
    await add_inbox(pg_db, ring, pub, H, "m1", text="aaj mausam kaisa hai")
    clock = FakeClock(NOW)
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        worker = Worker(store, rig.deps(store, ring, clock, WorkerConfig()), clock)
        while await worker.run_once():
            pass
    assert seen and set(seen) == {H}
    assert ADDRESS_SCOPE.get() is None


async def test_two_concurrent_tasks_each_read_only_their_own_household():
    import asyncio

    seen: dict[str, list[object]] = {"a": [], "b": []}
    gate = asyncio.Event()

    async def work(name: str, household: str) -> None:
        with acting_for(HouseholdId(household)):
            seen[name].append(ADDRESS_SCOPE.get())
            if name == "a":
                await gate.wait()  # b runs, sets its own household and finishes while a is still inside its scope
            else:
                gate.set()
            await asyncio.sleep(0)
            seen[name].append(ADDRESS_SCOPE.get())

    await asyncio.gather(work("a", "h-a"), work("b", "h-b"))
    assert seen == {"a": ["h-a", "h-a"], "b": ["h-b", "h-b"]}
    assert ADDRESS_SCOPE.get() is None  # nothing leaked to the caller
