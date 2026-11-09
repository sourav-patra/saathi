"""Speech-to-text behind a provider interface.

Phase 0 ships one T1 provider: a **whisper.cpp server** (MIT) running as its
own container. Two reasons for the separate container:
- It runs fast on the VPS's ARM CPU with a quantised multilingual model, which
  handles Hindi/Hinglish.
- Audio decoding (OGG/Opus voice notes) happens through ffmpeg *inside that
  container*, an external program. No FFmpeg-linked Python wheel enters our
  dependency tree, which keeps the license policy clean.

The ASR eval (evals/) decides whether a hosted ASR engine should become the
primary engine for the elder's real voice notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class Transcript:
    text: str
    language: str | None = None
    provider: str = ""
    confidence: float | None = None  # not every provider reports one


class ASRProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, mime: str) -> Transcript: ...


class WhisperCppASR:
    name = "whispercpp"

    def __init__(self, base_url: str, *, language: str = "hi", client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.language = language
        self._client = client or httpx.AsyncClient(timeout=180)

    async def transcribe(self, audio: bytes, mime: str) -> Transcript:
        ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/wav": "wav"}
        filename = "note." + ext.get(mime.split(";")[0].strip(), "ogg")
        resp = await self._client.post(
            f"{self.base_url}/inference",
            files={"file": (filename, audio, mime)},
            data={"response_format": "json", "language": self.language, "temperature": "0.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        return Transcript(text=(data.get("text") or "").strip(), language=self.language, provider=self.name)


def build_asr(provider: str, *, whispercpp_url: str) -> ASRProvider:
    if provider == "whispercpp":
        return WhisperCppASR(whispercpp_url)
    raise ValueError(f"unknown ASR provider: {provider}")
