"""The fixed clip job (M16; D-71): a job run by the owner, not a service.

It writes `<reply_key>.v<n>.ogg` (`<reply_key>.v<n>.<language>.ogg` for a language other than Hindi) and inserts a
`voice_clips` row with the review columns null. A clip ships only after the caregiver has listened to it and a manual
receipt exists; `saathi-admin clips review` then sets the review columns. The job never sets them.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from saathi.domain import VoiceClip, WavBlob
from saathi.ids import ReplyKey as ReplyKeyId
from saathi.ids import VoiceId
from saathi.ports import TTS, AudioCodec, Store
from saathi.replies import reply_text
from saathi.store.repos.voice import VOICE_SCOPE, VoiceRepo
from saathi.voice.store import ReplyKey

FIXED_VOICE = VoiceId("fixed")
MAX_CLIP_SECONDS = 30.0


def clip_filename(key: ReplyKey, version: int, language: str) -> str:
    infix = "" if language == "hi" else f".{language}"
    return f"{key.value}.v{version}{infix}.ogg"


async def generate_fixed(
    owner: Store, tts: TTS, codec: AudioCodec, out_dir: Path, *, language: str = "hi"
) -> list[VoiceClip]:
    """Make a clip for each reply key whose newest version has other text (or none). Returns the new clips.

    A key whose newest stored text is the current text is left alone, so a second run does not make new versions
    that would each need a new review.
    """
    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    made: list[VoiceClip] = []
    for key in ReplyKey:
        text = reply_text(key, language)
        async with owner.tx(household=VOICE_SCOPE) as tx:
            versions = await VoiceRepo.versions(tx, key.value, language)
        if versions and versions[0].text == text:
            continue
        version = versions[0].version + 1 if versions else 1
        spoken = await tts.synthesize(text, voice=FIXED_VOICE, max_seconds=MAX_CLIP_SECONDS)
        encoded = await codec.encode_clip(WavBlob(data=spoken.data))
        digest = hashlib.sha256(encoded.data).hexdigest()
        name = clip_filename(key, version, language)
        await asyncio.to_thread((out_dir / name).write_bytes, encoded.data)
        async with owner.tx(household=VOICE_SCOPE) as tx:
            await VoiceRepo.insert(
                tx, reply_key=key.value, version=version, language=language, text=text, sha256=digest, path=name
            )
        made.append(
            VoiceClip(key=ReplyKeyId(key.value), language=language, data=encoded.data, sha256=digest, version=version)
        )
    return made
