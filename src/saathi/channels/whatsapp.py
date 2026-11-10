"""WhatsApp Business Cloud API: webhook parsing, signature check, media, send.

A thin client written directly against the Graph API with httpx, to keep the
dependency surface small. Webhook payload reference: Meta "WhatsApp Cloud API —
Webhooks" docs.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from saathi.legacy import Channel, InboundMessage, MediaKind

_KIND_MAP = {
    "text": MediaKind.TEXT,
    "audio": MediaKind.AUDIO,
    "voice": MediaKind.AUDIO,
    "image": MediaKind.IMAGE,
    "video": MediaKind.VIDEO,
    "document": MediaKind.DOCUMENT,
    "sticker": MediaKind.IMAGE,
}


def verify_signature(app_secret: str, body: bytes, header_value: str | None) -> bool:
    """Validate ``X-Hub-Signature-256: sha256=<hex>`` (HMAC of the raw body)."""
    if not app_secret or not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value.removeprefix("sha256="))


def _e164(wa_id: str) -> str:
    return wa_id if wa_id.startswith("+") else f"+{wa_id}"


def parse_webhook(payload: dict[str, Any]) -> list[InboundMessage]:
    """Extract inbound user messages. Status updates (sent/delivered/read) are ignored."""
    out: list[InboundMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for msg in value.get("messages", []) or []:
                mtype = msg.get("type", "")
                kind = _KIND_MAP.get(mtype, MediaKind.OTHER)
                text = None
                media_id = None
                mime = None
                if mtype == "text":
                    text = (msg.get("text") or {}).get("body")
                elif mtype in _KIND_MAP:
                    media = msg.get(mtype) or {}
                    media_id = media.get("id")
                    mime = media.get("mime_type")
                    text = media.get("caption")
                elif mtype == "interactive":
                    inter = msg.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    text = reply.get("title")
                    kind = MediaKind.TEXT
                elif mtype == "button":
                    text = (msg.get("button") or {}).get("text")
                    kind = MediaKind.TEXT
                out.append(
                    InboundMessage(
                        channel=Channel.WHATSAPP,
                        provider_message_id=msg.get("id", ""),
                        sender_phone_e164=_e164(msg.get("from", "")),
                        kind=kind,
                        text=text,
                        media_id=media_id,
                        media_mime=mime,
                    )
                )
    return out


class WhatsAppClient:
    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        graph_version: str = "v23.0",
        client: httpx.AsyncClient | None = None,
    ):
        self.base = f"https://graph.facebook.com/{graph_version}"
        self.phone_number_id = phone_number_id
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._client = client or httpx.AsyncClient(timeout=60)

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Returns (bytes, mime). The media URL is short-lived and needs the token."""
        meta = await self._client.get(f"{self.base}/{media_id}", headers=self._headers)
        meta.raise_for_status()
        info = meta.json()
        blob = await self._client.get(info["url"], headers=self._headers)
        blob.raise_for_status()
        return blob.content, info.get("mime_type", "application/octet-stream")

    async def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"messaging_product": "whatsapp", **payload}
        resp = await self._client.post(
            f"{self.base}/{self.phone_number_id}/messages", json=body, headers=self._headers
        )
        resp.raise_for_status()
        return resp.json()

    async def send_text(self, to_e164: str, text: str) -> dict[str, Any]:
        return await self._send(
            {"to": to_e164.lstrip("+"), "type": "text", "text": {"body": text[:4096]}}
        )

    async def upload_media(self, data: bytes, mime: str, filename: str) -> str:
        resp = await self._client.post(
            f"{self.base}/{self.phone_number_id}/media",
            headers=self._headers,
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, data, mime)},
        )
        resp.raise_for_status()
        return resp.json()["id"]

    async def send_audio(self, to_e164: str, media_id: str) -> dict[str, Any]:
        return await self._send(
            {"to": to_e164.lstrip("+"), "type": "audio", "audio": {"id": media_id}}
        )

    async def mark_read(self, provider_message_id: str) -> None:
        await self._send({"status": "read", "message_id": provider_message_id})
