"""Speech-to-text (M13) behind the `ASR` port: a client of the local whisper.cpp server (MIT).

The server runs as its own container and takes 16 kHz WAV only: it has no decoder of its own.
Voice notes arrive as Ogg Opus, so the worker decodes them first through the `AudioCodec` port (`opusdec`,
`saathi.voice.codec`) and hands this client the WAV it produced. The client posts exactly what it is given.

The ASR eval (evals/) decides whether a hosted engine should become the primary one for real voice notes. A hosted
engine needs a receipt first (PL-20) and is not built in Phase 0.
"""

from __future__ import annotations

import httpx

from saathi.domain import AudioBlob, Transcript
from saathi.errors import AsrUnavailable

_EXTENSION = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a"}


class LocalAsrClient:
    """Implements `ports.ASR`. Any transport error, bad status or empty body is `AsrUnavailable`: the caller forwards
    the audio to a human and never waits on this client (RQ-16)."""

    engine = "whispercpp"

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None, timeout_s: float = 180.0) -> None:
        self._url = base_url.rstrip("/") + "/inference"
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def transcribe(self, audio: AudioBlob, *, hint: str = "hi") -> Transcript:
        mime = audio.mime.split(";")[0].strip().lower()
        filename = "note." + _EXTENSION.get(mime, "wav")
        try:
            resp = await self._client.post(
                self._url,
                files={"file": (filename, audio.data, mime)},
                data={"response_format": "json", "language": hint, "temperature": "0.0"},
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AsrUnavailable(type(exc).__name__) from None
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise AsrUnavailable("bad_body")
        return Transcript(text=text.strip(), language=hint, engine=self.engine)

    async def aclose(self) -> None:
        await self._client.aclose()
