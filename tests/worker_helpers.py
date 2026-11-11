"""Helpers for the worker tests: a household with real keys, config and consents, inbox rows, and a `WorkerDeps`.

Everything is synthetic. Rows are seeded as the superuser (bypassing RLS); the code under test runs as the core role.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from conftest import PgDb
from fakes import (
    FakeASR,
    FakeAudioCodec,
    FakeAudit,
    FakeCaregiverChannel,
    FakeClassifier,
    FakeElderChannel,
    FakeGateway,
    FakeMediaStore,
    FakeVoiceStore,
)
from store_helpers import NOW, open_store, seed_household

from saathi.domain import MediaBlob
from saathi.householdcfg import ApproverEntry, HouseholdConfig
from saathi.ids import HouseholdId, PublicKey
from saathi.store import Role, Store
from saathi.store.keys import KeyRing, generate_content_pair
from saathi.store.repos._util import aad
from saathi.worker import WorkerConfig, WorkerDeps

KEK = os.urandom(32)
CANARY_PAN = "ABCDE1234F"  # a synthetic PAN-shaped value; the redactor must remove it before any model


@dataclass
class Rig:
    """The fakes behind one `WorkerDeps`."""

    elder: FakeElderChannel = field(default_factory=FakeElderChannel)
    caregiver: FakeCaregiverChannel = field(default_factory=FakeCaregiverChannel)
    voice: FakeVoiceStore = field(default_factory=FakeVoiceStore)
    asr: FakeASR = field(default_factory=FakeASR)
    codec: FakeAudioCodec = field(default_factory=FakeAudioCodec)
    media: FakeMediaStore = field(default_factory=FakeMediaStore)
    audit: FakeAudit = field(default_factory=FakeAudit)
    gateway: FakeGateway = field(default_factory=FakeGateway)
    classifier: FakeClassifier | None = None
    owner_alerts: list[str] = field(default_factory=list)

    async def owner_alert(self, what: str) -> None:
        self.owner_alerts.append(what)

    def deps(self, store: Store, keys: KeyRing, clock: Any, cfg: WorkerConfig | None = None) -> WorkerDeps:
        return WorkerDeps(
            store=store, keys=keys, elder=self.elder, caregiver=self.caregiver, voice=self.voice, asr=self.asr,
            codec=self.codec, media=self.media, audit=self.audit, clock=clock, owner_alert=self.owner_alert,
            classifier=self.classifier, gateway=self.gateway, cfg=cfg or WorkerConfig(),
        )  # fmt: skip


def household_config(*approvers: str, version: int = 1) -> HouseholdConfig:
    roles = ("primary", "backup")
    return HouseholdConfig(
        version=version,
        approvers=[ApproverEntry(approver_id=a, role=roles[min(i, 1)]) for i, a in enumerate(approvers or ("ap1",))],
        elder_channel_active="whatsapp",
        channels_enabled=frozenset({"whatsapp"}),
        helper_name="Helper",
    )


async def provision(
    db: PgDb,
    h: str = "h1",
    *,
    consents: bool = True,
    approvers: tuple[str, ...] = ("ap1",),
    keys: KeyRing | None = None,
) -> tuple[KeyRing, PublicKey]:
    """A household with a real content key (wrapped under the test key-encryption key), a config row and consents."""
    await seed_household(db, h)
    ring = keys or KeyRing(KEK)
    pub, priv = generate_content_pair()
    wrapped = ring.wrap(priv, household=HouseholdId(h), purpose="content", key_id="k1")
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute(
            "UPDATE control.household_keys SET public_key = $2, wrapped_key = $3 WHERE household_id = $1",
            h,
            bytes(pub),
            wrapped,
        )
        cfg = household_config(*(f"{a}{h}" for a in approvers))
        await su.execute(
            "INSERT INTO control.household_config (household_id, version, values, created_by)"
            " VALUES ($1, 1, $2::jsonb, 'test')",
            h,
            cfg.model_dump_json(exclude={"version"}),
        )
        if consents:
            await grant(su, h, "transcription", "local_models")
            await grant(su, h, "alert_audio", "telegram_caregiver")
    finally:
        await su.close()
    return ring, pub


async def grant(su: asyncpg.Connection, h: str, use: str, recipient: str, *, event: str = "grant") -> None:
    await su.execute(
        "INSERT INTO control.consent_events (household_id, subject, use, recipient, event, capacity, language, how,"
        " notice_text_sha256, recorded_by) VALUES ($1, 'elder', $2, $3, $4, 'self', 'hi', 'spoken', 'sha', 'test')",
        h, use, recipient, event,
    )  # fmt: skip


async def add_inbox(
    db: PgDb,
    ring: KeyRing,
    pub: PublicKey,
    h: str,
    inbox_id: str,
    *,
    kind: str = "text",
    text: str | None = None,
    caption: str | None = None,
    received_at: datetime = NOW,
    media_ref: str | None = None,
    not_before: datetime | None = None,
    state: str = "received",
) -> None:
    su = await asyncpg.connect(db.dsn())
    try:
        await su.execute(
            "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
            " payload_sha256, media_ref, media_mime, state, received_at, not_before)"
            " VALUES ($1, $2, $3, 'whatsapp', $4, $5, $6, 'sha', $7, $8, $9, $10, $11)",
            inbox_id, h, "wa:" + inbox_id, "hmac-" + h, "e" + h, kind, media_ref,
            "audio/ogg" if media_ref else None, state, received_at, not_before,
        )  # fmt: skip
        for part, body in (("text", text), ("caption", caption)):
            if body is None:
                continue
            await su.execute(
                "INSERT INTO raw_text (inbox_id, household_id, part, body_sealed, key_id, expires_at)"
                " VALUES ($1, $2, $3, $4, 'k1', $5)",
                inbox_id,
                h,
                part,
                ring.seal(pub, body.encode(), aad=aad(h, inbox_id, part)),
                received_at + timedelta(days=30),
            )
    finally:
        await su.close()


def audio(rig: Rig, handle: str = "1001", data: bytes = b"synthetic-opus") -> str:
    """Put a voice note where the fake elder channel can fetch it; returns the handle for `add_inbox`."""
    rig.elder.media[handle] = MediaBlob(data=data, mime="audio/ogg")
    return handle


async def rows(db: PgDb, sql: str, *args: Any) -> list[asyncpg.Record]:
    su = await asyncpg.connect(db.dsn())
    try:
        return await su.fetch(sql, *args)
    finally:
        await su.close()


__all__ = [
    "CANARY_PAN", "KEK", "NOW", "HouseholdId", "Rig", "Role", "add_inbox", "audio", "grant", "household_config",
    "open_store", "provision", "rows",
]  # fmt: skip
