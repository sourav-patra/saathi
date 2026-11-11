"""LocalTTS (M16; D-69): speech from the local runtime over HTTP. No hosted service is called from here.

The elder's text is personal content, so it goes only to the local runtime whose address the caller gives.
A hosted fallback needs a PL-20 receipt first and is not built in Phase 0 (`ReceiptMissing` is for that path).
"""

from __future__ import annotations

import hashlib

import httpx

from saathi.domain import VoiceClip
from saathi.errors import TtsTimeout, TtsUnavailable
from saathi.ids import ReplyKey, VoiceId


class LocalTTS:
    """Implements `ports.TTS`. The runtime takes a JSON body `{text, voice}` and answers WAV bytes."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = base_url.rstrip("/") + "/synthesize"
        self._client = client or httpx.AsyncClient()

    async def synthesize(self, text: str, *, voice: VoiceId, max_seconds: float) -> VoiceClip:
        try:
            resp = await self._client.post(self._url, json={"text": text, "voice": str(voice)}, timeout=max_seconds)
        except httpx.TimeoutException as exc:
            raise TtsTimeout(type(exc).__name__) from None
        except httpx.HTTPError as exc:
            raise TtsUnavailable(type(exc).__name__) from None
        if resp.status_code != 200 or not resp.content:
            raise TtsUnavailable(f"status_{resp.status_code}")
        return VoiceClip(key=ReplyKey("dynamic"), data=resp.content, sha256=hashlib.sha256(resp.content).hexdigest())

    async def aclose(self) -> None:
        await self._client.aclose()
