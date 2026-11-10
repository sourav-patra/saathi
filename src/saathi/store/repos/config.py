"""ConfigRepo (M06; RQ-110). Versioned household rows: the core and scan roles read, only the owner role writes."""

from __future__ import annotations

from pydantic import ValidationError

from saathi.householdcfg import ConfigMissing, HouseholdConfig
from saathi.ports import Tx
from saathi.store.repos._util import affected, json_value


class ConfigRepo:
    @staticmethod
    async def load(tx: Tx) -> HouseholdConfig:
        """The newest version of this household. `ConfigMissing` when there is no row or it does not validate:
        a value that cannot be read is never replaced by a default."""
        rows = await tx.fetch(
            "SELECT version, values FROM control.household_config WHERE household_id = $1"
            " ORDER BY version DESC LIMIT 1",
            tx.household,
        )
        if not rows:
            raise ConfigMissing()
        values = json_value(rows[0]["values"])
        if not isinstance(values, dict):
            raise ConfigMissing()
        try:
            return HouseholdConfig.model_validate({**values, "version": int(rows[0]["version"])})
        except ValidationError as exc:
            raise ConfigMissing() from exc

    @staticmethod
    async def append(tx: Tx, cfg: HouseholdConfig, *, created_by: str) -> bool:
        """Owner role only. A new row for `cfg.version`; False when that version exists."""
        values = cfg.model_dump(mode="json", exclude={"version"})
        tag = await tx.execute(
            "INSERT INTO control.household_config (household_id, version, values, created_by)"
            " VALUES ($1, $2, $3::jsonb, $4) ON CONFLICT DO NOTHING",
            tx.household,
            cfg.version,
            values,
            created_by,
        )
        return affected(tag) == 1
