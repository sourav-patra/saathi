"""LT-05: Settings and HouseholdConfig (M02, M06; RQ-110; PL-13, PL-14; TQ-72). Synthetic values only."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from conftest import PgDb
from store_helpers import open_store, seed_household

from saathi.config import ConfigError, Settings, require_secrets
from saathi.householdcfg import ApproverEntry, Caps, ConfigMissing, HouseholdConfig
from saathi.ids import HouseholdId
from saathi.store import Role
from saathi.store.repos import ConfigRepo

HOUSEHOLD_VALUES = {
    "elder_phones", "tg_primary_approver_id", "tg_backup_approver_ids", "caregiver_name", "persona_name",
    "paid_monthly_cap_inr", "caps", "approvers",
}  # fmt: skip


def cfg(version: int, order: str | None = "100", *, helper: str = "Helper") -> HouseholdConfig:
    n = Decimal(order) if order else None
    caps = Caps(order_inr=n, day_inr=n * 2, monthly_soft_inr=n * 10) if n else None
    return HouseholdConfig(
        version=version, caps=caps, approvers=[ApproverEntry(approver_id="a1", role="primary")],
        elder_channel_active="whatsapp", channels_enabled=frozenset({"whatsapp"}), helper_name=helper,
    )  # fmt: skip


async def test_two_households_different_caps(pg_db: PgDb):
    for h in ("h1", "h2"):
        await seed_household(pg_db, h)
    async with open_store(pg_db, Role.OWNER) as owner:
        for h, order in (("h1", "100"), ("h2", "250")):
            async with owner.tx(household=HouseholdId(h)) as tx:
                assert await ConfigRepo.append(tx, cfg(1, order), created_by="owner")
    async with open_store(pg_db, Role.CORE) as core:
        got = {}
        for h in ("h1", "h2"):
            async with core.tx(household=HouseholdId(h)) as tx:
                got[h] = await ConfigRepo.load(tx)
    assert got["h1"].caps and got["h1"].caps.order_inr == Decimal("100")
    assert got["h2"].caps and got["h2"].caps.order_inr == Decimal("250")
    assert got["h1"].order_within_caps(Decimal("100"), Decimal(0))
    assert not got["h1"].order_within_caps(Decimal("101"), Decimal(0))
    assert got["h2"].order_within_caps(Decimal("101"), Decimal(0))


async def test_new_version_applies_next_load(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.OWNER) as owner, open_store(pg_db, Role.CORE) as core:
        async with owner.tx(household=HouseholdId("h1")) as tx:
            await ConfigRepo.append(tx, cfg(1, "100"), created_by="owner")
        async with core.tx(household=HouseholdId("h1")) as tx:
            first = await ConfigRepo.load(tx)
            async with owner.tx(household=HouseholdId("h1")) as otx:  # written while the core's step is running
                await ConfigRepo.append(otx, cfg(2, "300", helper="Renamed"), created_by="owner")
                assert not await ConfigRepo.append(otx, cfg(2, "999"), created_by="owner")  # a version never changes
            assert (await ConfigRepo.load(tx)).version == 2  # a read sees the newest committed version
        async with core.tx(household=HouseholdId("h1")) as tx:
            second = await ConfigRepo.load(tx)
    assert (first.version, second.version) == (1, 2)
    assert second.caps and second.caps.order_inr == Decimal("300") and second.helper_name == "Renamed"
    assert second.rate_limit_msgs == 20 and second.models_local_only  # defaults the row did not state


async def test_agent_role_cannot_write_config(pg_db: PgDb):
    await seed_household(pg_db, "h1")
    for role in (Role.CORE, Role.MODEL, Role.EDGE):
        async with open_store(pg_db, role) as store:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with store.tx(household=HouseholdId("h1")) as tx:
                    await ConfigRepo.append(tx, cfg(1), created_by="agent")
    su = await asyncpg.connect(pg_db.dsn())
    try:
        assert await su.fetchval("SELECT count(*) FROM control.household_config") == 0
    finally:
        await su.close()


async def test_unset_cap_denies(pg_db: PgDb):
    unset = cfg(1, None)
    assert unset.caps is None and not unset.order_within_caps(Decimal("1"), Decimal(0))
    await seed_household(pg_db, "h1")
    async with open_store(pg_db, Role.CORE) as core:
        async with core.tx(household=HouseholdId("h1")) as tx:
            with pytest.raises(ConfigMissing):  # no row: nothing to default to
                await ConfigRepo.load(tx)
    su = await asyncpg.connect(pg_db.dsn())
    try:  # a row that does not validate is no row either
        await su.execute(
            "INSERT INTO control.household_config (household_id, version, values, created_by)"
            " VALUES ('h1', 1, '{\"caps\": {\"order_inr\": \"-5\"}}', 'owner')"
        )
    finally:
        await su.close()
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=HouseholdId("h1")) as tx:
        with pytest.raises(ConfigMissing):
            await ConfigRepo.load(tx)


def test_no_household_value_read_from_environment(monkeypatch: pytest.MonkeyPatch):
    assert not HOUSEHOLD_VALUES & set(Settings.model_fields)
    for name in HOUSEHOLD_VALUES:
        monkeypatch.setenv("SAATHI_" + name.upper(), "1")
    s = Settings(_env_file=None)
    assert not any(hasattr(s, n) for n in HOUSEHOLD_VALUES)
    src = Path(__file__).resolve().parent.parent / "src" / "saathi"
    for p in src.rglob("*.py"):
        if p.name == "legacy.py":  # the old message types, removed with the last unit that imports them
            continue
        text = p.read_text()
        assert "settings.elder_phones" not in text and "get_settings().caps" not in text, p.name


def test_database_defaults_name_runtime_roles_and_carry_no_password():
    s = Settings(_env_file=None)
    for role in ("edge", "core", "model", "owner"):
        url = getattr(s, f"db_url_{role}")
        assert f"//saathi_{role}@" in url and url.endswith("/saathi")
    assert not hasattr(s, "database_url")


@pytest.mark.parametrize("secret", ["", "x" * 31])
def test_short_approval_secret_refused(secret: str):
    with pytest.raises(ConfigError) as exc:
        require_secrets(Settings(_env_file=None, approval_secret=secret))
    assert secret not in str(exc.value) or secret == ""  # the message never echoes the value


def test_approval_secret_of_32_bytes_is_accepted_from_env_or_file(tmp_path: Path):
    require_secrets(Settings(_env_file=None, approval_secret="y" * 32))
    f = tmp_path / "approval"
    f.write_bytes(os.urandom(24).hex().encode() + b"\n")  # 48 hex chars and a newline
    require_secrets(Settings(_env_file=None, approval_secret_file=str(f)))
    f.write_bytes(b"short\n")
    with pytest.raises(ConfigError):
        # the file wins over the environment value
        require_secrets(Settings(_env_file=None, approval_secret="y" * 32, approval_secret_file=str(f)))
    with pytest.raises(ConfigError):
        require_secrets(Settings(_env_file=None, approval_secret_file=str(tmp_path / "missing")))
