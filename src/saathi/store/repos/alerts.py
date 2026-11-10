"""AlertRepo (M04; RQ-18, RQ-23). Compare-and-set updates: False when the row is not in the expected state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Literal

from saathi.domain import AlertOpen
from saathi.ids import ApproverId, ElderId, EventId, InboxId, derive_id
from saathi.ports import Tx
from saathi.states import ALERT, Machine
from saathi.store.repos._util import affected

_MACHINE = Machine(ALERT, phase=0)
FOLLOWUP_S = 900  # TX-A3: a follow-up is due 15 minutes after an acknowledgement
_STAGES = ("a", "b", "c")


class AlertRepo:
    @staticmethod
    async def open(
        tx: Tx, inbox_id: InboxId, elder_id: ElderId, a: AlertOpen, t0: datetime, audio_ref: str | None
    ) -> bool:
        """A01 or A11. The unique (household, inbox, kind) means the edge and the worker cannot both open one."""
        tag = await tx.execute(
            "INSERT INTO alert_events (id, household_id, elder_id, inbox_id, kind, origin, rule_ids, t0, next_due_at,"
            " audio_ref) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, $9) ON CONFLICT DO NOTHING",
            derive_id("al", f"{inbox_id}_{a.kind}"),
            tx.household,
            elder_id,
            inbox_id,
            a.kind,
            a.origin,
            list(a.rule_ids),
            t0,
            audio_ref,
        )
        return affected(tag) == 1

    @staticmethod
    async def due(tx: Tx, event_id: EventId) -> Mapping[str, Any] | None:
        """The event row if it is open and due at the transaction time, else None.
        (`domain.AlertView` has no fields yet, so the row is returned as it is.)"""
        rows = await tx.fetch(
            "SELECT * FROM alert_events WHERE id = $1 AND state = 'open' AND next_due_at <= $2", event_id, tx.now
        )
        return dict(rows[0]) if rows else None

    @staticmethod
    async def add_attempt(
        tx: Tx, event_id: EventId, stage: str, approver: ApproverId, channel: str, idem_key: str, due_at: datetime
    ) -> bool:
        """One row per idempotency key: a repeat returns False."""
        tag = await tx.execute(
            "INSERT INTO alert_attempts (household_id, event_id, stage, approver_id, channel, idem_key, due_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (household_id, idem_key) DO NOTHING",
            tx.household,
            event_id,
            stage,
            approver,
            channel,
            idem_key,
            due_at,
        )
        return affected(tag) == 1

    @staticmethod
    async def mark_attempt(
        tx: Tx, idem_key: str, status: str, *, sent_at: datetime | None = None, provider_ref_hmac: str | None = None
    ) -> bool:
        tag = await tx.execute(
            "UPDATE alert_attempts SET status = $2, sent_at = coalesce($3, sent_at),"
            " provider_ref_hmac = coalesce($4, provider_ref_hmac) WHERE idem_key = $1",
            idem_key,
            status,
            sent_at,
            provider_ref_hmac,
        )
        return affected(tag) == 1

    @staticmethod
    async def ack(tx: Tx, event_id: EventId, approver: ApproverId, via: Literal["tap", "dtmf"]) -> bool:
        """A06 (tap) or A12 (dtmf, stage c only): compare-and-set from `open`, cancel `due` attempts, set follow-up."""
        event = "acknowledge_tap" if via == "tap" else "key_press_1"
        row = _MACHINE.lookup("open", event)
        if row is None:
            return False
        tag = await tx.execute(
            "UPDATE alert_events SET state = $2, acked_by = $3, acked_at = $4, followup_due_at = $5"
            " WHERE id = $1 AND state = 'open' AND ($6::text IS NULL OR stage = $6)",
            event_id,
            row.to,
            approver,
            tx.now,
            tx.now + timedelta(seconds=FOLLOWUP_S),
            row.from_stage,
        )
        if affected(tag) != 1:
            return False
        await tx.execute(
            "UPDATE alert_attempts SET status = 'cancelled' WHERE event_id = $1 AND status = 'due'", event_id
        )
        return True

    @staticmethod
    async def set_stage(tx: Tx, event_id: EventId, stage: Literal["a", "b", "c"]) -> bool:
        """A03, A04: stages only move forward, and only while the event is open."""
        if stage not in _STAGES:
            raise ValueError(f"unknown alert stage {stage!r}")
        tag = await tx.execute(
            "UPDATE alert_events SET stage = $2 WHERE id = $1 AND state = 'open' AND stage < $2", event_id, stage
        )
        return affected(tag) == 1

    @staticmethod
    async def set_next_due(tx: Tx, event_id: EventId, when: datetime | None) -> bool:
        tag = await tx.execute(
            "UPDATE alert_events SET next_due_at = $2 WHERE id = $1 AND state = 'open'", event_id, when
        )
        return affected(tag) == 1

    @staticmethod
    async def false_alarm(tx: Tx, event_id: EventId) -> bool:
        """A08: a flag on an acknowledged event; the state does not change."""
        tag = await tx.execute(
            "UPDATE alert_events SET false_alarm = true WHERE id = $1 AND state = 'acknowledged'", event_id
        )
        return affected(tag) == 1

    @staticmethod
    async def block(tx: Tx, event_id: EventId, reason: str) -> bool:
        """A07: every cap spent. Only the reason the table lists is accepted."""
        row = _MACHINE.lookup("open", "every_cap_spent")
        if row is None or reason not in row.reasons:
            return False
        tag = await tx.execute(
            "UPDATE alert_events SET state = 'blocked', blocked_reason = $2, next_due_at = NULL"
            " WHERE id = $1 AND state = 'open'",
            event_id,
            reason,
        )
        return affected(tag) == 1
