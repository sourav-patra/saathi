"""LocalMediaStore (M13): inbound media as files under `media_dir`, owner-only, pruned after 30 days.

A file holds an elder's voice note or photo, so it is never world readable (mode 600, directories 700), a key can
never leave the store directory, and nothing here is logged. The key `put` returns is a relative path.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from saathi.domain import MediaBlob, MediaRef
from saathi.errors import MediaExpired
from saathi.ids import HouseholdId

RETENTION_S = 30 * 24 * 3600
_FILE_MODE = 0o600
_DIR_MODE = 0o700


class LocalMediaStore:
    """Implements `ports.MediaStore`. `get` of a missing or pruned key raises `MediaExpired`."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root.resolve() not in path.parents:
            raise MediaExpired()  # a key that points outside the store is treated as absent
        return path

    @staticmethod
    def _name(ref: MediaRef) -> str:
        return hashlib.sha256(f"{ref.source.value}|{ref.handle}".encode()).hexdigest()

    async def put(self, ref: MediaRef, blob: MediaBlob, *, household: HouseholdId) -> str:
        if not household.strip() or "/" in household or household.startswith("."):
            raise ValueError("a household id cannot name a directory here")
        key = f"{household}/{self._name(ref)}"
        path = self._path(key)
        path.parent.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        mime = blob.mime.replace("\n", " ").encode()
        for suffix, data in ((".mime", mime), ("", blob.data)):
            target = path.with_name(path.name + suffix)
            tmp = target.with_name(target.name + ".part")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, _FILE_MODE)
            os.replace(tmp, target)
        return key

    async def get(self, key: str) -> MediaBlob:
        path = self._path(key)
        try:
            return MediaBlob(data=path.read_bytes(), mime=path.with_name(path.name + ".mime").read_text())
        except OSError:
            raise MediaExpired() from None

    def prune(self, *, now: float | None = None, max_age_s: float = RETENTION_S) -> int:
        """Delete files not modified for `max_age_s`; the number of media files removed."""
        cutoff = (time.time() if now is None else now) - max_age_s
        removed = 0
        for path in self._root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += path.suffix != ".mime"
        return removed
