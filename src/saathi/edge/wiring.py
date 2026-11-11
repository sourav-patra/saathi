"""Composition of the edge from settings (M07). Platform names stay in `edge/`, so the root module names none."""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from saathi.clock import SystemClock
from saathi.config import Settings, read_secret_file, secret_text
from saathi.domain import Source
from saathi.edge.adapters import InboundAdapter, TelegramElderInbound, WhatsAppInbound
from saathi.edge.app import create_edge_app
from saathi.errors import SaathiError
from saathi.guards.radar import scan
from saathi.store import Role, Store, create_pool
from saathi.store.keys import KeyRing
from saathi.trace import build_exporter


class EdgeConfigError(SaathiError):
    """The edge cannot start: a required value is missing. The message names the setting, never its value."""


def build_adapters(s: Settings) -> dict[Source, InboundAdapter]:
    out: dict[Source, InboundAdapter] = {}
    wa_secret = secret_text(s.wa_app_secret, s.wa_app_secret_file)
    if wa_secret:
        out[Source.WHATSAPP] = WhatsAppInbound(wa_secret)
    tg_secret = secret_text(s.tg_elder_secret, s.tg_elder_secret_file)
    if tg_secret:
        out[Source.TELEGRAM_ELDER] = TelegramElderInbound(tg_secret)
    return out


async def build_edge(s: Settings) -> tuple[FastAPI, Store]:
    """Opens the pool as the edge role and builds the app. The edge holds no key-encryption key or approval secret."""
    if not s.sender_hmac_key_file:
        raise EdgeConfigError("sender_hmac_key_file is not set")
    sender_key = read_secret_file(s.sender_hmac_key_file)
    adapters = build_adapters(s)
    if not adapters:
        raise EdgeConfigError("no inbound channel has a secret set")
    clock = SystemClock()
    store = Store(await create_pool(s.db_url_edge), Role.EDGE, clock)
    trace = build_exporter(s.trace_dir, clock)
    app = create_edge_app(s, store, KeyRing(kek=None), adapters, scan, clock, sender_key=sender_key, trace=trace)
    return app, store


EDGE_LIMIT_CONCURRENCY = 100  # open connections the edge accepts at once; more get a 503 instead of queueing


def uvicorn_config(app: FastAPI, *, host: str, port: int) -> uvicorn.Config:
    """Server settings of the public edge: a cap on concurrent connections bounds slow or partial uploads."""
    return uvicorn.Config(app, host=host, port=port, log_level="info", limit_concurrency=EDGE_LIMIT_CONCURRENCY)


async def serve(s: Settings, *, host: str = "0.0.0.0", port: int | None = None) -> None:  # pragma: no cover
    port = s.edge_port if port is None else port
    app, store = await build_edge(s)
    try:
        await uvicorn.Server(uvicorn_config(app, host=host, port=port)).serve()
    finally:
        await store.close()


def main(settings: Settings | None = None) -> None:  # pragma: no cover
    import asyncio

    from saathi.config import get_settings

    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(settings or get_settings()))
