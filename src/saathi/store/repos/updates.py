"""UpdateRepo: the Telegram replay guard (M04; RQ-97). The primary key is (bot, update_id): ids repeat across bots."""

from __future__ import annotations

from typing import Literal

from saathi.ports import Tx
from saathi.store.repos._util import affected


class UpdateRepo:
    @staticmethod
    async def first_seen(tx: Tx, bot: Literal["caregiver", "elder"], update_id: int, kind: str) -> bool:
        """True the first time an update id is seen for this bot, False on every repeat."""
        tag = await tx.execute(
            "INSERT INTO tg_updates (bot, update_id, household_id, kind) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT DO NOTHING",
            bot,
            update_id,
            tx.household,
            kind,
        )
        return affected(tag) == 1
