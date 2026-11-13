"""Forward-only SQL migrator (M27, RQ-38, 109).

Applies ``NNN_*.sql`` files from a directory in order, each inside one transaction, and records
``(version, sha256, applied_at)`` in ``schema_migrations``. A file that was applied and has since changed is refused.
Connect as ``saathi_migrator`` (never a superuser): no migration creates a role, the init script does.
asyncpg only; this module is one of the few places allowed to hold a raw connection (LLD intake part, M04).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
from pathlib import Path

import asyncpg

_FILE_RE = re.compile(r"^(\d{3})_[A-Za-z0-9_]+\.sql$")
_LOCK_KEY = 0x5A47_4D49  # arbitrary constant: one migrator at a time per database

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  sha256 text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now())
"""


class MigrationError(Exception):
    """The migration set is unusable: a misnamed or duplicate file, or an applied file changed, renamed or missing."""


def discover(directory: Path) -> list[tuple[str, Path]]:
    """Return ``(version, path)`` for every ``*.sql`` in ``directory``, in version order.

    Every ``*.sql`` must match ``NNN_name.sql``: a file the pattern would skip silently is an error instead.
    """
    found: list[tuple[str, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        if _FILE_RE.match(path.name) is None:
            raise MigrationError(f"{path.name}: not named NNN_name.sql")
        found.append((path.stem, path))
    versions = [v[:3] for v, _ in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration number in {directory}")
    return found


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def migrate(dsn: str, dir: Path) -> list[str]:  # noqa: A002 (name fixed by the LLD signature)
    """Apply every pending file in ``dir``; return the versions applied by this call, in order."""
    files = discover(Path(dir))
    contents = {version: path.read_bytes() for version, path in files}  # each file is read once: hashed and run as is
    conn = await asyncpg.connect(dsn)
    applied_now: list[str] = []
    try:
        if await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
            raise MigrationError("refusing to migrate as a superuser: connect as saathi_migrator")
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        try:
            await conn.execute(_CREATE_TABLE)
            rows = await conn.fetch("SELECT version, sha256 FROM schema_migrations")
            done = {r["version"][:3]: (r["version"], r["sha256"]) for r in rows}  # keyed by the NNN prefix
            by_number = {v[:3]: v for v in contents}
            # Check every applied file first so a changed, renamed or missing file is refused before anything runs.
            for number, (version, sha) in sorted(done.items()):
                if number not in by_number:
                    raise MigrationError(f"{version}: applied, but its file is missing")
                if by_number[number] != version:
                    raise MigrationError(f"{version}: applied under that name, the file is now {by_number[number]}")
                if sha != sha256_of(contents[version]):
                    raise MigrationError(f"{version}: file changed after it was applied")
            for version, _ in files:
                if version[:3] in done:
                    continue
                data = contents[version]
                async with conn.transaction():
                    await conn.execute(data.decode("utf-8"))
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, sha256) VALUES ($1, $2)", version, sha256_of(data)
                    )
                applied_now.append(version)
        finally:
            try:  # never let a failed unlock hide the error that is already on its way out
                await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
            except Exception:  # noqa: BLE001
                pass
    finally:
        await conn.close()
    return applied_now


def main() -> None:
    """``saathi-migrate``: DSN from SAATHI_MIGRATE_DSN, directory from SAATHI_SCHEMA_DIR (default infra/sql/schema)."""
    dsn = os.environ.get("SAATHI_MIGRATE_DSN")
    if not dsn:
        sys.exit("SAATHI_MIGRATE_DSN is not set (a saathi_migrator DSN)")
    directory = Path(os.environ.get("SAATHI_SCHEMA_DIR", "infra/sql/schema"))
    # A missing or empty directory would "succeed" on an empty schema and release edge and worker (review U8 F1).
    if not directory.is_dir() or not any(directory.glob("*.sql")):
        sys.exit(f"{directory}: no migration files (is the schema directory mounted?)")
    for version in asyncio.run(migrate(dsn, directory)):
        print(f"applied {version}")


if __name__ == "__main__":
    main()
