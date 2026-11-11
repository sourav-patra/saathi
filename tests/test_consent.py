"""LT-06: consent events and the gate (M06; PL-05, PL-33, PL-34; TQ-81, TQ-44). Synthetic values only."""

from __future__ import annotations

import hashlib

import asyncpg
import pytest
from conftest import PgDb
from store_helpers import NOW, open_store, seed_household

from saathi.consents import gate
from saathi.errors import StoreUnavailable
from saathi.ids import HouseholdId
from saathi.store import Role
from saathi.store.repos import ConsentEvent, ConsentRepo

H = HouseholdId("h1")
NOTICE_SHA = hashlib.sha256(b"synthetic notice text v1").hexdigest()


def ev(use: str = "transcription", recipient: str = "local_models", event: str = "grant", **kw) -> ConsentEvent:
    base = dict(
        subject="elder", use=use, recipient=recipient, event=event, capacity="self", language="hi", how="spoken",
        notice_text_sha256=NOTICE_SHA, recorded_by="owner",
    )  # fmt: skip
    return ConsentEvent(**(base | kw))


async def record(db: PgDb, *events: ConsentEvent) -> None:
    async with open_store(db, Role.OWNER) as owner:
        for e in events:
            async with owner.tx(household=H) as tx:
                await ConsentRepo.append(tx, e)


async def ask(db: PgDb, use: str, recipient: str, *, subject: str = "elder", household: HouseholdId = H) -> bool:
    async with open_store(db, Role.CORE) as core, core.tx(household=household) as tx:
        return await gate(tx, use, recipient, subject=subject)


async def test_no_row_is_false(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    assert await ask(pg_db, "transcription", "local_models") is False


async def test_grant_then_withdraw(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await record(pg_db, ev())
    assert await ask(pg_db, "transcription", "local_models") is True
    assert await ask(pg_db, "transcription", "local_models", subject="caregiver") is False  # whose data matters
    await record(pg_db, ev(event="withdraw"))
    assert await ask(pg_db, "transcription", "local_models") is False
    await record(pg_db, ev())  # the latest event wins, in either direction
    assert await ask(pg_db, "transcription", "local_models") is True


async def test_wrong_recipient_denied(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await record(pg_db, ev(use="alert_audio", recipient="telegram_caregiver"))
    assert await ask(pg_db, "alert_audio", "telegram_caregiver") is True
    assert await ask(pg_db, "alert_audio", "hosted_model") is False
    assert await ask(pg_db, "other_use", "telegram_caregiver") is False


async def test_consent_is_per_household(pg_db: PgDb):
    for h in ("h1", "h2"):
        await seed_household(pg_db, h)
    await record(pg_db, ev())
    assert await ask(pg_db, "transcription", "local_models", household=HouseholdId("h2")) is False


async def test_unknown_capacity_denies(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await record(pg_db, ev(capacity="unknown"))
    assert await ask(pg_db, "transcription", "local_models") is False  # a grant with unknown capacity is no consent
    await record(pg_db, ev(use="show_raw", recipient="telegram_caregiver"))
    assert await ask(pg_db, "transcription", "local_models") is True  # capacity restated as `self`
    await record(pg_db, ev(use="x", recipient="y", event="withdraw", capacity="unknown"))
    assert await ask(pg_db, "show_raw", "telegram_caregiver") is False  # the newest record closes every use


async def test_other_subjects_event_cannot_reopen_after_unknown(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await record(pg_db, ev(), ev(use="show_raw", recipient="telegram_caregiver"))
    assert await ask(pg_db, "transcription", "local_models") is True
    await record(pg_db, ev(capacity="unknown"))  # the elder's capacity is now unknown
    await record(pg_db, ev(subject="caregiver", use="other_use", recipient="y", capacity="self"))
    assert await ask(pg_db, "transcription", "local_models") is False
    assert await ask(pg_db, "show_raw", "telegram_caregiver") is False
    await record(pg_db, ev(capacity="self"))  # only the elder's own restatement reopens
    assert await ask(pg_db, "transcription", "local_models") is True


async def test_guardian_needs_ref(pg_db: PgDb):
    with pytest.raises(ValueError):
        ev(capacity="guardian")
    await seed_household(pg_db, "h1")
    su = await asyncpg.connect(pg_db.dsn())
    try:
        with pytest.raises(asyncpg.CheckViolationError):  # the schema refuses it too
            await su.execute(
                "INSERT INTO control.consent_events (household_id, subject, use, recipient, event, capacity, language,"
                " how, notice_text_sha256, recorded_by)"
                " VALUES ('h1','elder','u','r','grant','guardian','hi','guardian','x','o')"
            )
    finally:
        await su.close()
    await record(pg_db, ev(capacity="guardian", guardian_ref="p1", how="guardian"))
    assert await ask(pg_db, "transcription", "local_models") is True
    await record(pg_db, ev(capacity="guardian", guardian_ref="p1", event="withdraw", how="spoken"))  # the elder refuses
    assert await ask(pg_db, "transcription", "local_models") is False


async def test_events_insert_only(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await record(pg_db, ev())
    for role in (Role.OWNER, Role.CORE):
        async with open_store(pg_db, role) as store:
            for sql in ("UPDATE control.consent_events SET event = 'grant'", "DELETE FROM control.consent_events"):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with store.tx(household=H) as tx:
                        await tx.execute(sql)
    for role in (Role.CORE, Role.EDGE, Role.MODEL):  # agents read at most; none writes
        async with open_store(pg_db, role) as store:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with store.tx(household=H) as tx:
                    await ConsentRepo.append(tx, ev(event="withdraw"))


async def test_withdrawal_applies_next_call(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await record(pg_db, ev())
    async with open_store(pg_db, Role.CORE) as core:
        assert await _gate_in(core, "transcription", "local_models") is True
        await record(pg_db, ev(event="withdraw"))
        assert await _gate_in(core, "transcription", "local_models") is False  # the very next call, same pool


async def _gate_in(core, use: str, recipient: str) -> bool:
    async with core.tx(household=H) as tx:
        return await gate(tx, use, recipient)


# One denial test per enforcement point (TQ-81): a granted use elsewhere never opens another point.
async def _denied_at(db: PgDb, use: str, recipient: str) -> None:
    await seed_household(db, "h1")
    other = [("transcription", "local_models"), ("alert_audio", "telegram_caregiver")]
    other += [("show_raw", "telegram_caregiver")]
    await record(db, *[ev(use=u, recipient=r) for u, r in other if u != use])
    assert await ask(db, use, recipient) is False


async def test_denial_at_worker(pg_db: PgDb):
    await _denied_at(pg_db, "transcription", "local_models")  # the R01 guard: no processing without consent


async def test_denial_at_alert_audio(pg_db: PgDb):
    await _denied_at(pg_db, "alert_audio", "telegram_caregiver")  # no audio attached to the alert


async def test_denial_at_show_raw(pg_db: PgDb):
    await _denied_at(pg_db, "show_raw", "telegram_caregiver")


async def test_gate_fails_closed_on_an_unreadable_store():
    class Down:
        household = H
        now = NOW

        async def fetch(self, *_a):
            raise StoreUnavailable("ConnectionError")

    assert await gate(Down(), "u", "r") is False


async def test_notice_hash_stored(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    await record(pg_db, ev())
    su = await asyncpg.connect(pg_db.dsn())
    try:
        row = await su.fetchrow("SELECT notice_text_sha256, recorded_by, recorded_at FROM control.consent_events")
    finally:
        await su.close()
    assert row["notice_text_sha256"] == NOTICE_SHA and row["recorded_by"] == "owner" and row["recorded_at"] == NOW
