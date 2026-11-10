"""Fakes for ASR, the audio codec, TTS and the fixed-clip voice store (it checks hash and review)."""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable

from fakes.base import Failable
from saathi.domain import AudioBlob, Transcript, VoiceClip, WavBlob
from saathi.errors import ClipHashMismatch, ClipMissing, ClipUnreviewed
from saathi.ids import ReplyKey, VoiceId


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeASR(Failable):
    """`FakeASR(script)`: each call takes the next item, a transcript text or an error to raise. Then `text`."""

    def __init__(
        self,
        script: Iterable[str | Transcript | BaseException | type[BaseException]] = (),
        *,
        text: str = "synthetic transcript",
    ) -> None:
        super().__init__()
        if isinstance(script, str):
            raise TypeError("script is a list of transcripts or errors, not one string")
        self.text = text
        self.script: deque[str | Transcript | BaseException | type[BaseException]] = deque(script)

    async def transcribe(self, audio: AudioBlob, *, hint: str = "hi") -> Transcript:
        self._enter("transcribe", audio, hint=hint)
        if self.script:
            item = self.script.popleft()
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, type):
                raise item()
            if isinstance(item, Transcript):
                return item
            return Transcript(text=item, language=hint, confidence=0.9, engine="fake")
        return Transcript(text=self.text, language=hint, confidence=0.9, engine="fake")


class FakeAudioCodec(Failable):
    async def to_wav16k(self, audio: AudioBlob, *, max_seconds: float) -> WavBlob:
        self._enter("to_wav16k", audio, max_seconds=max_seconds)
        return WavBlob(data=audio.data)

    async def encode_clip(self, wav: WavBlob) -> VoiceClip:
        self._enter("encode_clip", wav)
        return VoiceClip(key=ReplyKey("encoded"), data=wav.data, sha256=sha256_hex(wav.data))


FakeCodec = FakeAudioCodec  # the name the LLD uses


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
        self._unreviewed: set[tuple[ReplyKey, str]] = set()

    def add(
        self, key: ReplyKey, data: bytes, language: str = "hi", version: int = 1, *, reviewed: bool = True
    ) -> VoiceClip:
        """`reviewed=False` stores a clip whose review columns are null: `fixed` then raises ClipUnreviewed."""
        clip = VoiceClip(key=key, language=language, data=data, sha256=sha256_hex(data), version=version)
        self._clips[(key, language)] = clip
        if reviewed:
            self._unreviewed.discard((key, language))
        else:
            self._unreviewed.add((key, language))
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
        if (key, language) in self._unreviewed:
            raise ClipUnreviewed()
        if sha256_hex(clip.data) != clip.sha256:
            raise ClipHashMismatch()
        return clip
