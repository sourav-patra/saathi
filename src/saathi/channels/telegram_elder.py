"""Telegram elder channel, the backup to WhatsApp (M09, RQ-105, RQ-108).

Phase 0 builds the class and its contract rows against a mocked HTTP client; the live bot, start codes
and the switch are Phase 1. Capabilities exclude templates. Update bodies are data, never instructions.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from saathi.channels import ChannelParseError, PersonDirectory, SentKeys
from saathi.domain import (
    ChannelCap,
    ChannelHealth,
    ImageBlob,
    InboundMessage,
    MediaBlob,
    MediaKind,
    MediaRef,
    SendReceipt,
    Source,
    VoiceClip,
)
from saathi.errors import (
    ChannelDown,
    MediaExpired,
    ProactiveNotAllowed,
    RateLimited,
    SendRejected,
    TemplateMissing,
)
from saathi.ids import IdemKey, PersonRef, TemplateId

__all__ = [
    "MAX_MEDIA_BYTES",
    "PersonDirectory",
    "TelegramClient",
    "TelegramElderChannel",
    "parse_update",
    "verify_secret_token",
]

# The LLD names no download cap for the Bot API; this reuses its 16 MiB input cap for voice-note tools (M13).
MAX_MEDIA_BYTES = 16 * 1024 * 1024
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


def verify_secret_token(expected: str, header_value: str | None) -> bool:
    """Check ``X-Telegram-Bot-Api-Secret-Token`` in constant time."""
    if not expected or not header_value:
        return False
    return hmac.compare_digest(expected.encode(), header_value.encode())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _file(value: Any, default_mime: str | None = None) -> tuple[str | None, str | None]:
    """`(file_id, mime)` from a Bot API file object, or `(None, None)` when it is not one."""
    body = _dict(value)
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        return None, None
    return file_id, _str(body.get("mime_type")) or default_mime


def _plain_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or value == "":
        return None
    return str(value)


def parse_update(update: dict[str, Any], *, now: datetime | None = None) -> list[InboundMessage]:
    """Extract the elder's message from a Bot API update (private chats only). Callback queries, edits,
    channel posts and group chats yield nothing. Stickers, contacts and locations are `UNSUPPORTED`.
    An update that is not an object raises `ChannelParseError`; a message with no chat or message id is dropped."""
    if not isinstance(update, dict):
        raise ChannelParseError("malformed update")
    msg = update.get("message")
    if not isinstance(msg, dict):
        return []
    chat = _dict(msg.get("chat"))
    chat_id, message_id = _plain_id(chat.get("id")), _plain_id(msg.get("message_id"))
    if chat.get("type", "private") != "private" or chat_id is None or message_id is None:
        return []
    kind = MediaKind.UNSUPPORTED
    text: str | None = None
    file_id: str | None = None
    mime: str | None = None
    if "text" in msg:
        kind, text = MediaKind.TEXT, _str(msg["text"])
    else:
        text = _str(msg.get("caption"))
        photo = msg.get("photo")
        if msg.get("voice"):
            kind = MediaKind.AUDIO
            file_id, mime = _file(msg["voice"], "audio/ogg")
        elif msg.get("audio"):
            kind = MediaKind.AUDIO
            file_id, mime = _file(msg["audio"], "audio/mpeg")
        elif photo:
            kind = MediaKind.IMAGE
            sizes = photo if isinstance(photo, list) else []
            file_id, mime = _file(sizes[-1] if sizes else None, "image/jpeg")
        elif msg.get("video") or msg.get("video_note"):
            kind = MediaKind.VIDEO
            file_id, mime = _file(msg.get("video") or msg["video_note"], "video/mp4")
        elif msg.get("document"):
            kind = MediaKind.DOCUMENT
            file_id, mime = _file(msg["document"])
    try:
        received = datetime.fromtimestamp(int(msg["date"]), UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        received = now or datetime.now(UTC)
    media = MediaRef(source=Source.TELEGRAM_ELDER, handle=file_id, mime=mime) if file_id else None
    return [
        InboundMessage(
            source=Source.TELEGRAM_ELDER,
            provider_message_id=f"{chat_id}:{message_id}",
            sender_ref=chat_id,
            kind=kind,
            text=text,
            media=media,
            received_at=received,
        )
    ]


class TelegramClient:
    """Raw Bot API calls over httpx. The token is only in the URL and never logged or returned."""

    def __init__(self, *, token: str, client: httpx.AsyncClient | None = None, api: str = "https://api.telegram.org"):
        self._url = f"{api}/bot{token}"
        self._file_url = f"{api}/file/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=60)

    async def call(self, method: str, payload: Mapping[str, Any] | None = None) -> httpx.Response:
        return await self._client.post(f"{self._url}/{method}", json=dict(payload or {}))

    async def upload(
        self, method: str, field: str, data: bytes, filename: str, mime: str, form: Mapping[str, Any]
    ) -> httpx.Response:
        return await self._client.post(
            f"{self._url}/{method}", data={k: str(v) for k, v in form.items()}, files={field: (filename, data, mime)}
        )

    async def download(self, file_path: str, *, limit: int = MAX_MEDIA_BYTES) -> httpx.Response:
        """Fetch a file, refusing a body over `limit` bytes with `SendRejected("media_too_large")`."""
        async with self._client.stream("GET", f"{self._file_url}/{file_path}", follow_redirects=False) as resp:
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise SendRejected("media_too_large")
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > limit:
                    raise SendRejected("media_too_large")
            return httpx.Response(resp.status_code, content=bytes(data), headers=resp.headers)


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _raise_for(resp: httpx.Response, *, media: bool = False) -> dict[str, Any]:
    """Return the Bot API `result` or raise a port error. Descriptions are never copied into messages.
    A body that is not the documented object maps to `SendRejected`, never to a stray exception type."""
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {}
    body = parsed if isinstance(parsed, dict) else {}
    if resp.is_success and body.get("ok"):
        result = body.get("result")
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise SendRejected("bad_response")
        return result
    status = int(_number(body.get("error_code"), resp.status_code))
    desc = str(body.get("description", "")).lower()
    if status == 429:
        raise RateLimited(max(0.0, _number(_dict(body.get("parameters")).get("retry_after"), 0.0)))
    if status >= 500 or status == 401:
        raise ChannelDown()
    if media and (status == 404 or "file" in desc):
        raise MediaExpired()
    if status == 403 and "initiate" in desc:
        raise ProactiveNotAllowed("chat_not_started")  # the chat never started the bot
    raise SendRejected(str(status))


class TelegramElderChannel:
    kind: Literal["whatsapp", "telegram_elder"] = "telegram_elder"
    capabilities = frozenset(
        {
            ChannelCap.VOICE_IN,
            ChannelCap.VOICE_OUT,
            ChannelCap.IMAGE_IN,
            ChannelCap.IMAGE_OUT,
            ChannelCap.VIDEO_IN,
            ChannelCap.TEXT,
            ChannelCap.PROACTIVE_FREE,
        }
    )  # no TEMPLATES, no 24 hour window (RQ-108)

    def __init__(self, client: TelegramClient, people: PersonDirectory) -> None:
        self._client = client
        self._people = people
        self._sent = SentKeys()

    async def _chat(self, to: PersonRef) -> str:
        try:
            return await self._people.address(to, self.kind)
        except KeyError as exc:
            raise SendRejected("unknown_person") from exc

    def _receipt(self, result: dict[str, Any], key: IdemKey) -> SendReceipt:
        try:
            provider_id = str(result["message_id"])
        except (KeyError, TypeError) as exc:
            raise SendRejected("bad_response") from exc
        self._sent.record(key)
        return SendReceipt(provider_id=provider_id, key=key)

    async def _run(self, coro: Any) -> dict[str, Any]:
        try:
            resp = await coro
        except httpx.HTTPError as exc:
            raise ChannelDown() from exc
        return _raise_for(resp)

    async def send_text(self, to: PersonRef, text: str, *, key: IdemKey) -> SendReceipt:
        async with self._sent.claim(key):
            chat = await self._chat(to)
            result = await self._run(self._client.call("sendMessage", {"chat_id": chat, "text": text[:TEXT_LIMIT]}))
            return self._receipt(result, key)

    async def send_audio(self, to: PersonRef, clip: VoiceClip, *, key: IdemKey) -> SendReceipt:
        async with self._sent.claim(key):
            chat = await self._chat(to)
            result = await self._run(
                self._client.upload("sendVoice", "voice", clip.data, f"{clip.key}.ogg", "audio/ogg", {"chat_id": chat})
            )
            return self._receipt(result, key)

    async def send_image(self, to: PersonRef, image: ImageBlob, caption: str, *, key: IdemKey) -> SendReceipt:
        async with self._sent.claim(key):
            chat = await self._chat(to)
            form = {"chat_id": chat, "caption": caption[:CAPTION_LIMIT]}
            result = await self._run(
                self._client.upload("sendPhoto", "photo", image.data, "card.jpg", image.mime, form)
            )
            return self._receipt(result, key)

    async def send_template(
        self, to: PersonRef, template: TemplateId, params: Mapping[str, str], *, key: IdemKey
    ) -> SendReceipt:
        raise TemplateMissing()  # Telegram has no templates; callers read `capabilities`

    async def fetch_media(self, ref: MediaRef) -> MediaBlob:
        if ref.source is not Source.TELEGRAM_ELDER:
            raise SendRejected("wrong_source")
        try:
            meta = await self._client.call("getFile", {"file_id": ref.handle})
            info = _raise_for(meta, media=True)
            path = info.get("file_path")
            if not isinstance(path, str) or not path:
                raise MediaExpired()
            blob = await self._client.download(str(path))
        except httpx.HTTPError as exc:
            raise ChannelDown() from exc
        if blob.status_code in (404, 410):
            raise MediaExpired()
        if blob.status_code >= 500:
            raise ChannelDown()
        if not blob.is_success:
            raise SendRejected(str(blob.status_code))
        return MediaBlob(data=blob.content, mime=ref.mime or "application/octet-stream")

    async def health(self) -> ChannelHealth:
        try:
            resp = await self._client.call("getMe")
        except httpx.HTTPError:
            return ChannelHealth.DOWN
        if resp.is_success:
            return ChannelHealth.UP
        if resp.status_code >= 500 or resp.status_code in (401, 403):
            return ChannelHealth.DOWN
        return ChannelHealth.DEGRADED
