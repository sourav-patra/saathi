"""Helpers for the edge tests: synthetic keys and senders, a fake store with scan functions, a real-database seed and
an in-process client. Everything here is synthetic."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
from conftest import PgDb
from fakes import FakeClock, FakeStore
from fakes.store import FakeTx
from store_helpers import open_store

from saathi.config import Settings
from saathi.domain import Source
from saathi.edge import TelegramElderInbound, WhatsAppInbound, create_edge_app
from saathi.edge.handler import EdgeCounters
from saathi.guards.radar import scan
from saathi.ids import ElderId, HouseholdId
from saathi.store import Role
from saathi.store.keys import KeyRing, generate_content_pair, sender_hmac

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
SENDER_KEY = hashlib.sha256(b"synthetic sender key").digest()
WA_SECRET = "synthetic-app-secret"
TG_SECRET = "synthetic-tg-secret"
WA_PATH, TG_PATH = "wa-path-x", "tg-path-x"
ELDER_NUMBER = "+910000000001"
STRANGER_NUMBER = "+910000000009"
ELDER_CHAT = "4242"
H = HouseholdId("h1")
E = ElderId("eh1")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        wa_app_secret=WA_SECRET,  # type: ignore[arg-type]
        wa_verify_token="synthetic-verify",  # type: ignore[arg-type]
        wa_webhook_path=WA_PATH,
        tg_elder_secret=TG_SECRET,  # type: ignore[arg-type]
        tg_elder_webhook_path=TG_PATH,
    )


def adapters() -> dict:
    return {Source.WHATSAPP: WhatsAppInbound(WA_SECRET), Source.TELEGRAM_ELDER: TelegramElderInbound(TG_SECRET)}


def wa_sign(body: bytes, secret: str = WA_SECRET) -> dict[str, str]:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def wa_body(*messages: dict[str, Any]) -> bytes:
    return json.dumps({"entry": [{"changes": [{"value": {"messages": list(messages)}}]}]}).encode()


def wa_msg(mtype: str = "text", *, mid: str = "wamid.1", sender: str = ELDER_NUMBER.lstrip("+"), **body: Any) -> dict:
    if mtype == "text" and "text" not in body:
        body["text"] = {"body": "namaste"}
    return {"from": sender, "id": mid, "timestamp": "1767268800", "type": mtype, **body}


def tg_body(text: str = "hello", *, message_id: int = 1, chat: str = ELDER_CHAT) -> bytes:
    msg = {"message_id": message_id, "date": 1767268800, "chat": {"id": int(chat), "type": "private"}, "text": text}
    return json.dumps({"update_id": 1, "message": msg}).encode()


def tg_headers(secret: str = TG_SECRET) -> dict[str, str]:
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


class FakeScan:
    """The two security-definer calls the edge makes, with a fixed set of known senders."""

    def __init__(self, known: dict[str, tuple[HouseholdId, ElderId]]) -> None:
        self.known = known
        self.resolved: list[str] = []
        self.rejected: list[tuple[str, str]] = []

    async def resolve_sender(self, hmac_value: str):
        self.resolved.append(hmac_value)
        return self.known.get(hmac_value)

    async def reject_inbound(self, sender_hmac_value: str, reason: str) -> None:
        self.rejected.append((sender_hmac_value, reason))


class EdgeFakeStore(FakeStore):
    """A FakeStore with scan functions. Each fetch in a transaction returns the same one row."""

    def __init__(self, *, not_before: datetime | None = None) -> None:
        super().__init__(FakeClock(NOW))
        self.not_before = not_before
        self.scan = FakeScan({sender_hmac(SENDER_KEY, "whatsapp", ELDER_NUMBER): (H, E)})

    @asynccontextmanager
    async def tx(self, *, household: HouseholdId, lock_household: bool = False) -> AsyncIterator[FakeTx]:
        async with super().tx(household=household, lock_household=lock_household) as tx:
            tx.rows = [{"not_before": self.not_before, "key_id": "k1"}]
            yield tx  # type: ignore[misc]

    def statements(self, needle: str) -> list[tuple[str, tuple]]:
        return [s for tx in self.transactions for s in tx.statements if needle in s[0]]


class PublicOnly:
    """A key reader that holds one synthetic public key and no private one."""

    def __init__(self, public: bytes) -> None:
        self.public = public

    async def active_public(self, tx, purpose):
        return "k1", self.public

    async def wrapped(self, tx, purpose, key_id):
        return None


def fake_app(store: EdgeFakeStore, *, radar=scan, max_body: int | None = None, counters: EdgeCounters | None = None):
    pub, _ = generate_content_pair()
    kwargs: dict[str, Any] = {} if max_body is None else {"max_body": max_body}
    return create_edge_app(
        settings(), store, KeyRing(kek=None, reader=PublicOnly(bytes(pub))), adapters(), radar, store.clock,  # type: ignore[arg-type]
        sender_key=SENDER_KEY, counters=counters, **kwargs,
    )  # fmt: skip


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://edge")


async def seed_edge_household(
    db: PgDb, *, with_key: bool = True, rate: tuple[int, int] | None = None
) -> tuple[bytes, bytes]:
    """One household, one elder, a WhatsApp and a Telegram identity and a content key; returns the key pair."""
    pub, priv = generate_content_pair()
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute("INSERT INTO households (id, name) VALUES ($1, 'Household')", H)
        await su.execute("INSERT INTO elders (id, household_id, display_name) VALUES ($1, $2, 'Elder')", E, H)
        for n, (channel, sender) in enumerate((("whatsapp", ELDER_NUMBER), ("telegram_elder", ELDER_CHAT))):
            await su.execute(
                "INSERT INTO elder_identities (id, household_id, elder_id, channel, sender_hmac, person_ref,"
                " person_ref_enc, key_id) VALUES ($1, $2, $3, $4, $5, $6, '\\x00', 'k1')",
                f"i{n}", H, E, channel, sender_hmac(SENDER_KEY, channel, sender), f"ref{n}",
            )  # fmt: skip
        if with_key:
            await su.execute(
                "INSERT INTO control.household_keys (household_id, purpose, key_id, public_key, wrapped_key)"
                " VALUES ($1, 'content', 'k1', $2, '\\x00')",
                H, bytes(pub),
            )  # fmt: skip
        if rate:
            await su.execute(
                "INSERT INTO control.household_config (household_id, version, values, created_by)"
                " VALUES ($1, 1, $2, 'owner')",
                H, json.dumps({"rate_limit_msgs": rate[0], "rate_limit_window_s": rate[1]}),
            )  # fmt: skip
    finally:
        await su.close()
    return bytes(pub), priv


@asynccontextmanager
async def pg_edge(db: PgDb, *, radar=scan, clock: FakeClock | None = None) -> AsyncIterator[httpx.AsyncClient]:
    """The edge app on a real database as the edge role, driven in process."""
    clock = clock or FakeClock(NOW)
    async with open_store(db, Role.EDGE, clock=clock) as store:
        app = create_edge_app(
            settings(), store, KeyRing(kek=None), adapters(), radar, clock, sender_key=SENDER_KEY  # type: ignore[arg-type]
        )  # fmt: skip
        async with client(app) as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


async def su_fetch(db: PgDb, sql: str, *args: Any) -> list[asyncpg.Record]:
    su = await asyncpg.connect(db.dsn())
    try:
        return await su.fetch(sql, *args)
    finally:
        await su.close()


def wa_url() -> str:
    return f"/wa/{WA_PATH}"


def tg_url() -> str:
    return f"/tg/elder/{TG_PATH}"


