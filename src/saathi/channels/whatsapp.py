"""WhatsApp Business Cloud API elder channel (M08, RQ-105, RQ-81).

A thin client written directly against the Graph API with httpx, to keep the dependency surface small.
`WhatsAppElderChannel` implements the `ElderChannel` port: core code passes a `PersonRef`, the channel
resolves it through a `PersonDirectory`, and no return value carries a phone number. Webhook message
bodies are data: parsing never interprets them.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

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
from saathi.ids import IdemKey, PersonRef, TemplateId, normalize_e164
from saathi.ports import Clock

__all__ = [
    "MAX_MEDIA_BYTES",
    "PersonDirectory",
    "WhatsAppClient",
    "WhatsAppElderChannel",
    "parse_webhook",
    "verify_signature",
]

WINDOW = timedelta(hours=24)
# The LLD names no download cap for the Graph API; this reuses its 16 MiB input cap for voice-note tools (M13).
MAX_MEDIA_BYTES = 16 * 1024 * 1024
# Hosts that serve media bytes for a media id (Cloud API). The token is sent only to these, over https.
MEDIA_HOSTS = frozenset({"lookaside.fbsbx.com"})
_MEDIA_HANDLE = re.compile(r"[0-9]+")
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024

# Graph API error codes (Meta "Cloud API error codes").
_CODE_REENGAGEMENT = 131047  # more than 24 h since the user's last message: only a template may go
_CODE_TEMPLATE_MISSING = {132000, 132001, 132012}
_CODE_RATE = {4, 80007, 130429, 131048, 131056}
_CODE_AUTH = {10, 190}

_MEDIA_KINDS = {
    "audio": MediaKind.AUDIO,
    "image": MediaKind.IMAGE,
    "video": MediaKind.VIDEO,
    "document": MediaKind.DOCUMENT,
}


def verify_signature(app_secret: str, body: bytes, header_value: str | None) -> bool:
    """Validate ``X-Hub-Signature-256: sha256=<hex>`` (HMAC of the raw body). Compared as bytes: a header that
    is not ASCII is simply invalid, never an error."""
    if not app_secret or not header_value or not header_value.startswith("sha256="):
        return False
    try:
        given = header_value.removeprefix("sha256=").encode("ascii")
    except UnicodeEncodeError:
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest().encode("ascii")
    return hmac.compare_digest(expected, given)


def _received_at(msg: Mapping[str, Any], now: datetime | None) -> datetime:
    try:
        return datetime.fromtimestamp(int(msg["timestamp"]), UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return now or datetime.now(UTC)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _items(value: Any) -> list[Any]:
    """A list the platform documents, or nothing when it is absent. Any other shape is a malformed envelope."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ChannelParseError("malformed webhook envelope")
    return value


def _parse_message(msg: dict[str, Any], now: datetime | None) -> InboundMessage | None:
    """One message item. An item with no usable id or sender is dropped; odd inner fields degrade to
    `UNSUPPORTED` or empty text rather than raising."""
    message_id = msg.get("id")
    if not isinstance(message_id, str) or not message_id:
        return None
    sender = msg.get("from")
    try:
        sender_ref = normalize_e164(sender) if isinstance(sender, str) else ""
    except ValueError:
        sender_ref = ""
    if not sender_ref:
        return None
    mtype = _str(msg.get("type")) or ""
    kind = MediaKind.UNSUPPORTED
    text: str | None = None
    media: MediaRef | None = None
    if mtype == "text":
        kind = MediaKind.TEXT
        text = _str(_dict(msg.get("text")).get("body"))
    elif mtype in _MEDIA_KINDS:
        body = _dict(msg.get(mtype))
        kind = _MEDIA_KINDS[mtype]
        text = _str(body.get("caption"))
        handle = body.get("id")
        if isinstance(handle, str) and handle:
            media = MediaRef(source=Source.WHATSAPP, handle=handle, mime=_str(body.get("mime_type")))
    elif mtype == "interactive":
        inter = _dict(msg.get("interactive"))
        reply = _dict(inter.get("button_reply")) or _dict(inter.get("list_reply"))
        kind, text = MediaKind.TEXT, _str(reply.get("title"))
    elif mtype == "button":
        kind, text = MediaKind.TEXT, _str(_dict(msg.get("button")).get("text"))
    return InboundMessage(
        source=Source.WHATSAPP,
        provider_message_id=message_id,
        sender_ref=sender_ref,
        kind=kind,
        text=text,
        media=media,
        received_at=_received_at(msg, now),
    )


def parse_webhook(payload: dict[str, Any], *, now: datetime | None = None) -> list[InboundMessage]:
    """Extract inbound user messages. Status updates are ignored; sticker, contact, location and any
    kind we do not know become `MediaKind.UNSUPPORTED`, never dropped (RQ-81 d). A malformed envelope raises
    `ChannelParseError`; a malformed item inside a good envelope is skipped so the rest of the batch survives."""
    if not isinstance(payload, dict):
        raise ChannelParseError("malformed webhook envelope")
    out: list[InboundMessage] = []
    for entry in _items(payload.get("entry")):
        if not isinstance(entry, dict):
            raise ChannelParseError("malformed webhook envelope")
        for change in _items(entry.get("changes")):
            if not isinstance(change, dict):
                raise ChannelParseError("malformed webhook envelope")
            value = change.get("value")
            if value is not None and not isinstance(value, dict):
                raise ChannelParseError("malformed webhook envelope")
            for msg in _items((value or {}).get("messages")):
                if isinstance(msg, dict) and (parsed := _parse_message(msg, now)) is not None:
                    out.append(parsed)
    return out


class WhatsAppClient:
    """Raw Graph API calls. Returns responses unchanged; error mapping is the channel's job."""

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        graph_version: str = "v23.0",
        client: httpx.AsyncClient | None = None,
        media_hosts: frozenset[str] = MEDIA_HOSTS,
        api_base: str = "",
        media_base: str = "",
    ):
        # `api_base` and `media_base` name the egress proxy routes of a model-driven container (RQ-102); empty = direct.
        self.base = f"{api_base.rstrip('/') or 'https://graph.facebook.com'}/{graph_version}"
        self._media_base = media_base.rstrip("/")
        self.phone_number_id = phone_number_id
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._client = client or httpx.AsyncClient(timeout=60)
        self._media_hosts = media_hosts

    async def get(self, path: str, **params: str) -> httpx.Response:
        """A Graph API path under the versioned base. Never a full URL: the token goes only to `base`."""
        return await self._client.get(f"{self.base}/{path}", headers=self._headers, params=params or None)

    def media_url_allowed(self, url: object) -> bool:
        if not isinstance(url, str):
            return False
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port
        except ValueError:
            return False
        return (
            parts.scheme == "https"
            and host in self._media_hosts
            and port in (None, 443)
            and parts.username is None
            and parts.password is None
        )

    async def download(self, url: str, *, limit: int = MAX_MEDIA_BYTES) -> bytes:
        """Fetch media bytes with the token, only from an allowlisted https host, up to `limit` bytes.
        Raises `SendRejected` for a disallowed host or an oversize body."""
        if not self.media_url_allowed(url):
            raise SendRejected("bad_media_url")
        if self._media_base:  # the checked URL is fetched through the proxy route, which names the same media host
            parts = urlsplit(url)
            url = f"{self._media_base}{parts.path}" + (f"?{parts.query}" if parts.query else "")
        async with self._client.stream("GET", url, headers=self._headers, follow_redirects=False) as resp:
            if not resp.is_success:
                await resp.aread()
                _raise_for(resp, media=True)
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise SendRejected("media_too_large")
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > limit:
                    raise SendRejected("media_too_large")
        return bytes(data)

    async def send(self, payload: dict[str, Any]) -> httpx.Response:
        body = {"messaging_product": "whatsapp", **payload}
        return await self._client.post(f"{self.base}/{self.phone_number_id}/messages", json=body, headers=self._headers)

    async def upload(self, data: bytes, mime: str, filename: str) -> httpx.Response:
        return await self._client.post(
            f"{self.base}/{self.phone_number_id}/media",
            headers=self._headers,
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, data, mime)},
        )


def _graph_error(resp: httpx.Response) -> tuple[int, str]:
    try:
        err = resp.json().get("error") or {}
        return int(err.get("code", 0)), str(err.get("message", ""))
    except (ValueError, AttributeError, TypeError):
        return 0, ""


def _raise_for(resp: httpx.Response, *, media: bool = False) -> None:
    """Map a Graph API failure to a port error. A body text is never put in the message."""
    if resp.is_success:
        return
    code, _ = _graph_error(resp)
    status = resp.status_code
    if code == _CODE_REENGAGEMENT:
        raise ProactiveNotAllowed("window_closed")
    if code in _CODE_TEMPLATE_MISSING:
        raise TemplateMissing()
    if status == 429 or code in _CODE_RATE:
        try:
            retry = float(resp.headers.get("retry-after", 0))
        except ValueError:
            retry = 0.0
        raise RateLimited(retry)
    if status >= 500 or status in (401, 403) or code in _CODE_AUTH:
        raise ChannelDown()
    if media and status in (400, 404, 410):
        raise MediaExpired()
    raise SendRejected(str(code or status))


class WhatsAppElderChannel:
    kind: Literal["whatsapp", "telegram_elder"] = "whatsapp"
    capabilities = frozenset(
        {
            ChannelCap.VOICE_IN,
            ChannelCap.VOICE_OUT,
            ChannelCap.IMAGE_IN,
            ChannelCap.IMAGE_OUT,
            ChannelCap.VIDEO_IN,
            ChannelCap.TEXT,
            ChannelCap.TEMPLATES,
        }
    )  # no PROACTIVE_FREE: free text needs the 24 hour window (RQ-77)

    def __init__(
        self, client: WhatsAppClient, people: PersonDirectory, clock: Clock, *, template_language: str = "hi"
    ) -> None:
        self._client = client
        self._people = people
        self._clock = clock
        self._lang = template_language
        self._last_inbound: dict[PersonRef, datetime] = {}
        self._sent = SentKeys()

    def note_inbound(self, person: PersonRef, at: datetime) -> None:
        """Record the elder's last message time so a closed window is known before a send."""
        prev = self._last_inbound.get(person)
        if prev is None or at > prev:
            self._last_inbound[person] = at

    def _check_window(self, to: PersonRef) -> None:
        last = self._last_inbound.get(to)
        if last is not None and self._clock.now() - last > WINDOW:
            raise ProactiveNotAllowed("window_closed")

    async def _address(self, to: PersonRef) -> str:
        """The elder's number in the shared E.164 form, without the plus (the Graph API wants digits)."""
        try:
            raw = await self._people.address(to, self.kind)
        except KeyError as exc:
            raise SendRejected("unknown_person") from exc
        try:
            return normalize_e164(raw).removeprefix("+")
        except ValueError as exc:
            raise SendRejected("bad_address") from exc

    async def _post(self, key: IdemKey, payload: dict[str, Any], *, to: PersonRef) -> SendReceipt:
        """Send under a held claim on `key` (see `SentKeys.claim`); records the key only on success."""
        address = await self._address(to)
        try:
            resp = await self._client.send({"to": address, **payload})
        except httpx.HTTPError as exc:
            raise ChannelDown() from exc
        _raise_for(resp)
        try:
            provider_id = str(resp.json()["messages"][0]["id"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise SendRejected("bad_response") from exc
        self._sent.record(key)
        return SendReceipt(provider_id=provider_id, key=key)

    async def _upload(self, data: bytes, mime: str, filename: str) -> str:
        try:
            resp = await self._client.upload(data, mime, filename)
        except httpx.HTTPError as exc:
            raise ChannelDown() from exc
        _raise_for(resp)
        try:
            return str(resp.json()["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SendRejected("bad_response") from exc

    async def send_text(self, to: PersonRef, text: str, *, key: IdemKey) -> SendReceipt:
        async with self._sent.claim(key):
            self._check_window(to)
            return await self._post(key, {"type": "text", "text": {"body": text[:TEXT_LIMIT]}}, to=to)

    async def send_audio(self, to: PersonRef, clip: VoiceClip, *, key: IdemKey) -> SendReceipt:
        async with self._sent.claim(key):
            self._check_window(to)
            media_id = await self._upload(clip.data, "audio/ogg", f"{clip.key}.ogg")
            return await self._post(key, {"type": "audio", "audio": {"id": media_id}}, to=to)

    async def send_image(self, to: PersonRef, image: ImageBlob, caption: str, *, key: IdemKey) -> SendReceipt:
        async with self._sent.claim(key):
            self._check_window(to)
            media_id = await self._upload(image.data, image.mime, "card.jpg")
            payload = {"type": "image", "image": {"id": media_id, "caption": caption[:CAPTION_LIMIT]}}
            return await self._post(key, payload, to=to)

    async def send_template(
        self, to: PersonRef, template: TemplateId, params: Mapping[str, str], *, key: IdemKey
    ) -> SendReceipt:
        body: dict[str, Any] = {"name": str(template), "language": {"code": self._lang}}
        if params:
            body["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": v} for v in params.values()]}
            ]
        async with self._sent.claim(key):
            return await self._post(key, {"type": "template", "template": body}, to=to)

    async def fetch_media(self, ref: MediaRef) -> MediaBlob:
        """Two calls: the media id gives a short-lived URL, the URL needs the token. A gone file is `MediaExpired`.
        The handle must be digits and the URL an allowlisted https host, so the token cannot be sent elsewhere."""
        if ref.source is not Source.WHATSAPP:
            raise SendRejected("wrong_source")
        if not _MEDIA_HANDLE.fullmatch(ref.handle):
            raise SendRejected("bad_handle")
        try:
            meta = await self._client.get(ref.handle)
            _raise_for(meta, media=True)
            info = meta.json()
            url = info["url"]
            if not self._client.media_url_allowed(url):
                raise SendRejected("bad_media_url")
            data = await self._client.download(url)
        except httpx.HTTPError as exc:
            raise ChannelDown() from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise MediaExpired() from exc
        return MediaBlob(data=data, mime=_str(info.get("mime_type")) or ref.mime or "application/octet-stream")

    async def health(self) -> ChannelHealth:
        try:
            resp = await self._client.get(self._client.phone_number_id, fields="id")
        except httpx.HTTPError:
            return ChannelHealth.DOWN
        if resp.is_success:
            return ChannelHealth.UP
        if resp.status_code >= 500 or resp.status_code in (401, 403):
            return ChannelHealth.DOWN
        return ChannelHealth.DEGRADED
