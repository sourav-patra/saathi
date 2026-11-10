"""ConsentRepo (M06; PL-05, PL-33, PL-34). `control.consent_events` is append-only: the latest event of a use wins."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from saathi.ports import Tx

Subject = Literal["elder", "caregiver"]


class ConsentEvent(BaseModel, extra="forbid"):
    """One consent record. It holds no content: `guardian_ref` is a person id, never a name."""

    subject: Subject
    use: str
    recipient: str
    event: Literal["grant", "withdraw"]
    capacity: Literal["self", "guardian", "unknown"]
    guardian_ref: str | None = None
    language: str
    how: Literal["spoken", "written", "guardian"]
    notice_text_sha256: str
    recorded_by: str

    @model_validator(mode="after")
    def _guardian_needs_ref(self) -> ConsentEvent:
        if self.capacity == "guardian" and not self.guardian_ref:
            raise ValueError("a guardian record needs guardian_ref")
        return self


class ConsentRepo:
    @staticmethod
    async def append(tx: Tx, ev: ConsentEvent) -> None:
        """Owner role only. The schema refuses a guardian row with no `guardian_ref` as well."""
        await tx.execute(
            "INSERT INTO control.consent_events (household_id, subject, use, recipient, event, capacity, guardian_ref,"
            " language, how, notice_text_sha256, recorded_by, recorded_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
            tx.household,
            ev.subject,
            ev.use,
            ev.recipient,
            ev.event,
            ev.capacity,
            ev.guardian_ref,
            ev.language,
            ev.how,
            ev.notice_text_sha256,
            ev.recorded_by,
            tx.now,
        )

    @staticmethod
    async def allowed(tx: Tx, subject: str, use: str, recipient: str) -> bool:
        """The latest event of (subject, use, recipient) decides; no row is False. Also False while the capacity of
        that subject is `unknown` (PL-34), read from the subject's newest record of any use, so a later `unknown` closes
        every use of that subject and another subject's record cannot reopen it."""
        latest = await tx.fetch(
            "SELECT capacity FROM control.consent_events WHERE household_id = $1 AND subject = $2"
            " ORDER BY id DESC LIMIT 1",
            tx.household,
            subject,
        )
        if not latest or latest[0]["capacity"] not in ("self", "guardian"):
            return False
        rows = await tx.fetch(
            "SELECT event FROM control.consent_events"
            " WHERE household_id = $1 AND subject = $2 AND use = $3 AND recipient = $4 ORDER BY id DESC LIMIT 1",
            tx.household,
            subject,
            use,
            recipient,
        )
        return bool(rows) and rows[0]["event"] == "grant"
