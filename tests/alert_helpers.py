"""Helpers for the alert tests: approvers, config, an open event, and a rig with the fakes behind an `AlertService`.

Rows are seeded as the superuser (bypassing RLS); the service runs as the core role. Synthetic values only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from conftest import PgDb
from fakes import FakeAudit, FakeCaregiverChannel, FakeClock, FakeElderChannel, FakeEscalator, FakeVoiceStore
from store_helpers import NOW, open_store
from worker_helpers import provision

from saathi.alerts import AlertConfig, AlertService, TelegramStageA
from saathi.domain import MediaBlob, Source
from saathi.ids import EventId, HouseholdId, derive_id
from saathi.store import Role, Store
from saathi.store.keys import KeyRing
from saathi.store.repos import SignoffRepo
from saathi.voice.store import ReplyKey

H = "h1"
P, B = "ap1h1", "ap2h1"  # the primary and the backup of the household `h1` that `provision` writes
AUDIO_HANDLE = "voice-1"


async def add_approver(su: asyncpg.Connection, h: str, approver: str, telegram_id: int) -> None:
    await su.execute(
        "INSERT INTO approvers (id, household_id, name, telegram_user_id) VALUES ($1, $2, 'Approver', $3)"
        " ON CONFLICT DO NOTHING",
        approver, h, telegram_id,
    )  # fmt: skip


async def set_approvers(su: asyncpg.Connection, h: str, *approvers: str) -> None:
    """The household config lists these approvers, the first as primary and the rest as backups."""
    entries = [{"approver_id": a, "role": "primary" if i == 0 else "backup"} for i, a in enumerate(approvers)]
    await su.execute(
        "UPDATE control.household_config SET values = jsonb_set(values, '{approvers}', $2::jsonb)"
        " WHERE household_id = $1",
        h, json.dumps(entries),
    )  # fmt: skip


async def open_event(
    su: asyncpg.Connection,
    h: str,
    inbox: str,
    *,
    kind: str = "emergency",
    t0: datetime | None = None,
    inbox_kind: str = "text",
    media_ref: str | None = None,
    state: str = "processed",
    origin: str = "radar_edge",
) -> EventId:
    """An inbox row (settled by default, so no worker takes it) and an open alert event of `kind`, due at `t0`."""
    at = t0 or datetime.now(UTC)
    await su.execute(
        "INSERT INTO inbox (id, household_id, provider_message_id, source, sender_hmac, elder_id, kind,"
        " payload_sha256, media_ref, media_mime, state, received_at)"
        " VALUES ($1, $2, $3, 'whatsapp', $4, $5, $6, 'sha', $7, $8, $9, $10) ON CONFLICT DO NOTHING",
        inbox, h, "wa:" + inbox, "hmac-" + h, "e" + h, inbox_kind, media_ref, "audio/ogg" if media_ref else None,
        state, at,
    )  # fmt: skip
    event = derive_id("al", f"{inbox}_{kind}")
    await su.execute(
        "INSERT INTO alert_events (id, household_id, elder_id, inbox_id, kind, origin, rule_ids, t0, next_due_at,"
        " audio_ref) VALUES ($1, $2, $3, $4, $5, $6, '{}', $7, $7, NULL) ON CONFLICT DO NOTHING",
        event, h, "e" + h, inbox, kind, origin, at,
    )  # fmt: skip
    return EventId(event)


async def grant_so1(su: asyncpg.Connection, h: str, *, expires_at: datetime) -> int:
    return int(
        await su.fetchval(
            "INSERT INTO control.signoffs (household_id, scope, granter, granted_at, expires_at)"
            " VALUES ($1, 'SO-1', 'owner', $2, $3) RETURNING id",
            h, NOW - timedelta(days=1), expires_at,
        )  # fmt: skip
    )


@dataclass
class AlertRig:
    """One household, its fakes, and a way to build services on the same database and the same fake clock."""

    db: PgDb
    clock: FakeClock
    ring: KeyRing
    pub: bytes
    elder: FakeElderChannel = field(default_factory=FakeElderChannel)
    caregiver: FakeCaregiverChannel = field(default_factory=FakeCaregiverChannel)
    voice: FakeVoiceStore = field(default_factory=FakeVoiceStore)
    audit: FakeAudit = field(default_factory=FakeAudit)
    stage_b: FakeEscalator = field(default_factory=lambda: FakeEscalator("b"))
    stage_c: FakeEscalator = field(default_factory=lambda: FakeEscalator("c"))
    owner_alerts: list[str] = field(default_factory=list)
    cfg: AlertConfig | None = None
    trace: Any = None

    async def owner_alert(self, what: str) -> None:
        self.owner_alerts.append(what)

    def service(self, store: Store, *, audit: Any = None) -> AlertService:
        return AlertService(
            store, self.caregiver, {Source.WHATSAPP: self.elder}, self.voice,
            {"a": TelegramStageA(self.caregiver), "b": self.stage_b, "c": self.stage_c},
            audit or self.audit, SignoffRepo, self.clock, self.cfg, owner_alert=self.owner_alert, trace=self.trace,
        )  # fmt: skip

    async def seed(self, *, so1: bool = True) -> None:
        """The two approvers of the household, SO-1 valid for 90 days when `so1`, the emergency and scam clips."""
        su = await asyncpg.connect(self.db.dsn())
        try:
            await add_approver(su, H, P, 3001)
            await add_approver(su, H, B, 3002)
            await set_approvers(su, H, P, B)
            if so1:
                await grant_so1(su, H, expires_at=NOW + timedelta(days=90))
        finally:
            await su.close()
        for key in (ReplyKey.EMERGENCY, ReplyKey.SCAM):
            self.voice.add(key, b"synthetic-clip-" + key.value.encode(), "hi")

    async def event(self, inbox: str = "m1", kind: str = "emergency", **kw: Any) -> EventId:
        su = await asyncpg.connect(self.db.dsn())
        try:
            return await open_event(su, H, inbox, kind=kind, t0=self.clock.now(), **kw)
        finally:
            await su.close()

    async def run(self, svc: AlertService, seconds: float, *, every: float = 20) -> None:
        """Advance the fake clock `seconds`, ticking the service every `every` seconds and at the end."""
        left = seconds
        while left > 0:
            step = min(every, left)
            await self.clock.advance(step)
            await svc.tick()
            left -= step

    async def sql(self, query: str, *args: Any) -> list[asyncpg.Record]:
        su = await asyncpg.connect(self.db.dsn())
        try:
            return await su.fetch(query, *args)
        finally:
            await su.close()

    async def state(self, event: EventId) -> asyncpg.Record:
        [row] = await self.sql(
            "SELECT state, stage, acked_by, blocked_reason, false_alarm, next_due_at, last_elder_clip_at"
            " FROM alert_events WHERE id = $1",
            event,
        )
        return row

    async def attempts(self, event: EventId, stage: str | None = None) -> list[asyncpg.Record]:
        return await self.sql(
            "SELECT stage, approver_id, idem_key, status, due_at FROM alert_attempts WHERE event_id = $1"
            " AND ($2::text IS NULL OR stage = $2) AND channel NOT IN ('signoff', 'notice') ORDER BY due_at, id",
            event, stage,
        )  # fmt: skip

    def cards(self, approver: str | None = None) -> list[tuple[str, object, str]]:
        """(approver, card, key) of every card the caregiver channel took."""
        return [(str(to), p, str(k)) for m, to, p, k in self.caregiver.sent if m == "send_card" and (
            approver is None or to == approver)]  # fmt: skip


async def make_rig(db: PgDb, *, so1: bool = True, consents: bool = True, approvers: tuple[str, ...] = ("ap1", "ap2")):
    ring, pub = await provision(db, H, consents=consents, approvers=approvers)
    rig = AlertRig(db=db, clock=FakeClock(NOW), ring=ring, pub=bytes(pub))
    await rig.seed(so1=so1)
    return rig


def blob(data: bytes = b"synthetic-opus") -> MediaBlob:
    return MediaBlob(data=data, mime="audio/ogg")


__all__ = [
    "AUDIO_HANDLE", "B", "H", "NOW", "P", "AlertRig", "HouseholdId", "Role", "add_approver", "blob", "grant_so1",
    "make_rig", "open_event", "open_store", "set_approvers",
]  # fmt: skip
