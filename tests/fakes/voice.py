"""Fakes for ASR, the audio codec, TTS and the fixed-clip voice store (the store checks the clip hash)."""

from __future__ import annotations

import hashlib
from collections import deque

from fakes.base import Failable
from saathi.domain import AudioBlob, Transcript, VoiceClip, WavBlob
from saathi.errors import ClipHashMismatch, ClipMissing
from saathi.ids import ReplyKey, VoiceId


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeASR(Failable):
    def __init__(self, text: str = "synthetic transcript") -> None:
        super().__init__()
        self.text = text
        self.script: deque[Transcript] = deque()

    async def transcribe(self, audio: AudioBlob, *, hint: str = "hi") -> Transcript:
        self._enter("transcribe", audio, hint=hint)
        if self.script:
            return self.script.popleft()
        return Transcript(text=self.text, language=hint, confidence=0.9, engine="fake")


class FakeAudioCodec(Failable):
    async def to_wav16k(self, audio: AudioBlob, *, max_seconds: float) -> WavBlob:
        self._enter("to_wav16k", audio, max_seconds=max_seconds)
        return WavBlob(data=audio.data)

    async def encode_clip(self, wav: WavBlob) -> VoiceClip:
        self._enter("encode_clip", wav)
        return VoiceClip(key=ReplyKey("encoded"), data=wav.data, sha256=sha256_hex(wav.data))


class FakeTTS(Failable):
    async def synthesize(self, text: str, *, voice: VoiceId, max_seconds: float) -> VoiceClip:
        self._enter("synthesize", text, voice=voice, max_seconds=max_seconds)
        data = text.encode()
        return VoiceClip(key=ReplyKey("tts"), data=data, sha256=sha256_hex(data))


class FakeVoiceStore(Failable):
    """Fixed clips by (key, language). `fixed` re-checks the sha256 and raises ClipHashMismatch on a corrupt clip."""

    def __init__(self) -> None:
        super().__init__()
        self._clips: dict[tuple[ReplyKey, str], VoiceClip] = {}

    def add(self, key: ReplyKey, data: bytes, language: str = "hi", version: int = 1) -> VoiceClip:
        clip = VoiceClip(key=key, language=language, data=data, sha256=sha256_hex(data), version=version)
        self._clips[(key, language)] = clip
        return clip

    def corrupt(self, key: ReplyKey, language: str = "hi") -> None:
        """Change the stored bytes and keep the recorded hash, as a damaged file would."""
        clip = self._clips[(key, language)]
        self._clips[(key, language)] = clip.model_copy(update={"data": clip.data + b"\x00"})

    async def fixed(self, key: ReplyKey, language: str = "hi") -> VoiceClip:
        self._enter("fixed", key, language)
        clip = self._clips.get((key, language))
        if clip is None:
            raise ClipMissing()
        if sha256_hex(clip.data) != clip.sha256:
            raise ClipHashMismatch()
        return clip
