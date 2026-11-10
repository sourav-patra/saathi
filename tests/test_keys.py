"""LT-04: KeyRing, envelope keys per household (M05; RQ-111a, RQ-56a; TQ-73). Synthetic keys, made in the test."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import timedelta
from types import SimpleNamespace

import asyncpg
import pytest
from conftest import PgDb
from store_helpers import NOW, open_store, seed_household

from saathi.domain import InboundMessage, MediaKind, NewInbox, RawPart, Source
from saathi.errors import KeyMissing
from saathi.ids import ElderId, HouseholdId, InboxId, PublicKey
from saathi.store import Role
from saathi.store.keys import KeyRing, generate_content_pair, generate_secret, sender_hmac
from saathi.store.repos import InboxRepo

CANARY = "canary-phrase-7f3a91"
KEK = os.urandom(32)
AAD = b"h1|m1|text"


class MemReader:
    """A KeyReader over in-memory rows: {household: {purpose: {key_id: (public, wrapped, active)}}}."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict[str, tuple[bytes | None, bytes, bool]]]] = {}

    def put(self, h: str, purpose: str, key_id: str, public: bytes | None, wrapped: bytes, active: bool = True) -> None:
        self.rows.setdefault(h, {}).setdefault(purpose, {})[key_id] = (public, wrapped, active)

    async def active_public(self, tx, purpose):
        for kid, (pub, _w, active) in self.rows.get(tx.household, {}).get(purpose, {}).items():
            if active and pub is not None:
                return kid, pub
        return None

    async def wrapped(self, tx, purpose, key_id):
        rows = self.rows.get(tx.household, {}).get(purpose, {})
        for kid, (_p, w, active) in rows.items():
            if (key_id is None and active) or kid == key_id:
                return kid, w
        return None


def tx_of(h: str) -> SimpleNamespace:
    return SimpleNamespace(household=HouseholdId(h), now=NOW)


def provision(ring: KeyRing, reader: MemReader, h: str, key_id: str = "k1") -> PublicKey:
    pub, priv = generate_content_pair()
    wrapped = ring.wrap(priv, household=HouseholdId(h), purpose="content", key_id=key_id)
    reader.put(h, "content", key_id, bytes(pub), wrapped)
    return pub


async def test_seal_open_roundtrip():
    reader = MemReader()
    core = KeyRing(KEK, reader)
    pub = provision(core, reader, "h1")
    assert await core.public(tx_of("h1"), "content") == pub
    blob = core.seal(pub, CANARY.encode(), aad=AAD)
    assert blob[:1] == b"\x01" and len(blob) == 1 + 32 + 12 + len(CANARY) + 16
    assert await core.open(tx_of("h1"), blob, "k1", aad=AAD) == CANARY.encode()
    assert core.seal(pub, b"x", aad=AAD) != core.seal(pub, b"x", aad=AAD)  # fresh ephemeral key and nonce each time


async def test_cross_household_decrypt_fails():
    reader = MemReader()
    core = KeyRing(KEK, reader)
    pub1 = provision(core, reader, "h1")
    provision(core, reader, "h2")  # same key id, other key pair
    blob = core.seal(pub1, b"secret", aad=AAD)
    with pytest.raises(ValueError):
        await core.open(tx_of("h2"), blob, "k1", aad=AAD)
    with pytest.raises(KeyMissing):
        await core.open(tx_of("h3"), blob, "k1", aad=AAD)  # a household with no key row


async def test_blob_moved_to_other_row_fails_aad():
    reader = MemReader()
    core = KeyRing(KEK, reader)
    pub = provision(core, reader, "h1")
    blob = core.seal(pub, b"secret", aad=b"h1|m1|text")
    for moved in (b"h1|m2|text", b"h1|m1|caption", b"h2|m1|text"):
        with pytest.raises(ValueError):
            await core.open(tx_of("h1"), blob, "k1", aad=moved)
    with pytest.raises(ValueError):
        await core.open(tx_of("h1"), blob[:-1] + bytes([blob[-1] ^ 1]), "k1", aad=b"h1|m1|text")
    with pytest.raises(ValueError):
        await core.open(tx_of("h1"), b"\x02" + blob[1:], "k1", aad=b"h1|m1|text")


async def test_wrapped_key_is_bound_to_its_row_and_kek():
    core = KeyRing(KEK)
    wrapped = core.wrap(b"s" * 32, household=HouseholdId("h1"), purpose="audit", key_id="k1")
    assert core.unwrap(wrapped, household=HouseholdId("h1"), purpose="audit", key_id="k1") == b"s" * 32
    for bad in ({"household": "h2"}, {"purpose": "totp"}, {"key_id": "k2"}):
        kw = {"household": "h1", "purpose": "audit", "key_id": "k1"} | bad
        with pytest.raises(KeyMissing):
            core.unwrap(wrapped, household=HouseholdId(kw["household"]), purpose=kw["purpose"], key_id=kw["key_id"])
    with pytest.raises(KeyMissing):
        KeyRing(os.urandom(32)).unwrap(wrapped, household=HouseholdId("h1"), purpose="audit", key_id="k1")


async def test_edge_keyring_cannot_open():
    reader = MemReader()
    core = KeyRing(KEK, reader)
    pub = provision(core, reader, "h1")
    wrapped = core.wrap(generate_secret(), household=HouseholdId("h1"), purpose="audit", key_id="a1")
    reader.put("h1", "audit", "a1", None, wrapped)
    edge = KeyRing(None, reader)
    assert await edge.public(tx_of("h1"), "content") == pub  # the public half is all an edge holds
    blob = edge.seal(pub, b"x", aad=AAD)
    with pytest.raises(KeyMissing):
        await edge.open(tx_of("h1"), blob, "k1", aad=AAD)
    with pytest.raises(KeyMissing):
        await edge.hmac(tx_of("h1"), "phone", "+91 0000 000000")
    with pytest.raises(KeyMissing):
        edge.wrap(b"x" * 32, household=HouseholdId("h1"), purpose="audit", key_id="a1")
    assert "kek" not in repr(edge).replace("kek=absent", "")
    with pytest.raises(ValueError):
        KeyRing(b"short")


async def test_audit_hmac_uses_audit_key():
    reader = MemReader()
    core = KeyRing(KEK, reader)
    provision(core, reader, "h1")
    audit_key = generate_secret()
    wrapped = core.wrap(audit_key, household=HouseholdId("h1"), purpose="audit", key_id="a1")
    reader.put("h1", "audit", "a1", None, wrapped)
    got, key_id = await core.hmac(tx_of("h1"), "phone", "+91 0000 000000")
    assert key_id == "a1"
    assert got == hmac.new(audit_key, b"phone|+91 0000 000000", hashlib.sha256).hexdigest()
    assert got != (await core.hmac(tx_of("h1"), "address", "+91 0000 000000"))[0]  # the domain separates
    # a household without an audit key, and the chain-hash-free rule: the hash is not the plain SHA-256 either
    with pytest.raises(KeyMissing):
        await core.hmac(tx_of("h2"), "phone", "x")
    assert got != hashlib.sha256(b"phone|+91 0000 000000").hexdigest()


def test_sender_hmac_is_keyed_and_channel_separated():
    k = os.urandom(32)
    a = sender_hmac(k, "whatsapp", "+91 0000 000000")
    assert a == hmac.new(k, b"whatsapp:+91 0000 000000", hashlib.sha256).hexdigest()
    assert a != sender_hmac(k, "telegram", "+91 0000 000000")
    assert a != sender_hmac(os.urandom(32), "whatsapp", "+91 0000 000000")


def test_sender_hmac_rejects_ambiguous_channel():
    k = os.urandom(32)
    with pytest.raises(ValueError):
        sender_hmac(k, "a:b", "c")  # would collide with ("a", "b:c")


async def test_hmac_rejects_ambiguous_domain():
    core = KeyRing(os.urandom(32))
    with pytest.raises(ValueError):
        await core.hmac(tx_of("h1"), "a|b", "c")  # would collide with ("a", "b|c")


async def _install_real_keys(db: PgDb, h: str, core: KeyRing) -> PublicKey:
    """Replace the placeholder content key of `seed_household` with a real wrapped pair and add an audit key."""
    pub, priv = generate_content_pair()
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute(
            "UPDATE control.household_keys SET public_key = $2, wrapped_key = $3 WHERE household_id = $1",
            h, bytes(pub), core.wrap(priv, household=HouseholdId(h), purpose="content", key_id="k1"),
        )  # fmt: skip
        await su.execute(
            "INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
            " VALUES ($1, 'audit', 'a1', $2)",
            h, core.wrap(generate_secret(), household=HouseholdId(h), purpose="audit", key_id="a1"),
        )  # fmt: skip
    finally:
        await su.close()
    return pub


def _inbox(h: str, inbox_id: str) -> NewInbox:
    msg = InboundMessage(
        source=Source.WHATSAPP, provider_message_id="wa:" + inbox_id, sender_ref="s", kind=MediaKind.TEXT,
        text=CANARY, received_at=NOW,
    )  # fmt: skip
    return NewInbox(
        id=InboxId(inbox_id), household_id=HouseholdId(h), elder_id=ElderId("e" + h), msg=msg, sender_hmac="s" + h,
        payload_sha256="x", parts=[RawPart(part="text", text=CANARY)],
    )  # fmt: skip


async def test_stored_bytes_hold_no_canary_and_only_core_reads_them(pg_db: PgDb):
    core_ring = KeyRing(KEK)
    for h in ("h1", "h2"):
        await seed_household(pg_db, h)
    pubs = {h: await _install_real_keys(pg_db, h, core_ring) for h in ("h1", "h2")}
    edge_ring = KeyRing(None)
    async with open_store(pg_db, Role.EDGE) as edge:
        async with edge.tx(household=HouseholdId("h1")) as tx:
            pub = await edge_ring.public(tx, "content")
            assert pub == pubs["h1"]
            assert await InboxRepo.insert_if_new(
                tx, _inbox("h1", "m1"), public_key=pub, sealer=edge_ring, expires_at=NOW + timedelta(days=30)
            )
        async with edge.tx(household=HouseholdId("h1")) as tx:  # the edge role holds no grant on the wrapped key
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await tx.fetch("SELECT wrapped_key FROM control.household_keys")
    su = await asyncpg.connect(pg_db.dsn())
    try:
        row = await su.fetchrow("SELECT body_sealed, key_id FROM raw_text WHERE inbox_id = 'm1'")
        dump = b"".join(bytes(r[0]) for r in await su.fetch("SELECT wrapped_key FROM control.household_keys"))
    finally:
        await su.close()
    stored = bytes(row["body_sealed"])
    assert CANARY.encode() not in stored and row["key_id"] == "k1"
    assert KEK not in dump
    async with open_store(pg_db, Role.CORE) as core:
        async with core.tx(household=HouseholdId("h1")) as tx:
            assert await core_ring.open(tx, stored, "k1", aad=b"h1|m1|text") == CANARY.encode()
            assert (await core_ring.hmac(tx, "phone", "+91 0000 000000"))[1] == "a1"
        async with core.tx(household=HouseholdId("h2")) as tx:
            with pytest.raises(ValueError):
                await core_ring.open(tx, stored, "k1", aad=b"h1|m1|text")
