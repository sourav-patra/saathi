"""The worker's entry point and its composition from settings (M12; RQ-79, RQ-102).

`build_worker` is the one place that names the concrete adapters: the core database login, the key ring from the
key-encryption key file, the elder channel, the local speech engines, the media and voice stores, and the audit sink.
`require_secrets` and `require_listed_hosts` run in `serve` before this is called. Every platform call goes through the
egress proxy when `egress_url` is set (a model-driven container has no other route). This module sits under `channels/`
because it is the only worker code that may import a platform client.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from saathi.audit.sink import PgAuditSink
from saathi.channels import PersonDirectory
from saathi.channels.telegram_elder import TelegramClient, TelegramElderChannel
from saathi.channels.whatsapp import WhatsAppClient, WhatsAppElderChannel
from saathi.clock import SystemClock
from saathi.config import ConfigError, Settings, read_secret_file, secret_text
from saathi.media import LocalMediaStore
from saathi.perception.asr import LocalAsrClient
from saathi.ports import ElderChannel
from saathi.store import Role, Store, create_pool
from saathi.store.keys import KeyRing
from saathi.trace import build_exporter
from saathi.voice.codec import OpusCodec
from saathi.voice.store import PgVoiceStore
from saathi.worker.loop import serve
from saathi.worker.steps import WorkerConfig, WorkerDeps
from saathi.worker.wiring import CoreDirectory, PendingCaregiverChannel

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


async def build_worker(s: Settings) -> WorkerDeps:
    """Open the core pool and build every dependency of the worker. `ConfigError` names a missing setting."""
    if not s.kek_file:
        raise ConfigError("kek_file is not set")
    kek = read_secret_file(s.kek_file)
    if len(kek) != 32:
        raise ConfigError("the key-encryption key file must hold 32 bytes")
    _elder_platform(s)  # a missing channel is refused before the database is opened
    clock = SystemClock()
    keys = KeyRing(kek)
    store = Store(await create_pool(s.db_url_core), Role.CORE, clock)
    try:
        return WorkerDeps(
            store=store,
            keys=keys,
            elder=build_elder_channel(s, CoreDirectory(store, keys), clock),
            caregiver=PendingCaregiverChannel(),
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



def main() -> None:  # pragma: no cover
    from saathi.config import get_settings

    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(get_settings(), build_worker))


__all__ = ["build_elder_channel", "build_owner_alert", "build_worker", "main"]
