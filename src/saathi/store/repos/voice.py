"""VoiceRepo (M16; D-69, D-71). `voice_clips` is shared by all households and has no household column.

The core reads; the owner role inserts generated clips and, after a manual receipt exists, sets the review columns.
A transaction still needs a non-empty scope, so callers pass `VOICE_SCOPE`; no tenant row carries it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from saathi.ids import HouseholdId
from saathi.ports import Tx
from saathi.store.repos._util import affected

VOICE_SCOPE = HouseholdId("-system-")

_COLUMNS = "reply_key, version, language, text, sha256, path, reviewed_by, reviewed_at, receipt_ref"


class ClipRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    reply_key: str
    version: int
    language: str
    text: str
    sha256: str
    path: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    receipt_ref: str | None = None

    @property
    def reviewed(self) -> bool:
        """A clip ships only with a reviewer, a review time and a receipt reference (D-71)."""
        return bool(self.reviewed_by) and self.reviewed_at is not None and bool(self.receipt_ref)


class VoiceRepo:
    @staticmethod
    async def versions(tx: Tx, reply_key: str, language: str) -> list[ClipRow]:
        """Every version of one clip, newest first."""
        rows = await tx.fetch(
            f"SELECT {_COLUMNS} FROM voice_clips WHERE reply_key = $1 AND language = $2 ORDER BY version DESC",
            reply_key,
            language,
        )
        return [ClipRow.model_validate(dict(r)) for r in rows]

    @staticmethod
    async def insert(tx: Tx, *, reply_key: str, version: int, language: str, text: str, sha256: str, path: str) -> None:
        """Owner role only. The review columns stay null: a new clip is unreviewed until a receipt exists."""
        await tx.execute(
            "INSERT INTO voice_clips (reply_key, version, language, text, sha256, path)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            reply_key,
            version,
            language,
            text,
            sha256,
            path,
        )

    @staticmethod
    async def review(
        tx: Tx, *, reply_key: str, version: int, language: str, reviewer: str, at: datetime, receipt_ref: str
    ) -> bool:
        """Owner role only, after the dated manual receipt exists. False when the version is absent."""
        tag = await tx.execute(
            "UPDATE voice_clips SET reviewed_by = $4, reviewed_at = $5, receipt_ref = $6"
            " WHERE reply_key = $1 AND version = $2 AND language = $3",
            reply_key,
            version,
            language,
            reviewer,
            at,
            receipt_ref,
        )
        return affected(tag) == 1
