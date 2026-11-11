"""Repos on a real database as the runtime roles: inbox, alerts, updates, flags, identities (M04; RQ-79, 18, 97)."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest
from conftest import PgDb
from fakes import FakeSealer
from store_helpers import NOW, open_store, seed_household, seed_inbox

from saathi.domain import AlertOpen, InboundMessage, Lease, MediaKind, NewInbox, RawPart, Source
from saathi.errors import FlagUnreadable, KeyMissing
from saathi.ids import ApproverId, ElderId, EventId, HouseholdId, InboxId, PersonRef, PublicKey
from saathi.store import Role
from saathi.store.repos import AlertRepo, FlagRepo, IdentityRepo, InboxRepo, UpdateRepo

H = HouseholdId("h1")
PUB = PublicKey(b"pub-h1")
LATER = NOW + timedelta(days=3650)


def new_inbox(inbox_id: str = "m1", *, text: str = "hello") -> NewInbox:
    msg = InboundMessage(
        source=Source.WHATSAPP, provider_message_id="wa:" + inbox_id, sender_ref="sender", kind=MediaKind.TEXT,
        text=text, received_at=NOW,
    )  # fmt: skip
    return NewInbox(
        id=InboxId(inbox_id), household_id=H, elder_id=ElderId("eh1"), msg=msg, sender_hmac="s",
        payload_sha256="x", parts=[RawPart(part="text", text=text), RawPart(part="caption", text="cap")],
    )  # fmt: skip


async def raw_rows(db: PgDb) -> list[asyncpg.Record]:
    su = await asyncpg.connect(db.dsn())
    try:
        return await su.fetch("SELECT inbox_id, part, body_sealed, key_id FROM raw_text ORDER BY part")
    finally:
        await su.close()


async def one(db: PgDb, sql: str, *args: object):
    su = await asyncpg.connect(db.dsn())
    try:
        return await su.fetchrow(sql, *args)
    finally:
        await su.close()


async def test_insert_if_new_as_edge_seals_parts_once(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.EDGE) as edge:
        for expected in (True, False):  # the second delivery of the same message
            async with edge.tx(household=H) as tx:
                got = await InboxRepo.insert_if_new(
                    tx, new_inbox(), public_key=PUB, sealer=FakeSealer(), expires_at=LATER
                )
            assert got is expected
    rows = await raw_rows(pg_db)
    assert [(r["part"], r["key_id"]) for r in rows] == [("caption", "k1"), ("text", "k1")]
    assert bytes(rows[1]["body_sealed"]) == b"sealed:h1|m1|text:hello"  # bound to household, inbox and part
    row = await one(pg_db, "SELECT state, attempts, kind, sender_hmac FROM inbox WHERE id = 'm1'")
    assert tuple(row) == ("received", 0, "text", "s")


async def test_insert_if_new_with_an_unknown_public_key_is_refused_whole(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.EDGE) as edge:
        with pytest.raises(KeyMissing):
            async with edge.tx(household=H) as tx:
                await InboxRepo.insert_if_new(
                    tx, new_inbox(), public_key=PublicKey(b"other"), sealer=FakeSealer(), expires_at=LATER
                )
    assert await one(pg_db, "SELECT 1 FROM inbox") is None  # the transaction rolled back, no half-written row


async def test_lease_settle_and_stale_tokens(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_inbox(pg_db, "h1", "m1")
    async with open_store(pg_db, Role.CORE) as core:
        lease = await core.scan.lease_next(LATER)
        assert lease is not None
        stale = Lease(household_id=H, inbox_id=lease.inbox_id, elder_id=lease.elder_id, token=uuid4())
        async with core.tx(household=H) as tx:
            assert not await InboxRepo.renew(tx, stale, LATER)
            assert not await InboxRepo.settle(tx, stale, "processed")
            assert not await InboxRepo.add_transcript(tx, stale, "words", FakeSealer(), PUB)
            assert await InboxRepo.renew(tx, lease, LATER + timedelta(seconds=60))
            assert await InboxRepo.add_transcript(tx, lease, "words", FakeSealer(), PUB)
            assert not await InboxRepo.add_transcript(tx, lease, "words", FakeSealer(), PUB)  # one per part
            assert await InboxRepo.mark_acked(tx, lease.inbox_id, LATER)
            assert not await InboxRepo.mark_acked(tx, lease.inbox_id, LATER)
            assert await InboxRepo.set_radar_at(tx, lease.inbox_id, LATER)
            with pytest.raises(ValueError):
                await InboxRepo.settle(tx, lease, "leased")
            assert await InboxRepo.settle(tx, lease, "processed")
            assert not await InboxRepo.settle(tx, lease, "dead")  # the lease is spent: a replay changes nothing
            assert not await InboxRepo.renew(tx, lease, LATER)
    row = await one(pg_db, "SELECT state, attempts, lease_token, acked_at IS NOT NULL AS a FROM inbox WHERE id = 'm1'")
    assert tuple(row) == ("processed", 1, None, True)
    assert [r["part"] for r in await raw_rows(pg_db)] == ["transcript"]


async def test_settle_back_to_received_makes_the_row_leasable_again(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_inbox(pg_db, "h1", "m1")
    async with open_store(pg_db, Role.CORE) as core:
        lease = await core.scan.lease_next(LATER)
        assert lease is not None
        async with core.tx(household=H) as tx:
            assert await InboxRepo.settle(tx, lease, "received")
        again = await core.scan.lease_next(LATER)
        assert again is not None and again.inbox_id == "m1" and again.token != lease.token


async def test_alert_open_is_idempotent_across_edge_and_worker(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_inbox(pg_db, "h1", "m1")
    a = AlertOpen(kind="emergency", origin="radar_edge", rule_ids=["RD-emergency-1"])
    async with open_store(pg_db, Role.EDGE) as edge, open_store(pg_db, Role.CORE) as core:
        async with edge.tx(household=H) as tx:
            assert await AlertRepo.open(tx, InboxId("m1"), ElderId("eh1"), a, NOW, None)
        async with core.tx(household=H) as tx:
            again = a.model_copy(update={"origin": "radar_worker"})
            assert not await AlertRepo.open(tx, InboxId("m1"), ElderId("eh1"), again, NOW, None)
            ev = EventId("al_m1_emergency")
            assert (await AlertRepo.due(tx, ev))["rule_ids"] == ["RD-emergency-1"]
            assert await AlertRepo.due(tx, EventId("nope")) is None
    assert (await one(pg_db, "SELECT count(*) AS n, min(origin) AS o FROM alert_events"))["o"] == "radar_edge"


async def test_alert_attempts_ack_and_stages(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_inbox(pg_db, "h1", "m1")
    ev, ap = EventId("al_m1_emergency"), ApproverId("ah1")
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:
        opened = AlertOpen(kind="emergency", origin="radar_worker")
        assert await AlertRepo.open(tx, InboxId("m1"), ElderId("eh1"), opened, NOW, None)
        assert await AlertRepo.add_attempt(tx, ev, "a", ap, "telegram", "alert:1", NOW)
        assert not await AlertRepo.add_attempt(tx, ev, "a", ap, "telegram", "alert:1", NOW)  # unique idempotency key
        assert await AlertRepo.add_attempt(tx, ev, "a", ap, "telegram", "alert:2", NOW)
        assert await AlertRepo.mark_attempt(tx, "alert:1", "sent", sent_at=NOW, provider_ref_hmac="p")
        assert not await AlertRepo.mark_attempt(tx, "alert:missing", "sent")
        assert await AlertRepo.set_next_due(tx, ev, NOW + timedelta(seconds=30))
        assert await AlertRepo.due(tx, ev) is None  # not due yet at tx.now
        assert not await AlertRepo.false_alarm(tx, ev)  # only an acknowledged event can be a false alarm
        assert not await AlertRepo.block(tx, ev, "other_reason")
        assert await AlertRepo.set_stage(tx, ev, "b")
        assert not await AlertRepo.set_stage(tx, ev, "a")  # stages only move forward
        assert not await AlertRepo.ack(tx, ev, ap, "dtmf")  # a key press counts only at stage c
        assert await AlertRepo.set_stage(tx, ev, "c")
        assert await AlertRepo.ack(tx, ev, ap, "dtmf")
        assert not await AlertRepo.ack(tx, ev, ap, "tap")  # already acknowledged
        assert not await AlertRepo.set_stage(tx, ev, "c")
        assert not await AlertRepo.block(tx, ev, "no_human_reached")
        assert await AlertRepo.false_alarm(tx, ev)
    row = await one(pg_db, "SELECT state, acked_by, followup_due_at - acked_at AS gap, false_alarm FROM alert_events")
    got = (row["state"], row["acked_by"], row["gap"], row["false_alarm"])
    assert got == ("acknowledged", "ah1", timedelta(seconds=900), True)
    st = await one(pg_db, "SELECT status FROM alert_attempts WHERE idem_key = 'alert:2'")
    assert st["status"] == "cancelled"  # a due attempt is cancelled by the acknowledgement
    assert (await one(pg_db, "SELECT status FROM alert_attempts WHERE idem_key = 'alert:1'"))["status"] == "sent"


async def test_alert_block_when_no_human_reached(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await seed_inbox(pg_db, "h1", "m1")
    ev = EventId("al_m1_scam")
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:
        opened = AlertOpen(kind="scam", origin="radar_worker")
        assert await AlertRepo.open(tx, InboxId("m1"), ElderId("eh1"), opened, NOW, None)
        assert await AlertRepo.block(tx, ev, "no_human_reached")
        assert not await AlertRepo.ack(tx, ev, ApproverId("ah1"), "tap")  # terminal: never rewritten
    assert (await one(pg_db, "SELECT state, blocked_reason FROM alert_events"))["blocked_reason"] == "no_human_reached"


async def test_first_seen_guards_replays_per_bot(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:
        assert await UpdateRepo.first_seen(tx, "caregiver", 7, "callback")
        assert not await UpdateRepo.first_seen(tx, "caregiver", 7, "callback")
        assert await UpdateRepo.first_seen(tx, "elder", 7, "message")  # ids repeat across bots


async def test_pause_flag(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.CORE) as core:
        async with core.tx(household=H) as tx:
            assert await FlagRepo.read_paused(tx) is False  # no row: never paused
            assert await FlagRepo.set_paused(tx, True, by="system:test", reason="drill")
            assert await FlagRepo.read_paused(tx) is True
        async with core.tx(household=H) as tx:
            assert await FlagRepo.set_paused(tx, False, by="system:test")
            assert await FlagRepo.read_paused(tx) is False


async def test_unreadable_flag_raises(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.EDGE) as edge:  # the edge has no read on runtime_flags
        with pytest.raises(FlagUnreadable):
            async with edge.tx(household=H) as tx:
                await FlagRepo.read_paused(tx)


async def test_identities_register_lookup_and_unknown(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.OWNER) as owner, open_store(pg_db, Role.CORE) as core:
        async with owner.tx(household=H) as tx:
            args = dict(sender_hmac="s-new", person_ref=PersonRef("ref-new"), person_ref_enc=b"sealed", key_id="k1")
            assert await IdentityRepo.register(tx, ElderId("eh1"), "telegram_elder", **args)
            assert not await IdentityRepo.register(tx, ElderId("eh1"), "telegram_elder", **args)
        async with core.tx(household=H) as tx:
            assert await IdentityRepo.person_ref(tx, ElderId("eh1"), "telegram_elder") == "ref-new"
            assert await IdentityRepo.person_ref(tx, ElderId("eh1"), Source.WHATSAPP) == "ref-h1"
            assert await IdentityRepo.sealed_address(tx, PersonRef("ref-new")) == (b"sealed", "k1")
            assert await IdentityRepo.sealed_address(tx, PersonRef("nope")) is None
            with pytest.raises(LookupError):
                await IdentityRepo.person_ref(tx, ElderId("eh1"), "caregiver_forward")


async def test_store_and_tx_satisfy_the_ports():
    from saathi import ports
    from saathi.store import PgTx, Store

    assert isinstance(PgTx(None, H, NOW), ports.Tx)  # type: ignore[arg-type]
    assert hasattr(Store, "tx") and "scan" in vars(Store)
