"""The edge application (M07): the webhook routes and the health route, nothing else."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping

from fastapi import FastAPI, HTTPException, Query, Request, Response

from saathi.config import Settings, secret_text
from saathi.domain import Source
from saathi.edge.adapters import InboundAdapter
from saathi.edge.handler import MAX_BODY_BYTES, EdgeCounters, EdgeDeps, handle_inbound
from saathi.edge.health import add_health_route
from saathi.guards.radar import RadarResult
from saathi.ports import Clock, TraceExporter
from saathi.store import Store
from saathi.store.keys import KeyRing
from saathi.trace import NullExporter


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """The body, or None when it is over `limit`. A declared length is refused before a byte is read."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isascii() and declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def create_edge_app(
    settings: Settings,
    store: Store,
    keys: KeyRing,
    adapters: Mapping[Source, InboundAdapter],
    radar: Callable[[str | None], RadarResult],
    clock: Clock,
    *,
    sender_key: bytes,
    counters: EdgeCounters | None = None,
    max_body: int = MAX_BODY_BYTES,
    trace: TraceExporter | None = None,
) -> FastAPI:
    deps = EdgeDeps(
        store=store, keys=keys, adapters=adapters, radar=radar, clock=clock, sender_key=sender_key,
        counters=counters or EdgeCounters(), max_body=max_body, trace=trace or NullExporter(),
    )  # fmt: skip
    app = FastAPI(title="saathi-edge", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.edge = deps

    async def answer(request: Request, source: Source) -> Response:
        raw = await _read_capped(request, deps.max_body)
        if raw is None:
            deps.counters.inc("body_too_large")
            return Response(status_code=413)
        status = await handle_inbound(raw, dict(request.headers), source, deps=deps)
        return Response(status_code=status.value)

    if Source.WHATSAPP in adapters and settings.wa_webhook_path:
        expected = secret_text(settings.wa_verify_token, settings.wa_verify_token_file)

        @app.get(f"/wa/{settings.wa_webhook_path}")
        async def wa_verify(
            mode: str = Query("", alias="hub.mode"),
            token: str = Query("", alias="hub.verify_token"),
            challenge: str = Query("", alias="hub.challenge"),
        ) -> Response:
            if mode == "subscribe" and expected and hmac.compare_digest(token.encode(), expected.encode()):
                return Response(content=challenge, media_type="text/plain")
            raise HTTPException(status_code=403)

        @app.post(f"/wa/{settings.wa_webhook_path}")
        async def wa_inbound(request: Request) -> Response:
            return await answer(request, Source.WHATSAPP)

    if Source.TELEGRAM_ELDER in adapters and settings.tg_elder_webhook_path:

        @app.post(f"/tg/elder/{settings.tg_elder_webhook_path}")
        async def tg_inbound(request: Request) -> Response:
            return await answer(request, Source.TELEGRAM_ELDER)

    add_health_route(app, settings.health_path, store, clock)
    return app
