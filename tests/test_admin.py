"""LT-46: the owner's admin commands (M24; RQ-95a, 110, 111a; PL-32; TQ-41, 73, 80). Synthetic values only.

Tests that need Postgres skip without SAATHI_TEST_DSN (and fail under CI=true without it).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

import asyncpg
import pytest
from conftest import PgDb
from store_helpers import NOW, open_store

from saathi.admin import (
    OWNER_ACTOR,
    Admin,
    AdminError,
    build_parser,
    dispatch,
    read_receipt,
    terms_receipt_allows,
)
from saathi.clock import FakeClock
from saathi.errors import ReceiptMissing
from saathi.ids import HouseholdId
from saathi.store import Role
from saathi.store.keys import KeyRing, sender_hmac
from saathi.store.repos import ConfigRepo, SignoffRepo
from saathi.store.repos.voice import VOICE_SCOPE

KEK = os.urandom(32)
SENDER_KEY = os.urandom(32)
H = "h1"
CLIP_DIGEST = hashlib.sha256(b"clip").hexdigest()
TODAY = NOW.date()
CONFIG = {
    "caps": {"order_inr": "500", "day_inr": "1000", "monthly_soft_inr": "5000"},
    "approvers": [{"approver_id": "a1", "role": "primary"}],
    "elder_channel_active": "telegram",
    "channels_enabled": ["telegram"],
    "helper_name": "Helper",
}


@asynccontextmanager
async def admin_for(db: PgDb, tmp_path: Path, *, kek: bytes | None = KEK) -> AsyncIterator[Admin]:
    async with open_store(db, Role.OWNER) as store:
        yield Admin(store, KeyRing(kek), FakeClock(NOW), sender_key=SENDER_KEY, receipts_dir=tmp_path / "receipts")


async def su_fetch(db: PgDb, sql: str, *args):
    conn = await asyncpg.connect(db.dsn())
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def onboard(a: Admin, h: str = H) -> None:
    await a.household_create(h, name="Household One")
    await a.elder_add(h, "e1" if h == H else "e" + h, name="Elder One")
    await a.approver_add(h, "a1" if h == H else "a" + h, telegram_id=9001, primary=True)


def write_terms(dir_: Path, *, when: date, decision: str = "allowed", signer: str = "owner") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / f"RECEIPT-pl-32-{when.isoformat()}.md"
    path.write_text(
        "The platform terms for AI assistants were read and recorded.\n"
        f"id: PL-32\ndate: {when.isoformat()}\nresult: verified\nsigner: {signer}\ndecision: {decision}\n"
        "note: clause cited, no terms text\n"
    )
    return path


def write_review(path: Path, *, signer: str = "caregiver") -> Path:
    path.write_text(f"A caregiver listened to the clip.\ndate: {TODAY.isoformat()}\nsigner: {signer}\n")
    return path


# --- without Postgres -----------------------------------------------------------------------------------------


def test_the_owner_role_is_required(tmp_path: Path):
    class FakeStore:
        role = Role.CORE

    with pytest.raises(PermissionError):
        Admin(FakeStore(), KeyRing(KEK), FakeClock(NOW))  # type: ignore[arg-type]


def test_terms_receipt_rules(tmp_path: Path):
    d = tmp_path / "r"
    assert terms_receipt_allows(d, TODAY) is False  # no directory
    write_terms(d, when=TODAY)
    assert terms_receipt_allows(d, TODAY) is True
    assert terms_receipt_allows(d, TODAY + timedelta(days=90)) is True
    assert terms_receipt_allows(d, TODAY + timedelta(days=91)) is False  # stale
    assert terms_receipt_allows(d, TODAY - timedelta(days=1)) is False  # dated in the future


@pytest.mark.parametrize("decision", ["unclear", "not allowed", ""])
def test_terms_receipt_that_does_not_allow_is_refused(tmp_path: Path, decision: str):
    write_terms(tmp_path, when=TODAY, decision=decision)
    assert terms_receipt_allows(tmp_path, TODAY) is False


def test_terms_receipt_needs_a_signer(tmp_path: Path):
    write_terms(tmp_path, when=TODAY, signer="")
    assert terms_receipt_allows(tmp_path, TODAY) is False


def test_receipt_reader_rejects_a_missing_or_headingless_file(tmp_path: Path):
    with pytest.raises(ReceiptMissing):
        read_receipt(tmp_path / "none.md")
    bad = tmp_path / "bad.md"
    bad.write_text("# Title\nsigner: x\n")
    with pytest.raises(ReceiptMissing):
        read_receipt(bad)


def test_parser_covers_every_command():
    p = build_parser()
    for argv in (
        ["household", "create", "h1", "--name", "n"],
        ["elder", "add", "h1", "e1", "--name", "n", "--lang", "hi"],
        ["approver", "add", "h1", "a1", "--telegram-id", "1", "--primary"],
        ["keys", "init", "h1"],
        ["keys", "rotate", "h1", "content"],
        ["keys", "rewrap", "--old-file", "a", "--new-file", "b"],
        ["identity", "add", "h1", "e1", "--channel", "telegram_elder", "--sender", "1"],
        ["signoff", "grant", "h1", "SO-1", "--days", "90"],
        ["signoff", "revoke", "h1", "SO-1"],
        ["consent", "record", "h1", "--file", "f"],
        ["config", "set", "h1", "--file", "f"],
        ["clips", "review", "ack", "1", "--receipt", "r", "--household", "h1"],
        "place add --household h1 --label L --address A --lat 0.1 --lng 0.2 --alias a1 --alias a2 --pickup".split(),
        ["place", "list", "--household", "h1"],
        ["place", "alias-add", "--household", "h1", "--place", "L", "--alias", "x"],
        ["place", "remove", "--household", "h1", "--place", "L"],
    ):
        assert isinstance(p.parse_args(argv), argparse.Namespace)
    with pytest.raises(SystemExit):
        p.parse_args(["keys", "rotate", "h1", "totp"])  # only content and audit rotate


# --- with Postgres --------------------------------------------------------------------------------------------


async def test_commands_use_owner_role(pg_db: PgDb, tmp_path: Path):
    async with open_store(pg_db, Role.OWNER) as store, store.tx(household=HouseholdId(H)) as tx:
        assert (await tx.fetch("SELECT current_user AS u"))[0]["u"] == "saathi_owner"
    async with open_store(pg_db, Role.CORE) as core:
        with pytest.raises(PermissionError):
            Admin(core, KeyRing(KEK), FakeClock(NOW))
    async with admin_for(pg_db, tmp_path) as a:
        await a.household_create(H, name="Household One")
    rows = await su_fetch(pg_db, "SELECT DISTINCT actor FROM audit_log")
    assert [r["actor"] for r in rows] == [OWNER_ACTOR]


async def test_household_elder_approver_identity_created(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        await a.keys_init(H)
        ref = await a.identity_add(H, "e1", channel="telegram_elder", sender="424242")
    assert (await su_fetch(pg_db, "SELECT name FROM households WHERE id = 'h1'"))[0]["name"] == "Household One"
    assert (await su_fetch(pg_db, "SELECT languages FROM elders WHERE id = 'e1'"))[0]["languages"] == ["hi", "en"]
    appr = (await su_fetch(pg_db, "SELECT is_primary, telegram_user_id FROM approvers WHERE id = 'a1'"))[0]
    assert (appr["is_primary"], appr["telegram_user_id"]) == (True, 9001)
    ident = (await su_fetch(pg_db, "SELECT * FROM elder_identities"))[0]
    assert ident["person_ref"] == ref and ident["active"] is True and ident["channel"] == "telegram_elder"
    assert ident["sender_hmac"] == sender_hmac(SENDER_KEY, "telegram_elder", "424242")
    assert b"424242" not in bytes(ident["person_ref_enc"])  # sealed, not plain
    async with open_store(pg_db, Role.OWNER) as store, store.tx(household=HouseholdId(H)) as tx:
        opened = await KeyRing(KEK).open(
            tx, bytes(ident["person_ref_enc"]), ident["key_id"], aad=f"h1|{ident['id']}|ref".encode()
        )
    assert opened == b"424242"


async def test_duplicates_and_unknown_households_are_refused(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        with pytest.raises(AdminError):
            await a.household_create(H, name="Again")
        with pytest.raises(AdminError):
            await a.elder_add(H, "e1", name="Again")
        with pytest.raises(AdminError):
            await a.approver_add(H, "a2", telegram_id=9001)  # same chat in the same household
        with pytest.raises(AdminError):
            await a.elder_add("nope", "e9", name="X")


async def test_identity_before_keys_refused(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        with pytest.raises(AdminError, match="keys"):
            await a.identity_add(H, "e1", channel="telegram_elder", sender="424242")
    assert await su_fetch(pg_db, "SELECT 1 FROM elder_identities") == []


async def test_identity_needs_a_sender_key_and_a_known_elder(pg_db: PgDb, tmp_path: Path):
    async with open_store(pg_db, Role.OWNER) as store:
        a = Admin(store, KeyRing(KEK), FakeClock(NOW), sender_key=None)
        await onboard(a)
        await a.keys_init(H)
        with pytest.raises(AdminError):
            await a.identity_add(H, "e1", channel="telegram_elder", sender="1")
        b = Admin(store, KeyRing(KEK), FakeClock(NOW), sender_key=SENDER_KEY)
        with pytest.raises(AdminError):
            await b.identity_add(H, "ghost", channel="telegram_elder", sender="1")
        await b.identity_add(H, "e1", channel="telegram_elder", sender="1")
        with pytest.raises(AdminError, match="already"):
            await b.identity_add(H, "e1", channel="telegram_elder", sender="1")


async def test_keys_init_one_active_key_per_purpose(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        made = await a.keys_init(H)
        with pytest.raises(AdminError, match="already"):
            await a.keys_init(H)
    assert set(made) == {"content", "audit", "totp"}
    rows = await su_fetch(
        pg_db, "SELECT purpose, key_id, public_key FROM control.household_keys WHERE retired_at IS NULL"
    )
    assert sorted(r["purpose"] for r in rows) == ["audit", "content", "totp"]
    assert {r["purpose"]: r["key_id"] for r in rows} == made
    assert [r["purpose"] for r in rows if r["public_key"] is not None] == ["content"]


async def test_keys_init_without_the_kek_writes_nothing(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path, kek=None) as a:
        await onboard(a)
        with pytest.raises(AdminError, match="key-encryption"):
            await a.keys_init(H)
    assert await su_fetch(pg_db, "SELECT 1 FROM control.household_keys") == []


async def test_rotation_reads_both_key_ids(pg_db: PgDb, tmp_path: Path):
    ring = KeyRing(KEK)
    async with admin_for(pg_db, tmp_path) as a, open_store(pg_db, Role.OWNER) as store:
        await onboard(a)
        made = await a.keys_init(H)
        async with store.tx(household=HouseholdId(H)) as tx:
            old_public = await ring.public(tx, "content")
        old_blob = ring.seal(old_public, b"before", aad=b"x")
        new_id = await a.keys_rotate(H, "content")
        new_audit = await a.keys_rotate(H, "audit")
        async with store.tx(household=HouseholdId(H)) as tx:
            new_public = await ring.public(tx, "content")
            assert new_public != old_public and new_id != made["content"]
            new_blob = ring.seal(new_public, b"after", aad=b"x")
            assert await ring.open(tx, old_blob, made["content"], aad=b"x") == b"before"  # old id still opens
            assert await ring.open(tx, new_blob, new_id, aad=b"x") == b"after"
            _, key_id = await ring.hmac(tx, "d", "v")
            assert key_id == new_audit != made["audit"]
    rows = await su_fetch(pg_db, "SELECT purpose FROM control.household_keys WHERE retired_at IS NULL ORDER BY purpose")
    assert [r["purpose"] for r in rows] == ["audit", "content", "totp"]  # still one active per purpose
    assert len(await su_fetch(pg_db, "SELECT 1 FROM control.household_keys")) == 5


async def test_rotation_refuses_totp_and_missing_keys(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        with pytest.raises(AdminError):
            await a.keys_rotate(H, "totp")
        with pytest.raises(AdminError, match="keys init"):
            await a.keys_rotate(H, "content")


async def test_rewrap_keeps_plaintext(pg_db: PgDb, tmp_path: Path):
    new_kek = os.urandom(32)
    async with admin_for(pg_db, tmp_path) as a, open_store(pg_db, Role.OWNER) as store:
        await onboard(a)
        await onboard(a, "h2")
        await a.keys_init(H)
        await a.keys_init("h2")
        before = await su_fetch(pg_db, "SELECT household_id, purpose, key_id, wrapped_key FROM control.household_keys")
        plain = {
            (r["household_id"], r["purpose"]): KeyRing(KEK).unwrap(
                bytes(r["wrapped_key"]),
                household=HouseholdId(r["household_id"]),
                purpose=r["purpose"],
                key_id=r["key_id"],
            )
            for r in before
        }
        with pytest.raises(AdminError):
            await a.keys_rewrap(KEK, KEK)
        with pytest.raises(AdminError, match="neither"):
            await a.keys_rewrap(os.urandom(32), os.urandom(32))
        assert await su_fetch(pg_db, "SELECT household_id, purpose, wrapped_key FROM control.household_keys") != []
        assert await a.keys_rewrap(KEK, new_kek) == 6
        assert await a.keys_rewrap(KEK, new_kek) == 0  # a repeat finds every row already under the new key
        after = await su_fetch(pg_db, "SELECT household_id, purpose, key_id, wrapped_key FROM control.household_keys")
        for r in after:
            got = KeyRing(new_kek).unwrap(
                bytes(r["wrapped_key"]),
                household=HouseholdId(r["household_id"]),
                purpose=r["purpose"],
                key_id=r["key_id"],
            )
            assert got == plain[(r["household_id"], r["purpose"])]
        # the old KEK no longer opens anything, and the audit key still works under the new one
        async with store.tx(household=HouseholdId(H)) as tx:
            assert (await KeyRing(new_kek).hmac(tx, "d", "v"))[0]


async def test_signoff_grant_and_revoke_write_rows(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a, open_store(pg_db, Role.OWNER) as store:
        await onboard(a)
        await a.keys_init(H)
        with pytest.raises(AdminError):
            await a.signoff_revoke(H, "SO-1")  # nothing to revoke
        with pytest.raises(AdminError):
            await a.signoff_grant(H, "SO-9", days=90)
        with pytest.raises(AdminError):
            await a.signoff_grant(H, "SO-1", days=0)
        sid = await a.signoff_grant(H, "SO-1", days=90)
        async with store.tx(household=HouseholdId(H)) as tx:
            state = await SignoffRepo.valid(tx, "SO-1", NOW)
        assert (state.valid, state.id, state.expires_at) == (True, sid, NOW + timedelta(days=90))
        assert await a.signoff_revoke(H, "SO-1") == sid
        with pytest.raises(AdminError):
            await a.signoff_revoke(H, "SO-1")  # already revoked
        async with store.tx(household=HouseholdId(H)) as tx:
            assert (await SignoffRepo.valid(tx, "SO-1", NOW)).valid is False
    row = (await su_fetch(pg_db, "SELECT granter, revoked_at FROM control.signoffs"))[0]
    assert row["granter"] == "owner" and row["revoked_at"] == NOW


async def test_consent_and_config_rows(pg_db: PgDb, tmp_path: Path):
    consent = tmp_path / "consent.json"
    consent.write_text(
        json.dumps(
            {
                "subject": "elder", "use": "hosted_model", "recipient": "provider-a", "event": "grant",
                "capacity": "self", "language": "hi", "how": "spoken", "notice_text_sha256": CLIP_DIGEST,
                "recorded_by": "owner",
            }
        )
    )  # fmt: skip
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(CONFIG))
    async with admin_for(pg_db, tmp_path) as a, open_store(pg_db, Role.OWNER) as store:
        await onboard(a)
        await a.keys_init(H)
        await a.consent_record(H, consent)
        assert await a.config_set(H, cfg) == 1
        assert await a.config_set(H, cfg) == 2  # no version in the file: the next one
        async with store.tx(household=HouseholdId(H)) as tx:
            loaded = await ConfigRepo.load(tx)
        assert loaded.version == 2 and loaded.helper_name == "Helper" and loaded.caps is not None
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({**CONFIG, "caps": {"order_inr": "-1", "day_inr": "1", "monthly_soft_inr": "1"}}))
        with pytest.raises(AdminError, match="caps") as err:
            await a.config_set(H, bad)
        assert "-1" not in str(err.value)  # field names only, never the value
        bad_consent = tmp_path / "bad-consent.json"
        bad_consent.write_text(json.dumps({"subject": "elder"}))
        with pytest.raises(AdminError):
            await a.consent_record(H, bad_consent)
        with pytest.raises(AdminError):
            await a.config_set(H, tmp_path / "missing.json")
    ev = (await su_fetch(pg_db, "SELECT event, capacity FROM control.consent_events"))[0]
    assert (ev["event"], ev["capacity"]) == ("grant", "self")


async def test_each_command_writes_audit_row(pg_db: PgDb, tmp_path: Path):
    consent = tmp_path / "consent.json"
    consent.write_text(
        json.dumps(
            {
                "subject": "elder", "use": "u", "recipient": "r", "event": "grant", "capacity": "self",
                "language": "hi", "how": "spoken", "notice_text_sha256": CLIP_DIGEST, "recorded_by": "owner",
            }
        )
    )  # fmt: skip
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(CONFIG))
    review = write_review(tmp_path / "review.md")
    write_terms(tmp_path / "receipts", when=TODAY)
    async with admin_for(pg_db, tmp_path) as a:
        await a.household_create(H, name="Household One")
        await a.elder_add(H, "e1", name="Elder One")
        await a.approver_add(H, "a1", telegram_id=9001)
        await a.keys_init(H)
        await a.keys_rotate(H, "content")
        await a.identity_add(H, "e1", channel="whatsapp", sender="+91 0000 000001")
        sid = await a.signoff_grant(H, "SO-1", days=30)
        await a.signoff_revoke(H, "SO-1")
        await a.consent_record(H, consent)
        await a.config_set(H, cfg)
        await seed_clip(pg_db)
        await a.clips_review(H, "ack", 1, receipt=review)
        await a.keys_rewrap(KEK, os.urandom(32))
    rows = await su_fetch(
        pg_db, "SELECT event, actor, key_id, subject, payload_hmac, detail FROM audit_log ORDER BY seq"
    )
    events = [r["event"] for r in rows]
    assert events == [
        "admin_household_created", "admin_elder_added", "admin_approver_added", "admin_keys_initialised",
        "key_rotated", "admin_identity_added", "admin_signoff_granted", "admin_signoff_revoked",
        "admin_consent_recorded", "admin_config_set", "admin_clip_reviewed", "admin_keys_rewrapped",
    ]  # fmt: skip
    assert {r["actor"] for r in rows} == {OWNER_ACTOR}
    unkeyed = [r for r in rows if r["key_id"] == "unkeyed"]
    assert [r["event"] for r in unkeyed] == events[:3]  # only the commands that run before keys init
    assert all(r["payload_hmac"] is None and r["subject"] is None for r in unkeyed)
    keyed = [r for r in rows if r["key_id"] != "unkeyed"]
    assert all(r["payload_hmac"] for r in keyed)
    flat = json.dumps([dict(r) for r in rows], default=str)
    for secret in ("+91 0000 000001", "910000000001", "Household One", "Elder One"):
        assert secret not in flat  # no raw identity or name in the audit rows
    assert sid == 1


async def seed_clip(db: PgDb) -> None:
    async with open_store(db, Role.OWNER) as owner, owner.tx(household=VOICE_SCOPE) as tx:
        from saathi.store.repos import VoiceRepo

        await VoiceRepo.insert(
            tx, reply_key="ack", version=1, language="hi", text="t", sha256=CLIP_DIGEST, path="ack.v1.ogg"
        )


async def test_clips_review_needs_receipt(pg_db: PgDb, tmp_path: Path):
    await seed_clip(pg_db)
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        with pytest.raises(ReceiptMissing):
            await a.clips_review(H, "ack", 1, receipt=tmp_path / "absent.md")
        no_signer = tmp_path / "no-signer.md"
        no_signer.write_text(f"A caregiver listened.\ndate: {TODAY.isoformat()}\n")
        with pytest.raises(ReceiptMissing):
            await a.clips_review(H, "ack", 1, receipt=no_signer)
        row = (await su_fetch(pg_db, "SELECT reviewed_by, receipt_ref FROM voice_clips"))[0]
        assert row["reviewed_by"] is None and row["receipt_ref"] is None  # nothing written
        with pytest.raises(AdminError, match="no such clip"):
            await a.clips_review(H, "ack", 7, receipt=write_review(tmp_path / "r.md"))
        await a.clips_review(H, "ack", 1, receipt=write_review(tmp_path / "r.md", signer="caregiver"))
    row = (await su_fetch(pg_db, "SELECT reviewed_by, reviewed_at, receipt_ref FROM voice_clips"))[0]
    assert (row["reviewed_by"], row["reviewed_at"], row["receipt_ref"]) == ("caregiver", NOW, "r.md")


async def test_whatsapp_identity_needs_terms_receipt(pg_db: PgDb, tmp_path: Path):
    receipts = tmp_path / "receipts"
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        await a.keys_init(H)
        with pytest.raises(AdminError, match="terms"):  # no receipt
            await a.identity_add(H, "e1", channel="whatsapp", sender="+91 0000 000001")
        write_terms(receipts, when=TODAY, decision="unclear")
        with pytest.raises(AdminError, match="terms"):  # unclear reading
            await a.identity_add(H, "e1", channel="whatsapp", sender="+91 0000 000001")
        for p in receipts.glob("RECEIPT-pl-32-*.md"):
            p.unlink()
        write_terms(receipts, when=TODAY - timedelta(days=120))
        with pytest.raises(AdminError, match="terms"):  # stale
            await a.identity_add(H, "e1", channel="whatsapp", sender="+91 0000 000001")
        assert await su_fetch(pg_db, "SELECT 1 FROM elder_identities") == []
        await a.identity_add(H, "e1", channel="telegram_elder", sender="7")  # Telegram needs no WhatsApp receipt
        write_terms(receipts, when=TODAY)
        await a.identity_add(H, "e1", channel="whatsapp", sender="+91 0000 000001")
    ident = await su_fetch(pg_db, "SELECT channel, sender_hmac FROM elder_identities ORDER BY channel")
    assert [r["channel"] for r in ident] == ["telegram_elder", "whatsapp"]
    assert ident[1]["sender_hmac"] == sender_hmac(SENDER_KEY, "whatsapp", "+910000000001")  # as the edge computes it


async def test_the_command_line_prints_no_secret(pg_db: PgDb, tmp_path: Path, capsys):
    del capsys
    p = build_parser()
    async with admin_for(pg_db, tmp_path) as a:
        out = [
            await dispatch(p.parse_args(argv), a)
            for argv in (
                ["household", "create", H, "--name", "Household One"],
                ["elder", "add", H, "e1", "--name", "Elder One"],
                ["approver", "add", H, "a1", "--telegram-id", "9001"],
                ["keys", "init", H],
                ["identity", "add", H, "e1", "--channel", "telegram_elder", "--sender", "424242"],
            )
        ]
    text = "\n".join(out)
    assert "424242" not in text and KEK.hex() not in text and SENDER_KEY.hex() not in text
    assert "content key_id:" in text and "person_ref" in text


# --- review fixes (F1 to F6) ----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_settings():
    from saathi.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()  # the cached settings must not outlive a test's environment


def _cli_env(db: PgDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from saathi.config import get_settings

    kek, sender = tmp_path / "kek", tmp_path / "sender"
    kek.write_bytes(KEK)
    sender.write_bytes(SENDER_KEY)
    for p in (kek, sender):
        p.chmod(0o600)
    monkeypatch.setenv("SAATHI_DB_URL_OWNER", db.dsn("saathi_owner"))
    monkeypatch.setenv("SAATHI_KEK_FILE", str(kek))
    monkeypatch.setenv("SAATHI_SENDER_HMAC_KEY_FILE", str(sender))
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


def test_f1_every_command_after_keys_init_runs_through_main(
    pg_db: PgDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    from saathi.admin import main

    _cli_env(pg_db, tmp_path, monkeypatch)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(CONFIG))
    codes = [
        main(argv)
        for argv in (
            ["household", "create", H, "--name", "Household One"],
            ["keys", "init", H],
            ["elder", "add", H, "e1", "--name", "Elder One"],
            ["approver", "add", H, "a1", "--telegram-id", "9001", "--primary"],
            ["config", "set", H, "--file", str(cfg)],
            ["signoff", "grant", H, "SO-1", "--days", "30"],
            ["signoff", "revoke", H, "SO-1"],
            ["identity", "add", H, "e1", "--channel", "telegram_elder", "--sender", "424242"],
        )
    ]
    err = capsys.readouterr().err
    assert codes == [0] * 8, err


def _cfg(tmp_path: Path, name: str = "cfg", **over) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({**CONFIG, **over}))
    return path


async def test_f2_config_refuses_whatsapp_without_an_allowing_receipt(pg_db: PgDb, tmp_path: Path):
    receipts = tmp_path / "receipts"
    active = _cfg(tmp_path, "active", elder_channel_active="whatsapp", channels_enabled=["whatsapp"])
    enabled = tmp_path / "enabled.json"
    enabled.write_text(json.dumps({**CONFIG, "channels_enabled": ["telegram", "whatsapp"]}))
    telegram = _cfg(tmp_path, "telegram")
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        await a.keys_init(H)
        for f in (active, enabled):
            with pytest.raises(AdminError, match="terms"):  # no receipt
                await a.config_set(H, f)
        write_terms(receipts, when=TODAY, decision="unclear")
        for f in (active, enabled):
            with pytest.raises(AdminError, match="terms"):  # unclear reading
                await a.config_set(H, f)
        assert await a.config_set(H, telegram) == 1  # unclear keeps Telegram
        assert await su_fetch(pg_db, "SELECT 1 FROM control.household_config WHERE version > 1") == []
        write_terms(receipts, when=TODAY)
        assert await a.config_set(H, active) == 2


def test_f3_empty_receipt_error_has_a_message(monkeypatch: pytest.MonkeyPatch, capsys):
    import saathi.admin as admin_mod

    async def boom(args):
        raise ReceiptMissing()

    monkeypatch.setattr(admin_mod, "_run", boom)
    assert admin_mod.main(["household", "create", H, "--name", "n"]) == 2
    err = capsys.readouterr().err.strip()
    assert err != "error:" and "receipt" in err


def test_f4_bad_household_id_is_a_tidy_refusal(pg_db: PgDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    from saathi.admin import main

    _cli_env(pg_db, tmp_path, monkeypatch)
    assert main(["household", "create", "../x\n", "--name", "n"]) == 2
    err = capsys.readouterr().err
    assert err.strip() == "error: invalid id" and "Traceback" not in err


@pytest.mark.parametrize("lang", ["xx-evil", "", "HI"])
def test_f5_lang_is_validated(lang: str):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["elder", "add", "h1", "e1", "--name", "n", "--lang", lang])
    assert build_parser().parse_args(["elder", "add", "h1", "e1", "--name", "n", "--lang", "en"]).lang == "en"


async def test_f5_elder_add_refuses_an_unknown_language(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await a.household_create(H, name="Household One")
        with pytest.raises(AdminError, match="language"):
            await a.elder_add(H, "e1", name="n", lang="xx-evil")
    assert await su_fetch(pg_db, "SELECT 1 FROM elders") == []


# --- ride places (008) ----------------------------------------------------------------------------------------


async def test_place_add_list_alias_and_remove(pg_db: PgDb, tmp_path: Path):
    p = build_parser()
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        out = await dispatch(
            p.parse_args(
                ["place", "add", "--household", H, "--label", "Clinic A", "--address", "2 Test Road, Sample Town",
                 "--lat", "0.1", "--lng", "0.2", "--alias", "doctor", "--alias", "dr"]
            ),
            a,
        )  # fmt: skip
        clinic = out.removeprefix("place added: ")
        home = await a.place_add(H, label="Home P", address="1 Pickup Lane, Sample Town", pickup=True)
        listing = await dispatch(p.parse_args(["place", "list", "--household", H]), a)
        assert listing.splitlines() == [
            f"{clinic}  Clinic A  pickup=no  coordinates=yes  rides=0  aliases=doctor, dr",
            f"{home}  Home P  pickup=yes  coordinates=no  rides=0  aliases=-",
        ]
        assert "Test Road" not in listing  # the address itself is not printed
        by_label = p.parse_args(["place", "alias-add", "--household", H, "--place", "clinic a", "--alias", "Doctor"])
        by_id = p.parse_args(["place", "alias-add", "--household", H, "--place", home, "--alias", "ghar"])
        assert await dispatch(by_label, a) == "alias added"  # a label matches in any case
        assert await dispatch(by_id, a) == "alias added"
        with pytest.raises(AdminError, match="already has this alias"):
            await a.place_alias_add(H, clinic, "doctor")
        with pytest.raises(AdminError, match="no such place"):
            await a.place_alias_add(H, "nowhere", "x")
        with pytest.raises(AdminError, match="letter or digit"):
            await a.place_alias_add(H, clinic, "?!")
        removal = p.parse_args(["place", "remove", "--household", H, "--place", "Clinic A"])
        assert await dispatch(removal, a) == "place removed"
        with pytest.raises(AdminError, match="no such place"):
            await a.place_remove(H, clinic)
    rows_ = await su_fetch(pg_db, "SELECT id, aliases FROM addresses")
    assert [(r["id"], r["aliases"]) for r in rows_] == [(home, ["ghar"])]


async def test_place_rules(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        await onboard(a, "h2")
        await a.place_add(H, label="Home P", address="x", pickup=True)
        with pytest.raises(AdminError, match="already has a pickup"):
            await a.place_add(H, label="Home Q", address="y", pickup=True)
        await a.place_add("h2", label="Home P", address="x", pickup=True)  # another household has its own
        with pytest.raises(AdminError, match="both"):
            await a.place_add(H, label="Clinic A", address="y", lat=0.1)
        with pytest.raises(AdminError, match="out of range"):
            await a.place_add(H, label="Clinic A", address="y", lat=91.0, lng=0.0)
        with pytest.raises(AdminError, match="label and an address"):
            await a.place_add(H, label=" ", address="y")
        with pytest.raises(AdminError, match="letter or digit"):
            await a.place_add(H, label="Clinic A", address="y", aliases=["..."])
        with pytest.raises(AdminError, match="no such household"):
            await a.place_add("nope", label="Clinic A", address="y")
        await a.place_add(H, label="Twin", address="y")
        await a.place_add(H, label="twin", address="z")
        with pytest.raises(AdminError, match="use the id"):
            await a.place_remove(H, "TWIN")  # two places share the label: the id is needed
    assert len(await su_fetch(pg_db, "SELECT 1 FROM addresses WHERE household_id = 'h1'")) == 3


async def test_place_commands_audit_codes_only(pg_db: PgDb, tmp_path: Path):
    async with admin_for(pg_db, tmp_path) as a:
        await onboard(a)
        await a.keys_init(H)
        place = await a.place_add(H, label="Clinic A", address="2 Test Road, Sample Town", aliases=["doctor"])
        await a.place_alias_add(H, place, "dr")
        await a.place_remove(H, place)
    rows_ = await su_fetch(pg_db, "SELECT event, actor, subject, detail FROM audit_log WHERE event LIKE 'admin_place%'")
    assert [r["event"] for r in rows_] == ["admin_place_added", "admin_place_alias_added", "admin_place_removed"]
    assert {r["actor"] for r in rows_} == {OWNER_ACTOR}
    flat = json.dumps([dict(r) for r in rows_], default=str)
    for private in ("Clinic A", "Test Road", "doctor"):
        assert private not in flat  # no label, address or alias in an audit row
