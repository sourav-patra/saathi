"""SignoffRepo (M06; RQ-95a, PL-26). `control.signoffs` is written only by the owner role; the core reads it.

An expired or revoked row behaves as off. Each use of a sign-off writes an audit row naming `SignoffState.id`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from saathi.ports import Tx
from saathi.store.repos._util import affected

Scope = Literal["SO-1"]


class SignoffState(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    id: int | None = None
    expires_at: datetime | None = None
    revoked: bool = False


class SignoffRepo:
    @staticmethod
    async def valid(tx: Tx, scope: Scope, now: datetime) -> SignoffState:
        """The newest row of this scope decides. No row is off; revoked or expired is off."""
        rows = await tx.fetch(
            "SELECT id, expires_at, revoked_at FROM control.signoffs WHERE household_id = $1 AND scope = $2"
            " ORDER BY id DESC LIMIT 1",
            tx.household,
            scope,
        )
        if not rows:
            return SignoffState(valid=False)
        r = rows[0]
        revoked = r["revoked_at"] is not None
        return SignoffState(
            valid=not revoked and r["expires_at"] > now, id=int(r["id"]), expires_at=r["expires_at"], revoked=revoked
        )

    @staticmethod
    async def grant(tx: Tx, scope: Scope, *, granter: str, expires_at: datetime) -> int:
        """Owner role only. Returns the new sign-off id."""
        rows = await tx.fetch(
            "INSERT INTO control.signoffs (household_id, scope, granter, granted_at, expires_at)"
            " VALUES ($1, $2, $3, $4, $5) RETURNING id",
            tx.household,
            scope,
            granter,
            tx.now,
            expires_at,
        )
        return int(rows[0]["id"])

    @staticmethod
    async def revoke(tx: Tx, signoff_id: int, now: datetime) -> bool:
        """Owner role only. False when the row is absent or already revoked."""
        tag = await tx.execute(
            "UPDATE control.signoffs SET revoked_at = $3 WHERE household_id = $1 AND id = $2 AND revoked_at IS NULL",
            tx.household,
            signoff_id,
            now,
        )
        return affected(tag) == 1
