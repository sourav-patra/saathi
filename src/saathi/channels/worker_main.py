"""The worker's entry point and its composition from settings (M12; RQ-79, RQ-102).

`build_worker` and `build_bot` are the places that name the concrete adapters: the core database login, the key ring
from the key-encryption key file, the elder channel, the caregiver bot channel over the approver directory, the alert
service, the local speech engines, the media and voice stores, the audit sink and the trace exporter.
`require_secrets` and `require_listed_hosts` run in `serve` before this is called. Every platform call goes through the
egress proxy when `egress_url` is set (a model-driven container has no other route). This module sits under `channels/`
because it is the only worker code that may import a platform client.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from saathi.alerts.service import AlertService
from saathi.alerts.stages import TelegramStageA, UnavailableEscalator
from saathi.audit.sink import PgAuditSink
from saathi.channels import PersonDirectory
from saathi.channels import telegram as caregiver_bot
from saathi.channels.telegram import CallbackRouter, PgApproverDirectory, TelegramCaregiverChannel
from saathi.channels.telegram_elder import TelegramClient, TelegramElderChannel
from saathi.channels.whatsapp import WhatsAppClient, WhatsAppElderChannel
from saathi.clock import SystemClock
from saathi.config import ConfigError, Settings, read_secret_file, require_secrets, secret_text
from saathi.domain import Source
from saathi.ids import ApproverId, EventId, HouseholdId
from saathi.llm.ledger import PgLedger, SpendNotices, store_approvers
from saathi.media import LocalMediaStore
from saathi.perception.asr import LocalAsrClient
from saathi.ports import Clock, ElderChannel
from saathi.rides.taps import PgRideTaps
from saathi.store import Role, Store, create_pool
from saathi.store.keys import KeyRing
from saathi.store.repos import SignoffRepo
from saathi.trace import build_exporter
from saathi.voice.codec import OpusCodec
from saathi.voice.store import PgVoiceStore
from saathi.worker.loop import serve
from saathi.worker.scope import ADDRESS_SCOPE, acting_for
from saathi.worker.steps import WorkerConfig, WorkerDeps
from saathi.worker.wiring import CoreDirectory

log = logging.getLogger(__name__)


def _elder_platform(s: Settings) -> tuple[str, str]:
    """(platform, token): WhatsApp when its credentials are set, else the Telegram elder bot (RQ-107 is Phase 1)."""
    wa_token = secret_text(s.wa_access_token, s.wa_access_token_file)
    if s.wa_phone_number_id and wa_token:
        return "whatsapp", wa_token
    tg_token = secret_text(s.tg_elder_token, s.tg_elder_token_file)
    if tg_token:
        return "telegram_elder", tg_token
    raise ConfigError("no elder channel has credentials set")


def build_elder_channel(s: Settings, people: PersonDirectory, clock: SystemClock) -> ElderChannel:
    platform, token = _elder_platform(s)
    egress = s.egress_url.rstrip("/")
    if platform == "whatsapp":
        client = WhatsAppClient(
            phone_number_id=s.wa_phone_number_id,
            access_token=token,
            graph_version=s.wa_graph_version,
            api_base=f"{egress}/whatsapp" if egress else "",
            media_base=f"{egress}/whatsapp-media" if egress else "",
        )
        return WhatsAppElderChannel(client, people, clock)
    api = f"{egress}/telegram" if egress else "https://api.telegram.org"
    return TelegramElderChannel(TelegramClient(token=token, api=api), people)


def build_owner_alert(s: Settings) -> Callable[[str], Awaitable[None]]:
    """A short code to the ops chat of the caregiver bot. With no bot or chat set, the code is only logged."""
    token = secret_text(s.tg_caregiver_token, s.tg_caregiver_token_file)
    if not (token and s.tg_ops_chat_id):
        log.warning("no ops chat is set: owner alerts are logged only")

        async def log_only(what: str) -> None:
            log.warning("owner alert: %s", what)

        return log_only
    egress = s.egress_url.rstrip("/")
    client = TelegramClient(token=token, api=f"{egress}/telegram" if egress else "https://api.telegram.org")

    async def alert(what: str) -> None:
        try:
            await client.call("sendMessage", {"chat_id": s.tg_ops_chat_id, "text": f"saathi: {what}"})
        except httpx.HTTPError:
            log.warning("owner alert could not be sent: %s", what)

    return alert


def _kek(s: Settings) -> bytes:
    if not s.kek_file:
        raise ConfigError("kek_file is not set")
    kek = read_secret_file(s.kek_file)
    if len(kek) != 32:
        raise ConfigError("the key-encryption key file must hold 32 bytes")
    return kek


def _caregiver_token(s: Settings) -> str:
    token = secret_text(s.tg_caregiver_token, s.tg_caregiver_token_file)
    if not token:
        raise ConfigError("the caregiver bot token is not set")
    return token


def build_bot_client(s: Settings) -> Bot:
    """The caregiver Bot API client; through the egress proxy when `egress_url` is set."""
    token = _caregiver_token(s)
    egress = s.egress_url.rstrip("/")
    if egress:
        return Bot(token=token, session=AiohttpSession(api=TelegramAPIServer.from_base(f"{egress}/telegram")))
    return Bot(token=token)


async def _scoped_households() -> list[HouseholdId]:
    """The household the current step acts for: the approver directory reads a chat only inside it (RQ-105)."""
    household = ADDRESS_SCOPE.get()
    return [household] if household is not None else []


def build_caregiver_channel(s: Settings, store: Store, clock: Clock) -> TelegramCaregiverChannel:
    """The live caregiver channel over the approver directory (WU-18 ruling 1); it replaces the stand-in."""
    directory = PgApproverDirectory(store.scan, store, _scoped_households)
    return TelegramCaregiverChannel(build_bot_client(s), directory, clock)


class ScopedAlertService(AlertService):
    """The alert service with the household scope set for each event, so the approver directory and the elder's
    sealed address open inside that household only."""

    async def step(self, household: HouseholdId, event: EventId) -> None:
        with acting_for(household):
            await super().step(household, event)

    async def acknowledge(
        self, household: HouseholdId, event: EventId, approver: ApproverId, via: Literal["tap", "dtmf"]
    ) -> bool:
        with acting_for(household):
            return await super().acknowledge(household, event, approver, via)

    async def false_alarm(self, household: HouseholdId, event: EventId, approver: ApproverId) -> bool:
        with acting_for(household):
            return await super().false_alarm(household, event, approver)


def build_alert_service(
    s: Settings,
    store: Store,
    caregiver: TelegramCaregiverChannel,
    elder: ElderChannel,
    voice: PgVoiceStore,
    audit: PgAuditSink,
    clock: Clock,
) -> AlertService:
    """Stage a on the caregiver bot; stages b and c are unavailable in Phase 0 (fake providers only)."""
    return ScopedAlertService(
        store,
        caregiver,
        {Source(elder.kind): elder},
        voice,
        {"a": TelegramStageA(caregiver), "b": UnavailableEscalator("b"), "c": UnavailableEscalator("c")},
        audit,
        SignoffRepo,
        clock,
        owner_alert=build_owner_alert(s),
        trace=build_exporter(s.trace_dir, clock),
    )


def build_ledger(
    s: Settings, store: Store, household: HouseholdId, caregiver: TelegramCaregiverChannel
) -> PgLedger:
    """The spend ledger of one household with both notices wired (RQ-48) and the scope and zone from the settings."""
    notices = SpendNotices(caregiver, store_approvers(store), build_owner_alert(s))
    return PgLedger(
        store, household, on_alert=notices.on_alert, on_cap=notices.on_cap, scope=s.ledger_scope, tz=s.ledger_tz
    )


@dataclass
class BotParts:
    store: Store
    channel: TelegramCaregiverChannel
    router: CallbackRouter
    alerts: AlertService


async def build_bot(s: Settings) -> BotParts:
    """The caregiver bot: the approver directory, the alert service, the key opener and the audit sink (WU-18)."""
    kek = _kek(s)
    _caregiver_token(s)
    _elder_platform(s)  # a missing setting is refused before the database is opened
    clock = SystemClock()
    keys = KeyRing(kek)
    store = Store(await create_pool(s.db_url_core), Role.CORE, clock)
    try:
        channel = build_caregiver_channel(s, store, clock)
        audit = PgAuditSink(store, keys)
        elder = build_elder_channel(s, CoreDirectory(store, keys), clock)
        voice = PgVoiceStore(store, Path(s.media_dir).parent / "clips")
        alerts = build_alert_service(s, store, channel, elder, voice, audit, clock)
        trace = build_exporter(s.trace_dir, clock)
        router = CallbackRouter(
            store, alerts, keys, audit, channel, answer=channel.answer, trace=trace,
            rides=PgRideTaps(store), edit=channel.edit_card,
        )  # fmt: skip
        return BotParts(store, channel, router, alerts)
    except BaseException:
        await store.close()
        raise


BOT_LOCK_KEY = 0x5AA7_0B07  # the fixed session advisory lock key of the single caregiver bot poller


class BotAlreadyRunning(ConfigError):
    """Another process holds the caregiver bot lock; two pollers would split updates and dedupe state."""


async def serve_bot(s: Settings, build: Callable[[Settings], Awaitable[BotParts]] = build_bot) -> None:
    """Start-up of the caregiver bot: `require_secrets` first, then compose, then take the single-poller advisory
    lock (refuse to start if held, e.g. `saathi-bot` beside `saathi-worker`), then poll beside the alert loop."""
    require_secrets(s)
    parts = await build(s)
    try:
        async with parts.store.singleton(BOT_LOCK_KEY) as held:
            if not held:
                raise BotAlreadyRunning("another caregiver bot poller holds the lock; this one does not start")
            alerts = asyncio.create_task(parts.alerts.run_forever())
            try:
                await caregiver_bot.serve(s, lambda _s: (parts.channel, parts.router))
            finally:
                alerts.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await alerts
    finally:
        await parts.store.close()


async def build_worker(s: Settings) -> WorkerDeps:
    """Open the core pool and build every dependency of the worker. `ConfigError` names a missing setting."""
    kek = _kek(s)
    _elder_platform(s)  # a missing channel is refused before the database is opened
    _caregiver_token(s)
    clock = SystemClock()
    keys = KeyRing(kek)
    store = Store(await create_pool(s.db_url_core), Role.CORE, clock)
    try:
        return WorkerDeps(
            store=store,
            keys=keys,
            elder=build_elder_channel(s, CoreDirectory(store, keys), clock),
            caregiver=build_caregiver_channel(s, store, clock),
            voice=PgVoiceStore(store, Path(s.media_dir).parent / "clips"),
            asr=LocalAsrClient(s.whispercpp_url),
            codec=OpusCodec(),
            media=LocalMediaStore(s.media_dir),
            audit=PgAuditSink(store, keys),
            clock=clock,
            owner_alert=build_owner_alert(s),
            cfg=WorkerConfig(),
            trace=build_exporter(s.trace_dir, clock),
        )
    except BaseException:
        await store.close()
        raise



async def serve_all(s: Settings) -> None:
    """The worker service of Phase 0 (two services of one image): the worker loop and the caregiver bot with the alert
    loop in one process, so an emergency is stepped wherever the worker runs. `require_secrets` runs first."""
    require_secrets(s)
    await supervise(lambda: serve(s, build_worker), lambda: serve_bot(s))


BOT_RESTART_SECONDS = 30.0


async def supervise(
    worker: Callable[[], Awaitable[None]],
    bot: Callable[[], Awaitable[None]],
    *,
    restart_after: float = BOT_RESTART_SECONDS,
) -> None:
    """The elder worker and the caregiver bot as separate tasks: a bot failure (a bad token, a held lock, a crash
    loop) is logged and the bot restarted after a pause, while the worker keeps running. The worker's own end or
    failure ends the process (compose restarts it), and the bot task is cancelled with it."""

    async def bot_loop() -> None:
        while True:
            try:
                await bot()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the bot never takes the elder worker down
                log.warning("caregiver bot stopped (%s); restarting in %.0fs", type(exc).__name__, restart_after)
            await asyncio.sleep(restart_after)

    bot_task = asyncio.create_task(bot_loop())
    try:
        await worker()
    finally:
        bot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bot_task


def main() -> None:  # pragma: no cover - entry point of `saathi-worker`
    from saathi.config import get_settings

    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve_all(get_settings()))


def main_bot() -> None:  # pragma: no cover - `saathi-bot`: the bot and alert loop alone, never beside `saathi-worker`
    from saathi.config import get_settings

    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve_bot(get_settings()))


__all__ = [
    "BOT_LOCK_KEY",
    "BotAlreadyRunning",
    "BotParts",
    "ScopedAlertService",
    "build_alert_service",
    "build_bot",
    "build_bot_client",
    "build_caregiver_channel",
    "build_elder_channel",
    "build_ledger",
    "build_owner_alert",
    "build_worker",
    "main",
    "main_bot",
    "serve_all",
    "serve_bot",
    "supervise",
]
