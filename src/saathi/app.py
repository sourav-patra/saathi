"""FastAPI service: WhatsApp webhook + Telegram bot in one process.

    GET  /webhooks/whatsapp  Meta verification handshake
    POST /webhooks/whatsapp  inbound messages (signature-verified)
    GET  /healthz            liveness
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager

from aiogram import Bot
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response

from saathi.channels.telegram import (
    RuntimeFlags,
    TelegramNotifier,
    build_dispatcher,
    build_router,
)
from saathi.channels.whatsapp import WhatsAppClient, parse_webhook, verify_signature
from saathi.config import Settings, get_settings
from saathi.legacy import Channel, InboundMessage
from saathi.llm.gateway import BudgetLedger, Gateway, tiers_from_settings
from saathi.observability import flush as flush_traces
from saathi.observability import init_tracing
from saathi.perception.asr import build_asr
from saathi.pipeline import Deps, build_graph, run_request

log = logging.getLogger("saathi")


class _Dedupe:
    """Meta retries webhooks, so drop provider message ids we've already seen."""

    def __init__(self, size: int = 2048):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def first_time(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen[key] = None
        if len(self._seen) > self._size:
            self._seen.popitem(last=False)
        return True


class _Media:
    def __init__(self, wa: WhatsAppClient):
        self.wa = wa
        self.prefetched: dict[str, tuple[bytes, str]] = {}

    async def fetch(self, msg: InboundMessage) -> tuple[bytes, str]:
        if msg.id in self.prefetched:
            return self.prefetched.pop(msg.id)
        if msg.channel == Channel.WHATSAPP and msg.media_id:
            return await self.wa.download_media(msg.media_id)
        raise ValueError("no media available")


class _ElderReplier:
    def __init__(self, wa: WhatsAppClient, default_elder: str | None):
        self.wa = wa
        self.default_elder = default_elder

    async def reply(self, msg: InboundMessage, text: str) -> None:
        to = msg.sender_phone_e164 or self.default_elder
        if not to:
            raise ValueError("no elder phone to reply to")
        await self.wa.send_text(to, text)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    dedupe = _Dedupe()
    flags = RuntimeFlags()
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_tracing(
            host=s.langfuse_host,
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key.get_secret_value(),
            environment=s.env,
        )
        wa = WhatsAppClient(
            phone_number_id=s.wa_phone_number_id,
            access_token=s.wa_access_token.get_secret_value(),
            graph_version=s.wa_graph_version,
        )
        bot = Bot(s.tg_bot_token.get_secret_value())
        notifier = TelegramNotifier(bot, s.tg_primary_approver_id, s.tg_backup_approver_ids)
        media = _Media(wa)
        deps = Deps(
            gateway=Gateway(
                tiers_from_settings(s), BudgetLedger(s.paid_monthly_cap_inr), usd_to_inr=s.usd_to_inr
            ),
            asr=build_asr(s.asr_provider, whispercpp_url=s.whispercpp_url),
            media=media,
            elder=_ElderReplier(wa, s.elder_phones[0] if s.elder_phones else None),
            caregiver=notifier,
            caregiver_name=s.caregiver_name,
        )
        graph = build_graph(deps)

        async def on_forward(msg: InboundMessage, data: bytes | None, mime: str | None) -> None:
            if data is not None:
                media.prefetched[msg.id] = (data, mime or "application/octet-stream")
            await run_request(graph, msg)

        dp = build_dispatcher(build_router(notifier=notifier, flags=flags, on_forward=on_forward))
        poller = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        state.update(graph=graph, flags=flags)
        log.info("saathi started")
        try:
            yield
        finally:
            poller.cancel()
            await bot.session.close()
            await deps.gateway.aclose()
            flush_traces()

    app = FastAPI(title="saathi", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "paused": flags.paused}

    @app.get("/webhooks/whatsapp")
    async def wa_verify(
        mode: str = Query("", alias="hub.mode"),
        token: str = Query("", alias="hub.verify_token"),
        challenge: str = Query("", alias="hub.challenge"),
    ) -> Response:
        expected = s.wa_verify_token.get_secret_value()
        if mode == "subscribe" and expected and token == expected:
            return Response(content=challenge, media_type="text/plain")
        raise HTTPException(status_code=403)

    @app.post("/webhooks/whatsapp")
    async def wa_inbound(request: Request, background: BackgroundTasks) -> dict:
        body = await request.body()
        if not verify_signature(
            s.wa_app_secret.get_secret_value(), body, request.headers.get("X-Hub-Signature-256")
        ):
            raise HTTPException(status_code=401, detail="bad signature")
        allowed = set(s.elder_phones)
        for msg in parse_webhook(await request.json()):
            if msg.sender_phone_e164 not in allowed:
                log.warning("ignoring message from unregistered number")
                continue
            if not dedupe.first_time(msg.provider_message_id):
                continue
            background.add_task(run_request, state["graph"], msg)
        return {"ok": True}  # ack fast; processing continues in the background

    return app


def run() -> None:  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)
