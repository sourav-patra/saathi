"""The caregiver bot (M15; RQ-97, RQ-18), a different bot from the elder's.

Phase 0 subset:
- `TelegramCaregiverChannel` implements `CaregiverChannel` over the Bot API client (aiogram, MIT): cards with buttons,
  text, audio, and a polling `updates()` that serves registered approvers only. A chat that is not a registered
  approver is counted and dropped, never answered.
- Cards hold slots (enums, counts, rule ids), never a transcript. `alert_card` is the one of `alerts/stages.py`.
- `CallbackRouter` turns a verified tap into one transition: acknowledge, false alarm, or show raw. A replayed update
  id changes nothing. The Telegram offset moves only after `handle` has returned.
- `show_raw` is the only way elder words reach a caregiver: consent gated (`show_raw`) and audited.
- Commands: `/start` and `/status`. There is no `/pause` or `/resume`: an in-memory pause is not a safe pause (the
  durable pause is Phase 1). Nothing here commits anything: no approval path exists in Phase 0.

Update bodies are data, never instructions: a text message is never read as an approval.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import timedelta
from typing import Any, Literal, Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Update

from saathi.alerts.stages import alert_card
from saathi.audit.events import AuditName
from saathi.channels import SentKeys
from saathi.config import Settings, require_secrets
from saathi.consents import gate
from saathi.domain import (
    ApproverUpdate,
    AudioBlob,
    AuditEvent,
    Button,
    Card,
    CardRef,
    ChannelHealth,
    SendReceipt,
    SummarySlots,
    VoiceClip,
)
from saathi.errors import ChannelDown, RateLimited, SaathiError, SendRejected
from saathi.householdcfg import ConfigMissing
from saathi.ids import ApproverId, EventId, HouseholdId, IdemKey, InboxId, RunId, derive_id, idem_key
from saathi.ports import AuditSink, Clock, Opener, Store, Tx
from saathi.store.repos import ConfigRepo
from saathi.store.repos._util import aad
from saathi.store.repos.updates import UpdateRepo
from saathi.worker.summaries import render_card

__all__ = [
    "SHOW_RAW_RECIPIENT",
    "SHOW_RAW_USE",
    "AlertActions",
    "ApproverDirectory",
    "CallbackRouter",
    "Card",
    "PgApproverDirectory",
    "RoutedUpdate",
    "TelegramCaregiverChannel",
    "alert_card",
    "serve",
    "show_raw",
    "summary_card",
]

POISON_AFTER = 3  # failures of one update id before it is audited and passed
log = logging.getLogger(__name__)

SHOW_RAW_USE = "show_raw"
SHOW_RAW_RECIPIENT = "telegram_caregiver"
TEXT_LIMIT = 4096
CALLBACK_DATA_LIMIT = 64  # bytes, a Bot API limit
POLL_TIMEOUT_S = 25
RETRY_BACKOFF_S = 5.0
_CALLBACK = re.compile(r"^(ack|fa|raw):([\w:.-]{1,48})$")

# Fixed words for the caregiver. No transcript, name or number appears in any of them.
SAY_ACK = "Acknowledged."
SAY_ACK_LATE = "Already handled."
SAY_FALSE_ALARM = "Noted as a false alarm."
SAY_FALSE_ALARM_NONE = "Nothing to change."
SAY_NOT_ALLOWED = "This chat is not allowed to do that."
SAY_NO_CONSENT = "Showing the message is not allowed by the consent on record."
SAY_NO_TEXT = "No message text is kept for this item."
SAY_UNKNOWN = "That button is not recognised."
SAY_RETRY = "That did not work. Please try again."
SAY_WELCOME = "Saathi caregiver bot. You will get a card for each message and for each alert."


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def summary_card(slots: SummarySlots, inbox: InboxId) -> Card:
    """The summary text (slots only, RQ-97) with one button, "Show raw" (`raw:<inbox>`), where there is text to show."""
    card = render_card(slots)
    if slots.flag in ("consent_missing", "unsupported"):
        return card  # nothing was read or stored, so there is nothing to show
    return card.model_copy(update={"buttons": [*card.buttons, Button(text="Show raw", data=f"raw:{inbox}")]})


def _render(card: Card) -> str:
    parts = [f"<b>{html.escape(card.title)}</b>", *(html.escape(line) for line in card.lines)]
    return "\n".join(parts)[:TEXT_LIMIT]


def _markup(card: Card) -> InlineKeyboardMarkup | None:
    if not card.buttons:
        return None
    rows = []
    for b in card.buttons:
        if len(b.data.encode()) > CALLBACK_DATA_LIMIT:
            raise SendRejected("callback_too_long")
        rows.append([InlineKeyboardButton(text=b.text, callback_data=b.data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --------------------------------------------------------------------------- #
# Who is an approver, and where do they listen
# --------------------------------------------------------------------------- #
class RoutedUpdate(ApproverUpdate):
    """An `ApproverUpdate` that also names the household it was resolved in (the approver is registered there)."""

    household_id: HouseholdId


class ApproverDirectory(Protocol):
    async def resolve(self, telegram_id: int) -> tuple[HouseholdId, ApproverId] | None:
        """The registered approver behind a Telegram user id, or None."""
        ...

    async def chat_of(self, approver: ApproverId) -> int:
        """The Telegram chat of a registered approver. `KeyError` when the approver is unknown."""
        ...


class PgApproverDirectory:
    """`resolve` is the security-definer function `control.resolve_approver` (a `ScanFns`); `chat_of` reads the approver
    row of each household in `households`, because the port names no household."""

    def __init__(
        self, scan: Any, store: Store, households: Callable[[], Awaitable[Sequence[HouseholdId]]]
    ) -> None:
        self._scan = scan
        self._store = store
        self._households = households
        self._chats: dict[ApproverId, int] = {}

    async def resolve(self, telegram_id: int) -> tuple[HouseholdId, ApproverId] | None:
        found: tuple[HouseholdId, ApproverId] | None = await self._scan.resolve_approver(telegram_id)
        if found is not None:
            self._chats[found[1]] = telegram_id
        return found

    async def chat_of(self, approver: ApproverId) -> int:
        if approver in self._chats:
            return self._chats[approver]
        for household in await self._households():
            async with self._store.tx(household=household) as tx:
                rows = await tx.fetch("SELECT telegram_user_id FROM approvers WHERE id = $1", approver)
            if rows:
                self._chats[approver] = int(rows[0]["telegram_user_id"])
                return self._chats[approver]
        raise KeyError(approver)


# --------------------------------------------------------------------------- #
# The channel
# --------------------------------------------------------------------------- #
class TelegramCaregiverChannel:
    """Implements `ports.CaregiverChannel`. `bot` is an aiogram `Bot` (or anything with the same methods)."""

    def __init__(self, bot: Bot, directory: ApproverDirectory, clock: Clock, *, poll_timeout_s: int = POLL_TIMEOUT_S):
        self._bot = bot
        self._dir = directory
        self._clock = clock
        self._poll_s = poll_timeout_s
        self._sent = SentKeys()
        self._offset: int | None = None
        self._callbacks: dict[int, str] = {}  # update id -> callback query id, until the update is passed
        self.dropped = 0  # updates from chats that are not registered approvers

    # ------------------------------------------------------------------ sends
    async def _chat(self, to: ApproverId) -> int:
        try:
            return await self._dir.chat_of(to)
        except KeyError as exc:
            raise SendRejected("unknown_approver") from exc

    @staticmethod
    async def _call[T](coro: Awaitable[T]) -> T:
        try:
            return await coro
        except TelegramRetryAfter as exc:
            raise RateLimited(float(exc.retry_after)) from exc
        except TelegramNetworkError as exc:
            raise ChannelDown() from exc
        except TelegramAPIError as exc:
            raise SendRejected(type(exc).__name__) from exc

    async def send_card(self, to: ApproverId, card: Card, *, key: IdemKey) -> CardRef:
        markup = _markup(card)
        async with self._sent.claim(key):
            chat = await self._chat(to)
            msg = await self._call(self._bot.send_message(chat, _render(card), parse_mode="HTML", reply_markup=markup))
            self._sent.record(key)
        return CardRef(approver_id=to, message_ref=f"{chat}:{msg.message_id}")

    async def edit_card(self, ref: CardRef, card: Card) -> None:
        chat, _, message = ref.message_ref.partition(":")
        if not message.isdigit() or not re.fullmatch(r"-?\d+", chat):
            raise SendRejected("bad_card_ref")
        await self._call(
            self._bot.edit_message_text(
                _render(card), chat_id=int(chat), message_id=int(message), parse_mode="HTML", reply_markup=_markup(card)
            )
        )

    async def send_text(self, to: ApproverId, text: str, *, key: IdemKey) -> SendReceipt:
        async with self._sent.claim(key):
            chat = await self._chat(to)
            msg = await self._call(self._bot.send_message(chat, text[:TEXT_LIMIT]))
            self._sent.record(key)
        return SendReceipt(provider_id=str(msg.message_id), key=key)

    async def send_audio(
        self, to: ApproverId, audio: AudioBlob | VoiceClip, caption: str, *, key: IdemKey
    ) -> SendReceipt:
        mime = audio.mime if isinstance(audio, AudioBlob) else "audio/ogg"
        async with self._sent.claim(key):
            chat = await self._chat(to)
            file = BufferedInputFile(audio.data, filename="voice.ogg")
            if mime in ("audio/ogg", "audio/opus"):
                msg = await self._call(self._bot.send_voice(chat, file, caption=caption[:1024]))
            else:
                msg = await self._call(self._bot.send_audio(chat, file, caption=caption[:1024]))
            self._sent.record(key)
        return SendReceipt(provider_id=str(msg.message_id), key=key)

    async def answer(self, up: ApproverUpdate, text: str) -> None:
        """Answer the tap that produced `up` (a short toast). Best effort: a failed answer changes nothing."""
        known = self._callbacks.get(up.update_id)
        if known is None:
            return
        try:
            await self._call(self._bot.answer_callback_query(known, text=text[:200]))
        except SaathiError as exc:
            log.warning("answering a tap failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------ receiving
    def updates(self) -> AsyncIterator[ApproverUpdate]:
        return self._updates()

    async def _updates(self) -> AsyncIterator[RoutedUpdate]:
        """Long poll. An update is yielded only for a registered approver. The offset moves past an update when the
        caller asks for the next one, that is, after `handle` returned; a caller that stops (an error in `handle`)
        leaves it, and Telegram sends it again. Updates of unregistered chats are counted, dropped and passed."""
        while True:
            batch = await self._call(
                self._bot.get_updates(
                    offset=self._offset, timeout=self._poll_s, allowed_updates=["message", "callback_query"]
                )
            )
            for raw in batch:
                routed = await self._route(raw)
                if routed is not None:
                    try:
                        yield routed
                    finally:
                        self._callbacks.pop(routed.update_id, None)
                self._offset = raw.update_id + 1

    async def _route(self, raw: Update) -> RoutedUpdate | None:
        cb, msg = raw.callback_query, raw.message
        sender = cb.from_user if cb is not None else (msg.from_user if msg is not None else None)
        if sender is None or (msg is not None and msg.chat.type != "private"):
            self.dropped += 1
            return None
        found = await self._dir.resolve(sender.id)
        if found is None:
            self.dropped += 1
            return None
        household, approver = found
        kind: Literal["callback", "text"]
        if cb is not None:
            if not cb.data:
                self.dropped += 1
                return None
            self._callbacks[raw.update_id] = cb.id
            kind, data = "callback", cb.data
        else:
            kind, data = "text", (msg.text or "") if msg is not None else ""
        return RoutedUpdate(
            update_id=raw.update_id, approver_id=approver, kind=kind, data=data, household_id=household
        )

    async def health(self) -> ChannelHealth:
        try:
            await self._bot.get_me()
        except TelegramRetryAfter:
            return ChannelHealth.DEGRADED
        except (TelegramAPIError, OSError):
            return ChannelHealth.DOWN
        return ChannelHealth.UP


# --------------------------------------------------------------------------- #
# Show raw
# --------------------------------------------------------------------------- #
async def _registered(tx: Tx, approver: ApproverId) -> bool:
    try:
        cfg = await ConfigRepo.load(tx)
    except ConfigMissing:
        return False
    return any(a.approver_id == approver for a in cfg.approvers)


async def show_raw(store: Store, keys: Opener, audit: AuditSink, up: RoutedUpdate, inbox: InboxId) -> str:
    """The stored words of one message, for a registered approver, when `show_raw` consent is on record.

    The audit row `show_raw` is written in the transaction that reads the text, so a text that was opened is always
    audited, and an audit store that is down means no text (`AuditUnavailable` propagates: fail closed). A refusal is
    audited too (`deny` or `consent_denied`) and returns a fixed sentence, never text."""
    household = up.household_id
    run = RunId(derive_id("run", f"tg{up.update_id}"))
    actor = f"approver:{up.approver_id}"
    refusal: tuple[AuditName, str, str] | None = None
    texts: list[str] = []
    async with store.tx(household=household) as tx:
        if not await _registered(tx, up.approver_id):
            refusal = (AuditName.DENY, "not_registered", SAY_NOT_ALLOWED)
        elif not await gate(tx, SHOW_RAW_USE, SHOW_RAW_RECIPIENT):
            refusal = (AuditName.CONSENT_DENIED, "no_consent", SAY_NO_CONSENT)
        else:
            rows = await tx.fetch(
                "SELECT part, body_sealed, key_id FROM raw_text WHERE inbox_id = $1 AND expires_at > $2"
                " ORDER BY CASE part WHEN 'text' THEN 0 WHEN 'caption' THEN 1 ELSE 2 END",
                inbox,
                tx.now,
            )
            for r in rows:
                opened = await keys.open(tx, bytes(r["body_sealed"]), r["key_id"], aad=aad(household, inbox, r["part"]))
                texts.append(opened.decode())
            await audit.append(
                AuditEvent(
                    household=household, run_id=run, actor=actor, event=AuditName.SHOW_RAW,
                    subject=str(inbox), detail={"parts": len(texts)},
                ),
                tx=tx,
            )  # fmt: skip
    if refusal is not None:
        name, reason, say = refusal
        await audit.append(
            AuditEvent(
                household=household, run_id=run, actor=actor, event=name, subject=str(inbox),
                detail={"reason": reason, "use": SHOW_RAW_USE},
            )
        )  # fmt: skip
        return say
    return "\n\n".join(texts) if texts else SAY_NO_TEXT


# --------------------------------------------------------------------------- #
# Taps
# --------------------------------------------------------------------------- #
class AlertActions(Protocol):
    """What the router needs of the alert service (M14)."""

    async def acknowledge(
        self, household: HouseholdId, event: EventId, approver: ApproverId, via: Literal["tap", "dtmf"]
    ) -> bool: ...

    async def false_alarm(self, household: HouseholdId, event: EventId, approver: ApproverId) -> bool: ...


class _Sender(Protocol):
    async def send_text(self, to: ApproverId, text: str, *, key: IdemKey) -> SendReceipt: ...


Answer = Callable[[ApproverUpdate, str], Awaitable[None]]


class CallbackRouter:
    """One update, one transition. The order is: skip a repeat, act, record the update id. Each action is idempotent on
    its own (acknowledge is one compare-and-set; a repeat is logged late), so a crash between the action and the record
    repeats the action on redelivery and changes nothing more. Recording first would lose an alert tap to a crash."""

    def __init__(
        self,
        store: Store,
        alerts: AlertActions,
        keys: Opener,
        audit: AuditSink,
        caregivers: _Sender,
        *,
        answer: Answer | None = None,
    ) -> None:
        self._store = store
        self._alerts = alerts
        self._keys = keys
        self._audit = audit
        self._caregivers = caregivers
        self._answer = answer
        self._failures: dict[int, int] = {}

    async def _say(self, up: ApproverUpdate, text: str) -> None:
        if self._answer is not None:
            await self._answer(up, text)

    async def _seen(self, up: RoutedUpdate) -> bool:
        async with self._store.tx(household=up.household_id) as tx:
            rows = await tx.fetch("SELECT 1 FROM tg_updates WHERE bot = 'caregiver' AND update_id = $1", up.update_id)
        return bool(rows)

    async def _record(self, up: RoutedUpdate) -> None:
        async with self._store.tx(household=up.household_id) as tx:
            await UpdateRepo.first_seen(tx, "caregiver", up.update_id, up.kind)

    async def handle(self, up: ApproverUpdate) -> None:
        """Return when the update is done and recorded. An error means "not done": the offset must not move, until the
        same update has failed `POISON_AFTER` times: then it is audited as `deny` (`poison_update`) and passed."""
        if not isinstance(up, RoutedUpdate):
            raise TypeError("an update must come from the channel, which names its household")
        if await self._seen(up):
            return
        try:
            await self._dispatch(up)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            n = self._failures.get(up.update_id, 0) + 1
            if n < POISON_AFTER:
                self._failures[up.update_id] = n
                with contextlib.suppress(Exception):
                    await self._say(up, SAY_RETRY)
                raise
            # A poison update: audited and passed, so the updates behind it (an emergency ack tap) are not held back.
            log.error("caregiver update dropped after %d failures: %s", n, type(exc).__name__)
            await self._deny(up, "poison_update")
            await self._record(up)
            self._failures.pop(up.update_id, None)
            return
        self._failures.pop(up.update_id, None)
        await self._record(up)

    async def _deny(self, up: RoutedUpdate, reason: str) -> None:
        await self._audit.append(
            AuditEvent(
                household=up.household_id, run_id=RunId(derive_id("run", f"tg{up.update_id}")),
                actor=f"approver:{up.approver_id}", event=AuditName.DENY, subject=str(up.update_id),
                detail={"reason": reason, "use": "caregiver_callback"},
            )
        )  # fmt: skip

    async def _dispatch(self, up: RoutedUpdate) -> None:
        if up.kind == "text":
            await self._command(up)
            return
        m = _CALLBACK.match(up.data)
        if m is None:
            await self._deny(up, "unknown_callback")
            await self._say(up, SAY_UNKNOWN)
            return
        verb, ident = m.groups()
        async with self._store.tx(household=up.household_id) as tx:
            registered = await _registered(tx, up.approver_id)
        if not registered:  # the allowlist is the household configuration, read now, not at start-up
            await self._deny(up, "not_registered")
            await self._say(up, SAY_NOT_ALLOWED)
            return
        if verb == "ack":
            won = await self._alerts.acknowledge(up.household_id, EventId(ident), up.approver_id, "tap")
            await self._say(up, SAY_ACK if won else SAY_ACK_LATE)
        elif verb == "fa":
            done = await self._alerts.false_alarm(up.household_id, EventId(ident), up.approver_id)
            await self._say(up, SAY_FALSE_ALARM if done else SAY_FALSE_ALARM_NONE)
        else:
            text = await show_raw(self._store, self._keys, self._audit, up, InboxId(ident))
            await self._caregivers.send_text(up.approver_id, text, key=idem_key("raw", str(up.update_id)))
            await self._say(up, "Sent.")

    async def _command(self, up: RoutedUpdate) -> None:
        words = up.data.split(maxsplit=1)
        word = words[0].split("@")[0] if words else ""
        if word not in ("/start", "/status"):
            return  # text is never an instruction: no "approved", no free words
        key = idem_key("cmd", str(up.update_id))
        if word == "/start":
            await self._caregivers.send_text(up.approver_id, SAY_WELCOME, key=key)
            return
        async with self._store.tx(household=up.household_id) as tx:
            opened = await tx.fetch("SELECT count(*) AS n FROM alert_events WHERE state = 'open'")
            newest = await tx.fetch("SELECT max(received_at) AS at FROM inbox")
            now = tx.now
        lag = newest[0]["at"] if newest else None
        newest_line = (
            f"Newest message from the elder: {int((now - lag) / timedelta(minutes=1))} minutes ago."
            if lag is not None
            else "No messages yet."
        )
        await self._caregivers.send_text(up.approver_id, f"Open alerts: {opened[0]['n']}. {newest_line}", key=key)


# --------------------------------------------------------------------------- #
# Start-up
# --------------------------------------------------------------------------- #
async def serve(
    s: Settings,
    build: Callable[[Settings], tuple[TelegramCaregiverChannel, CallbackRouter]],
    *,
    backoff_s: float = RETRY_BACKOFF_S,
    stop: asyncio.Event | None = None,
) -> None:
    """Start-up: `require_secrets` first (WU-14), then compose and poll. An error in `handle` or in the poll is waited
    out and the same update is fetched again, because the offset only moves after `handle` returned."""
    require_secrets(s)
    channel, router = build(s)
    while stop is None or not stop.is_set():
        updates = channel.updates()
        try:
            async for up in updates:
                await router.handle(up)
                if stop is not None and stop.is_set():
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # an outage or a bad update is waited out, never fatal
            log.warning("caregiver bot round failed: %s", type(exc).__name__)
            await asyncio.sleep(backoff_s)
        finally:
            await updates.aclose()  # type: ignore[attr-defined]


def run() -> None:  # pragma: no cover - entry point of `saathi-bot`
    from saathi.config import ConfigError, get_settings

    logging.basicConfig(level=logging.INFO)
    require_secrets(get_settings())
    raise ConfigError("the caregiver bot composition (store, alerts, bot token) is wired with the compose service")
