"""Caregiver-side Telegram bot (aiogram, MIT).

Phase 0 features:
- Summary cards for every elder message, and red alerts for emergencies and
  scams. Alerts go to the primary and backup approvers and repeat until
  someone taps "Acknowledge".
- Forward intake: the caregiver forwards the elder's WhatsApp voice note,
  photo or text into this bot, and it runs through the same pipeline.
- Commands: /start (shows your Telegram id), /status, /pause, /resume.

Only configured approver ids are served; everyone else is ignored.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from saathi.domain import Understanding
from saathi.legacy import Channel, InboundMessage, MediaKind

log = logging.getLogger(__name__)

ALERT_REPEAT_SECONDS = 120
ALERT_MAX_REPEATS = 5


class RuntimeFlags:
    """In-memory for Phase 0. Phase 1 persists this in Postgres."""

    def __init__(self) -> None:
        self.paused = False


class TelegramNotifier:
    def __init__(self, bot: Bot, primary_id: int, backup_ids: list[int]):
        self.bot = bot
        self.primary_id = primary_id
        self.backup_ids = backup_ids
        self._acked: set[str] = set()

    @property
    def everyone(self) -> list[int]:
        return [i for i in [self.primary_id, *self.backup_ids] if i]

    async def summary(self, msg: InboundMessage, transcript: str, u: Understanding) -> None:
        conf = f"{u.confidence:.0%}"
        missing = f"\n❓ Missing: {', '.join(u.missing_slots)}" if u.missing_slots else ""
        text = (
            f"🧓 <b>{html.escape(u.summary_en)}</b>\n"
            f"Intent: <code>{u.intent}</code> · {conf} · {u.tier_used} · {u.urgency}{missing}\n\n"
            f"<i>{html.escape(transcript[:1500]) or '(no text)'}</i>"
        )
        await self.bot.send_message(self.primary_id, text, parse_mode="HTML")

    async def alert(self, msg: InboundMessage, transcript: str, kind: str, matches: list[str]) -> None:
        icon = "🚨" if kind == "EMERGENCY" else "⚠️"
        text = (
            f"{icon} <b>{kind}</b> — matched: {html.escape(', '.join(matches))}\n\n"
            f"<i>{html.escape(transcript[:1500])}</i>\n\n"
            "The elder has been sent a safety message. Please call them."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Acknowledge", callback_data=f"ack:{msg.id}")]]
        )

        async def _send_round() -> None:
            for chat_id in self.everyone:
                try:
                    await self.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
                except Exception as e:  # one failing recipient must not block others
                    log.warning("alert to %s failed: %s", chat_id, e)

        await _send_round()
        if kind == "EMERGENCY":
            asyncio.create_task(self._repeat_until_ack(msg.id, _send_round))

    async def _repeat_until_ack(self, msg_id: str, send: Callable[[], Awaitable[None]]) -> None:
        for _ in range(ALERT_MAX_REPEATS):
            await asyncio.sleep(ALERT_REPEAT_SECONDS)
            if msg_id in self._acked:
                return
            await send()

    def ack(self, msg_id: str) -> None:
        self._acked.add(msg_id)


def build_router(
    *,
    notifier: TelegramNotifier,
    flags: RuntimeFlags,
    on_forward: Callable[[InboundMessage, bytes | None, str | None], Awaitable[None]],
) -> Router:
    router = Router()
    allowed = set(notifier.everyone)

    def _ok(user_id: int | None) -> bool:
        return user_id is not None and user_id in allowed

    @router.message(Command("start"))
    async def start(m: Message) -> None:
        await m.answer(f"Saathi bot. Your Telegram id is <code>{m.from_user.id}</code>.", parse_mode="HTML")

    @router.message(Command("status"))
    async def status(m: Message) -> None:
        if _ok(m.from_user and m.from_user.id):
            await m.answer(f"Running. Commits paused: {flags.paused}")

    @router.message(Command("pause"))
    async def pause(m: Message) -> None:
        if _ok(m.from_user and m.from_user.id):
            flags.paused = True
            await m.answer("⏸ All commits paused. /resume to continue.")

    @router.message(Command("resume"))
    async def resume(m: Message) -> None:
        if _ok(m.from_user and m.from_user.id):
            flags.paused = False
            await m.answer("▶️ Commits resumed.")

    @router.callback_query(F.data.startswith("ack:"))
    async def on_ack(cb: CallbackQuery) -> None:
        if not _ok(cb.from_user.id):
            return
        notifier.ack(cb.data.split(":", 1)[1])
        await cb.answer("Acknowledged")
        if cb.message:
            await cb.message.edit_reply_markup(reply_markup=None)

    @router.message(F.voice | F.audio | F.photo | F.video | F.text)
    async def forwarded(m: Message) -> None:
        if not _ok(m.from_user and m.from_user.id):
            return
        if m.text and m.text.startswith("/"):
            return
        kind, file_id, mime = MediaKind.TEXT, None, None
        if m.voice:
            kind, file_id, mime = MediaKind.AUDIO, m.voice.file_id, m.voice.mime_type or "audio/ogg"
        elif m.audio:
            kind, file_id, mime = MediaKind.AUDIO, m.audio.file_id, m.audio.mime_type or "audio/mpeg"
        elif m.photo:
            kind, file_id, mime = MediaKind.IMAGE, m.photo[-1].file_id, "image/jpeg"
        elif m.video:
            kind, file_id, mime = MediaKind.VIDEO, m.video.file_id, m.video.mime_type or "video/mp4"
        data: bytes | None = None
        if file_id:
            buf = await m.bot.download(file_id)
            data = buf.read() if buf else None
        inbound = InboundMessage(
            channel=Channel.TELEGRAM_FORWARD,
            provider_message_id=f"tg:{m.chat.id}:{m.message_id}",
            forwarded_by_telegram_id=m.from_user.id,
            kind=kind,
            text=m.text or m.caption,
            media_id=file_id,
            media_mime=mime,
        )
        await m.answer("📥 Got it, processing as a message from the elder…")
        await on_forward(inbound, data, mime)

    return router


def build_dispatcher(router: Router) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


def run() -> None:  # pragma: no cover - convenience entry point
    """The bot runs inside the API process (see saathi.app); this is for debugging."""
    from saathi.app import run as run_api

    run_api()
