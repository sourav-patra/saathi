"""PgAuditSink (M17; RQ-56a, RQ-95a, PL-07): the only writer of `audit_log`.

The role that runs this holds no INSERT on the table: it calls `control.audit_append`, a security-definer function
that sets the household from the transaction. Identifiers and low-entropy values are stored as keyed hashes under the
household `audit` key, never as text. Any failure raises `AuditUnavailable`; a caller that needs the record (a denial,
a sign-off use, a raw-text view) fails closed and a caller that does not may carry on, but nothing is dropped quietly.
"""

from __future__ import annotations

import asyncio
import json

from saathi.domain import AuditEvent, AuditRef
from saathi.errors import AuditUnavailable
from saathi.ports import Tx
from saathi.store.db import Store
from saathi.store.keys import KeyRing

DEFAULT_TIMEOUT_S = 5.0


class PgAuditSink:
    """Implements `ports.AuditSink`.

    Denials and failure events must be appended with ``tx=None`` so they survive an
    aborted caller transaction; ``tx=`` audits roll back with the caller (M17).
    """

    def __init__(self, store: Store, keys: KeyRing, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._store = store
        self._keys = keys
        self._timeout_s = timeout_s

    async def append(self, ev: AuditEvent, *, tx: Tx | None = None) -> AuditRef:
        if not str(ev.run_id).strip():
            raise ValueError("an audit row needs a run id")
        clash = sorted(n for n in ev.sensitive if f"{n}_hmac" in ev.detail)
        if clash:  # a caller must never supply or overwrite a stored fingerprint
            raise ValueError(f"detail already holds a fingerprint key for: {', '.join(clash)}")
        if tx is not None:
            if tx.household != ev.household:
                raise ValueError("the audit event names a different household than the transaction")
            return await self._write(tx, ev)
        try:
            async with asyncio.timeout(self._timeout_s), self._store.tx(household=ev.household) as own:
                return await self._write(own, ev)
        except AuditUnavailable:
            raise
        except Exception as exc:  # any store, key or timeout failure: fail closed with a code, never the cause text
            raise AuditUnavailable(type(exc).__name__) from None

    async def _write(self, tx: Tx, ev: AuditEvent) -> AuditRef:
        try:
            detail: dict[str, int | bool | str] = dict(ev.detail)
            for name, raw in sorted(ev.sensitive.items()):
                hashed, _ = await self._keys.hmac(tx, f"field.{name}", raw)
                detail[f"{name}_hmac"] = hashed
            subject = None
            if ev.subject is not None:
                subject, _ = await self._keys.hmac(tx, "subject", ev.subject)
            canonical = json.dumps(
                {"event": ev.event, "subject": subject, "detail": detail}, sort_keys=True, separators=(",", ":")
            )
            payload, key_id = await self._keys.hmac(tx, "payload", canonical)
            rows = await tx.fetch(
                "SELECT control.audit_append($1, $2, $3, $4, $5, $6, $7::jsonb) AS seq",
                str(ev.run_id),
                ev.actor,
                str(ev.event),
                subject,
                payload,
                key_id,
                detail,
            )
            return AuditRef(id=str(rows[0]["seq"]))
        except AuditUnavailable:
            raise
        except Exception as exc:
            raise AuditUnavailable(type(exc).__name__) from None
