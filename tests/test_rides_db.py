"""The ride helper against Postgres: the migration, the worker card, the place-choice tap. Synthetic values only.

Postgres tests need SAATHI_TEST_DSN (and fail under CI=true without it). Seeding uses the superuser; the code under
test runs as the core role. The admin commands for places are tested in test_admin.py.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest
from alert_helpers import make_rig
from conftest import PgDb
from fakes import FakeAudit, FakeCaregiverChannel, FakeClock
from worker_helpers import NOW, Rig, Role, add_inbox, open_store, provision, rows

from saathi.channels.telegram import CallbackRouter, RoutedUpdate
from saathi.errors import ChannelDown
from saathi.ids import ApproverId, HouseholdId, IdemKey
from saathi.rides.taps import PgRideTaps
from saathi.worker import WorkerConfig, process, steps

H = "h1"
P = "ap1h1"


async def denied(conn: asyncpg.Connection, sql: str) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.execute(sql)


async def seed_places(db: PgDb, h: str = H, *, pickup: bool = True) -> None:
    """Home P (the pickup), Clinic A, Temple B (with an alias) and Relative C (no coordinates)."""
    su = await asyncpg.connect(db.dsn())
    try:
        for pid, label, formatted, lat, lng, aliases, is_pickup, count in (
            ("pl_home", "Home P", "1 Pickup Lane, Sample Town", 0.0, 0.0, [], pickup, 0),
            ("pl_clinic", "Clinic A", "2 Test Road, Sample Town", 0.1, 0.2, ["doctor"], False, 0),
            ("pl_temple", "Temple B", "3 Test Road, Sample Town", 0.3, 0.4, ["mandir"], False, 3),
            ("pl_relative", "Relative C", "4 Test Road, Sample Town", None, None, [], False, 0),
        ):
            await su.execute(
                "INSERT INTO addresses (id, household_id, label, formatted, lat, lng, aliases, is_pickup, ride_count)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                h + pid if h != H else pid, h, label, formatted, lat, lng, aliases, is_pickup, count,
            )  # fmt: skip
    finally:
        await su.close()


# --- the migration ----------------------------------------------------------------------------------------------
async def test_migration_adds_the_four_columns_with_defaults(pg_db: PgDb):
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await su.execute("INSERT INTO households (id, name) VALUES ('h1', 'H')")
        await su.execute("INSERT INTO addresses (id, household_id, label, formatted) VALUES ('a1', 'h1', 'x', 'y')")
        row = await su.fetchrow("SELECT aliases, is_pickup, ride_count, last_ride_at FROM addresses")
    finally:
        await su.close()
    assert (row["aliases"], row["is_pickup"], row["ride_count"], row["last_ride_at"]) == ([], False, 0, None)


async def test_one_pickup_per_household(pg_db: PgDb):
    su = await asyncpg.connect(pg_db.dsn())
    try:
        for h in ("h1", "h2"):
            await su.execute("INSERT INTO households (id, name) VALUES ($1, 'H')", h)
        ins = "INSERT INTO addresses (id, household_id, label, formatted, is_pickup) VALUES ($1, $2, 'x', 'y', $3)"
        await su.execute(ins, "a1", "h1", True)
        await su.execute(ins, "a2", "h1", False)
        await su.execute(ins, "a3", "h1", False)  # any number of non-pickup places
        await su.execute(ins, "a4", "h2", True)  # another household has its own pickup
        with pytest.raises(asyncpg.UniqueViolationError):
            await su.execute(ins, "a5", "h1", True)
    finally:
        await su.close()


async def test_core_reads_places_and_changes_only_the_two_counters(pg_db: PgDb):
    await provision(pg_db, "h1")
    await provision(pg_db, "h2")
    await seed_places(pg_db, "h1")
    await seed_places(pg_db, "h2")
    core = await asyncpg.connect(pg_db.dsn("saathi_core"))
    try:
        await core.execute("SELECT set_config('app.household_id', 'h1', false)")
        assert await core.fetchval("SELECT count(*) FROM addresses") == 4  # RLS: its own household only
        assert (
            await core.execute("UPDATE addresses SET ride_count = ride_count + 1, last_ride_at = now()") == "UPDATE 4"
        )
        await denied(core, "UPDATE addresses SET label = 'z'")
        await denied(core, "UPDATE addresses SET is_pickup = false")
        await denied(core, "UPDATE addresses SET aliases = '{z}'")
        await denied(core, "DELETE FROM addresses")
        # another household's row is invisible, so a counter update reaches nothing
        assert await core.execute("UPDATE addresses SET ride_count = 9 WHERE id = 'h2pl_clinic'") == "UPDATE 0"
    finally:
        await core.close()
    other = await rows(pg_db, "SELECT DISTINCT ride_count FROM addresses WHERE household_id = 'h2' ORDER BY 1")
    assert [r["ride_count"] for r in other] == [0, 3]  # untouched


async def test_owner_rls_denies_another_household(pg_db: PgDb):
    await provision(pg_db, "h1")
    await provision(pg_db, "h2")
    await seed_places(pg_db, "h1")
    await seed_places(pg_db, "h2")
    owner = await asyncpg.connect(pg_db.dsn("saathi_owner"))
    try:
        await owner.execute("SELECT set_config('app.household_id', 'h1', false)")
        assert await owner.execute("DELETE FROM addresses WHERE id = 'h2pl_clinic'") == "DELETE 0"
        assert await owner.execute("UPDATE addresses SET aliases = '{q}' WHERE id = 'h2pl_clinic'") == "UPDATE 0"
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await owner.execute("UPDATE addresses SET ride_count = 5")  # the owner never touches the counters
        with pytest.raises(asyncpg.PostgresError):  # inserting into another household breaks the policy
            await owner.execute(
                "INSERT INTO addresses (id, household_id, label, formatted) VALUES ('zz', 'h2', 'x', 'y')"
            )
    finally:
        await owner.close()
    assert (await rows(pg_db, "SELECT count(*) AS n FROM addresses WHERE household_id = 'h2'"))[0]["n"] == 4


# --- the worker -------------------------------------------------------------------------------------------------
@asynccontextmanager
async def harness(db: PgDb, rig: Rig, keys: Any, cfg: WorkerConfig | None = None):
    clock = FakeClock(NOW)
    async with open_store(db, Role.CORE, clock=clock) as store:
        yield store, rig.deps(store, keys, clock, cfg), clock


async def run_one(db: PgDb, rig: Rig, ring: Any, pub: Any, text: str, *, places: bool = True) -> Any:
    """Seed the places and one text row of household h1, lease it and process it with the fakes of `rig`."""
    if places:
        await seed_places(db)
    await add_inbox(db, ring, pub, H, "m1", text=text)
    async with harness(db, rig, ring) as (store, deps, clock):
        lease = await store.scan.lease_next(clock.now(), 60)
        assert lease is not None
        return await process(lease, deps)


async def ride_count(db: PgDb, pid: str) -> int:
    return (await rows(db, "SELECT ride_count FROM addresses WHERE id = $1", pid))[0]["ride_count"]


async def test_ride_card_follows_the_summary_and_the_reply_is_unchanged(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    out = await run_one(pg_db, rig, ring, pub, "mujhe cab chahiye, Clinic A jaana hai")
    assert (out.inbox_state, out.emergency, out.scam) == ("processed", False, False)
    sent = [(m, to, k) for m, to, _p, k in rig.caregiver.sent]
    assert sent == [
        ("send_card", P, IdemKey("m1:summary:ap1h1")),
        ("send_card", P, IdemKey("m1:ride:ap1h1")),
    ]
    summary, ride = (c for *_x, c, _k in rig.caregiver.sent)
    assert summary.title == "New message from the elder"  # type: ignore[attr-defined]
    assert ride.title == "Ride request"  # type: ignore[attr-defined]
    assert ride.lines == ["To: Clinic A", "Pickup: 1 Pickup Lane, Sample Town", "Drop: 2 Test Road, Sample Town"]  # type: ignore[attr-defined]
    assert [b.text for b in ride.buttons] == ["Uber (pre-filled)", "Rapido", "Namma Yatri", "Map"]  # type: ignore[attr-defined]
    assert [(m, k) for m, _to, _p, k in rig.elder.sent] == [("send_text", IdemKey("m1:reply:text"))]
    assert await ride_count(pg_db, "pl_clinic") == 1 and await ride_count(pg_db, "pl_temple") == 3
    stamped = await rows(pg_db, "SELECT last_ride_at FROM addresses WHERE id = 'pl_clinic'")
    assert stamped[0]["last_ride_at"] == NOW
    assert "cab" not in json.dumps([c.model_dump() for c in (summary, ride)])  # no transcript words on a card


async def test_a_ride_to_every_approver_is_counted_once(pg_db: PgDb):
    ring, pub = await provision(pg_db, approvers=("ap1", "ap2"))
    rig = Rig()
    await run_one(pg_db, rig, ring, pub, "mandir jaana hai")
    kinds = [(to, k) for _m, to, _p, k in rig.caregiver.sent]
    assert [k for _to, k in kinds if ":ride:" in k] == [IdemKey("m1:ride:ap1h1"), IdemKey("m1:ride:ap2h1")]
    assert await ride_count(pg_db, "pl_temple") == 4  # 3 before, one ride, not one per caregiver


async def test_ride_word_with_no_known_place_sends_the_choice_card(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await run_one(pg_db, rig, ring, pub, "auto chahiye")
    *_summary, (_m, _to, ride, _k) = rig.caregiver.sent
    assert ride.title == "Ride request: place not recognised"  # type: ignore[attr-defined]
    assert [b.data for b in ride.buttons] == ["ride:pl_temple", "ride:pl_clinic", "ride:pl_relative"]  # type: ignore[attr-defined]
    assert [await ride_count(pg_db, p) for p in ("pl_clinic", "pl_temple", "pl_relative")] == [0, 3, 0]


async def test_no_ride_word_means_no_ride_card(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await run_one(pg_db, rig, ring, pub, "Clinic A ka number do")
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"]


async def test_a_household_with_no_places_gets_no_ride_card(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await run_one(pg_db, rig, ring, pub, "cab chahiye, Clinic A", places=False)
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"]


@pytest.mark.parametrize(
    ("text", "emergency", "scam"),
    [("mujhe seene mein dard ho raha hai, cab chahiye, Clinic A", True, False),
     ("kisi ne otp bhejne ko kaha hai, cab chahiye, Clinic A", False, True)],
)  # fmt: skip
async def test_an_emergency_or_a_scam_never_gets_a_ride_card(pg_db: PgDb, text: str, emergency: bool, scam: bool):
    ring, pub = await provision(pg_db)
    rig = Rig()
    out = await run_one(pg_db, rig, ring, pub, text)
    assert (out.emergency, out.scam) == (emergency, scam)
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"]  # the summary only
    assert await ride_count(pg_db, "pl_clinic") == 0


async def test_a_recogniser_failure_still_sends_the_summary_and_the_reply(pg_db: PgDb, monkeypatch, caplog):
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(steps, "recognise", boom)
    ring, pub = await provision(pg_db)
    rig = Rig()
    out = await run_one(pg_db, rig, ring, pub, "cab chahiye, Clinic A")
    assert out.inbox_state == "processed"
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"]
    assert [m for m, *_ in rig.elder.sent] == ["send_text"]
    assert "ride step failed" in caplog.text and await ride_count(pg_db, "pl_clinic") == 0


async def test_a_failed_ride_card_send_is_logged_and_blocks_nothing(pg_db: PgDb, caplog):
    ring, pub = await provision(pg_db)
    rig = Rig()
    real = rig.caregiver.send_card
    state = {"n": 0}

    async def flaky(to: ApproverId, card: Any, *, key: IdemKey) -> Any:
        state["n"] += 1
        if state["n"] == 2:  # the second send of the row is the ride card
            raise ChannelDown()
        return await real(to, card, key=key)

    rig.caregiver.send_card = flaky  # type: ignore[method-assign]
    out = await run_one(pg_db, rig, ring, pub, "cab chahiye, Clinic A")
    assert out.inbox_state == "processed"
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"]  # the summary
    assert [m for m, *_ in rig.elder.sent] == ["send_text"]  # the reply
    assert "ride card failed" in caplog.text
    assert await ride_count(pg_db, "pl_clinic") == 0  # nothing reached a caregiver, so nothing is counted


# --- the tap on a place choice ----------------------------------------------------------------------------------
async def test_a_tap_edits_the_choice_card_into_the_known_place_card_and_counts(pg_db: PgDb):
    rig = await make_rig(pg_db)
    await seed_places(pg_db)
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        out = FakeCaregiverChannel()
        router = CallbackRouter(
            store, rig.service(store), rig.ring, FakeAudit(), out,
            rides=PgRideTaps(store), edit=out.edit_card,
        )  # fmt: skip
        from saathi.rides.card import choice_card
        from saathi.rides.repo import PlaceRepo

        async with store.tx(household=HouseholdId(H)) as tx:
            card = choice_card(await PlaceRepo.all(tx))
        ref = await out.send_card(ApproverId(P), card, key=IdemKey("k1"))

        def tap(update_id: int, data: str) -> RoutedUpdate:
            return RoutedUpdate(
                update_id=update_id, approver_id=ApproverId(P), kind="callback", data=data,
                household_id=HouseholdId(H), message_ref=ref.message_ref,
            )  # fmt: skip

        await router.handle(tap(1, "ride:pl_clinic"))
        edited = out.cards[ref]
        assert edited.title == "Ride request" and edited.lines[0] == "To: Clinic A"
        assert [b.text for b in edited.buttons] == ["Uber (pre-filled)", "Rapido", "Namma Yatri", "Map"]
        assert await ride_count(pg_db, "pl_clinic") == 1
        await router.handle(tap(1, "ride:pl_clinic"))  # a repeated update id changes nothing
        assert await ride_count(pg_db, "pl_clinic") == 1
        await router.handle(tap(2, "ride:pl_nowhere"))  # a place that is gone: the card is left as it is
        assert out.cards[ref] == edited
        await router.handle(tap(3, "ride:pl_home"))  # the pickup is not a drop
        assert out.cards[ref] == edited and await ride_count(pg_db, "pl_home") == 0


async def test_a_tap_from_a_chat_that_is_not_an_approver_is_refused(pg_db: PgDb):
    rig = await make_rig(pg_db)
    await seed_places(pg_db)
    async with open_store(pg_db, Role.CORE, clock=rig.clock) as store:
        out = FakeCaregiverChannel()
        router = CallbackRouter(
            store, rig.service(store), rig.ring, FakeAudit(), out, rides=PgRideTaps(store), edit=out.edit_card
        )
        stranger = RoutedUpdate(
            update_id=7, approver_id=ApproverId("stranger"), kind="callback", data="ride:pl_clinic",
            household_id=HouseholdId(H), message_ref="card-1",
        )  # fmt: skip
        await router.handle(stranger)
    assert await ride_count(pg_db, "pl_clinic") == 0 and out.cards == {}
