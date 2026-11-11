"""PgVoiceStore (M16; D-69, D-71): fixed clips from disk, checked against the stored hash and the caregiver's review."""

from __future__ import annotations

import asyncio
import hashlib
from enum import StrEnum
from pathlib import Path

from saathi.domain import VoiceClip
from saathi.errors import ClipHashMismatch, ClipMissing, ClipUnreviewed
from saathi.ids import ReplyKey as ReplyKeyId
from saathi.ports import Store
from saathi.store.repos.voice import VOICE_SCOPE, ClipRow, VoiceRepo


class ReplyKey(StrEnum):
    EMERGENCY = "emergency"
    SCAM = "scam"
    ACK = "ack"
    TOLD_CAREGIVER = "told_caregiver"
    NOT_UNDERSTOOD = "not_understood"
    EXPIRED = "expired"
    CHANNEL_DOWN = "channel_down"


def _read(root: Path, name: str) -> bytes:
    """The file of a clip row. A path that leaves the clip folder is treated as a missing file."""
    base = root.resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise ClipMissing()
    try:
        return target.read_bytes()
    except OSError as exc:
        raise ClipMissing() from exc


class PgVoiceStore:
    """Implements `ports.VoiceStore`. `fixed` returns the newest reviewed version and never an unreviewed one."""

    def __init__(self, store: Store, clips_dir: Path) -> None:
        self._store = store
        self._dir = clips_dir

    async def fixed(self, key: ReplyKey | str, language: str = "hi") -> VoiceClip:
        async with self._store.tx(household=VOICE_SCOPE) as tx:
            rows = await VoiceRepo.versions(tx, str(key), language)
        if not rows:
            raise ClipMissing()
        row: ClipRow | None = next((r for r in rows if r.reviewed), None)
        if row is None:
            raise ClipUnreviewed()
        data = await asyncio.to_thread(_read, self._dir, row.path)
        if hashlib.sha256(data).hexdigest() != row.sha256:
            raise ClipHashMismatch()
        return VoiceClip(
            key=ReplyKeyId(row.reply_key), language=row.language, data=data, sha256=row.sha256, version=row.version
        )
