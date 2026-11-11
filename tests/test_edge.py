"""LT-07: the edge handler, rate state and health route (M07; RQ-79, 81, 16, 18, 104; TQ-01, 02, 08).

Tests that need rows use a real database as the edge role and skip without SAATHI_TEST_DSN. The `_fake` tests run
against a store with no database and cover the order of checks.
"""

from __future__ import annotations

import ast
import json
from datetime import timedelta
from pathlib import Path

from conftest import PgDb
from edge_helpers import (
    NOW,
    STRANGER_NUMBER,
    EdgeFakeStore,
    client,
    fake_app,
    pg_edge,
    seed_edge_household,
    su_fetch,
    tg_body,
    tg_headers,
    tg_url,
    wa_body,
    wa_msg,
    wa_sign,
    wa_url,
)
from fakes import FakeClock
from store_helpers import open_store

from saathi.edge import EdgeCounters, rate_state
from saathi.guards.radar import RadarResult, scan
from saathi.health import ProbeResult, ProbeStatus, beat_once
from saathi.ids import ElderId
from saathi.store import Role

SRC = Path(__file__).resolve().parent.parent / "src" / "saathi"
EDGE_FILES = sorted((SRC / "edge").glob("*.py")) + [SRC / "app.py"]
EMERGENCY = "mujhe seene mein dard ho raha hai"


async def post(c, body: bytes, headers: dict[str, str] | None = None, url: str | None = None):
    return await c.post(url or wa_url(), content=body, headers=wa_sign(body) if headers is None else headers)


# --- the order of checks, with no database -----------------------------------------------------------------


async def test_bad_signature_401_no_call_fake():
    store = EdgeFakeStore()
    counters = EdgeCounters()
    async with client(fake_app(store, counters=counters)) as c:
        body = wa_body(wa_msg())
        r = await post(c, body, wa_sign(body, "wrong"))
        assert r.status_code == 401
        assert (await post(c, body, {})).status_code == 401  # no header at all
    assert store.scan.resolved == [] and store.transactions == []
    assert counters.get("bad_signature") == 2


async def test_signature_is_checked_before_the_json_is_parsed_fake():
    store = EdgeFakeStore()
    async with client(fake_app(store)) as c:
        r = await post(c, b"not json", wa_sign(b"not json", "wrong"))
    assert r.status_code == 401  # never 400: nothing is parsed before the signature holds


async def test_bad_json_400_fake():
    store = EdgeFakeStore()
    async with client(fake_app(store)) as c:
        assert (await post(c, b"{broken")).status_code == 400
        assert (await post(c, b'{"entry": 5}')).status_code == 400  # not the documented envelope
        assert (await post(c, b"[1, 2]")).status_code == 400
    assert store.transactions == []


async def test_large_body_413_fake():
    store = EdgeFakeStore()
    async with client(fake_app(store, max_body=2048)) as c:
        big = wa_body(wa_msg(text={"body": "x" * 4096}))
        assert (await post(c, big)).status_code == 413
        assert (await c.post(wa_url(), content=big, headers={"content-length": "999999999"})).status_code == 413
    assert store.transactions == [] and store.scan.resolved == []


async def test_unknown_sender_hash_row_no_outbound_fake():
    store = EdgeFakeStore()
    async with client(fake_app(store)) as c:
        body = wa_body(wa_msg(sender=STRANGER_NUMBER.lstrip("+")))
        r = await post(c, body)
    assert r.status_code == 200 and r.content == b""  # no reply of any kind
    assert len(store.scan.rejected) == 1
    h, reason = store.scan.rejected[0]
    assert reason == "unknown_sender" and len(h) == 64 and STRANGER_NUMBER.lstrip("+") not in h
    assert store.transactions == []  # no household, no inbox row


async def test_rate_limited_message_is_scanned_and_stored_with_not_before_fake():
    later = NOW + timedelta(minutes=5)
    store = EdgeFakeStore(not_before=later)
    seen: list[str | None] = []

    def radar(text):
        seen.append(text)
        return scan(text)

    async with client(fake_app(store, radar=radar)) as c:
        body = wa_body(wa_msg(text={"body": EMERGENCY}))
        assert (await post(c, body)).status_code == 200
    assert seen == [EMERGENCY]  # the keyword match runs on a rate-limited message
    (sql, args), = store.statements("INSERT INTO inbox")
    assert args[10] == later  # not_before
    assert store.statements("INSERT INTO alert_events")


async def test_radar_failure_opens_an_emergency_fake():
    store = EdgeFakeStore()

    def broken(text):
        raise RuntimeError("scan failed")

    async with client(fake_app(store, radar=broken)) as c:
        assert (await post(c, wa_body(wa_msg()))).status_code == 200
    (_, args), = store.statements("INSERT INTO alert_events")
    assert "emergency" in args


async def test_edge_insert_failure_returns_5xx_fake():
    store = EdgeFakeStore()
    store.fail("tx", RuntimeError("down"))
    async with client(fake_app(store)) as c:
        r = await post(c, wa_body(wa_msg()))
    assert r.status_code == 503


async def test_callback_update_is_counted_and_dropped_fake():
    store = EdgeFakeStore()
    counters = EdgeCounters()
    body = json.dumps({"update_id": 2, "callback_query": {"id": "1", "data": "approve:q1"}}).encode()
    async with client(fake_app(store, counters=counters)) as c:
        r = await c.post(tg_url(), content=body, headers=tg_headers())
    assert r.status_code == 200 and store.transactions == []
    assert counters.get("elder_callback_ignored") == 1


async def test_verification_handshake_and_unmounted_routes_fake():
    async with client(fake_app(EdgeFakeStore())) as c:
        good = {"hub.mode": "subscribe", "hub.verify_token": "synthetic-verify", "hub.challenge": "77"}
        ok = await c.get(wa_url(), params=good)
        assert ok.status_code == 200 and ok.text == "77"
        bad = await c.get(wa_url(), params={**good, "hub.verify_token": "nope"})
        assert bad.status_code == 403
        assert (await c.post("/wa/other-path", content=b"{}")).status_code == 404
        assert (await c.get("/docs")).status_code == 404


# --- rows, on a real database ------------------------------------------------------------------------------


async def test_bad_signature_401_no_row(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        body = wa_body(wa_msg())
        r = await post(c, body, wa_sign(body, "wrong"))
        assert r.status_code == 401
        assert c.app.state.edge.counters.get("bad_signature") == 1  # type: ignore[attr-defined]
    assert await su_fetch(pg_db, "SELECT 1 FROM inbox") == []
    assert await su_fetch(pg_db, "SELECT 1 FROM rejected_inbound") == []


async def test_bad_json_400(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        assert (await post(c, b"{oops")).status_code == 400
    assert await su_fetch(pg_db, "SELECT 1 FROM inbox") == []


async def test_large_body_413(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        big = wa_body(wa_msg(text={"body": "x" * (2 * 1024 * 1024)}))
        assert (await post(c, big)).status_code == 413
    assert await su_fetch(pg_db, "SELECT 1 FROM inbox") == []


async def test_unknown_sender_hash_row_no_outbound(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        r = await post(c, wa_body(wa_msg(sender=STRANGER_NUMBER.lstrip("+"))))
    assert r.status_code == 200 and r.content == b""
    rows = await su_fetch(pg_db, "SELECT sender_hmac, reason FROM rejected_inbound")
    assert len(rows) == 1 and rows[0]["reason"] == "unknown_sender"
    assert STRANGER_NUMBER.lstrip("+") not in rows[0]["sender_hmac"] and len(rows[0]["sender_hmac"]) == 64
    assert await su_fetch(pg_db, "SELECT 1 FROM inbox") == []


async def test_text_persisted_before_200_and_sealed(pg_db: PgDb):
    canary = "canary-phrase-5c2e"
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        assert (await post(c, wa_body(wa_msg(text={"body": canary})))).status_code == 200
    (row,) = await su_fetch(pg_db, "SELECT * FROM inbox")
    assert row["state"] == "received" and row["kind"] == "text" and row["elder_id"] == "eh1"
    assert row["provider_message_id"] == "wa:wamid.1" and row["not_before"] is None
    (part,) = await su_fetch(pg_db, "SELECT part, body_sealed FROM raw_text")
    assert part["part"] == "text" and canary.encode() not in bytes(part["body_sealed"])


async def test_sticker_stored_unsupported(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        assert (await post(c, wa_body(wa_msg("sticker", sticker={"id": "1"})))).status_code == 200
    (row,) = await su_fetch(pg_db, "SELECT kind, state FROM inbox")
    assert row["kind"] == "unsupported" and row["state"] == "received"


async def test_caption_is_stored_as_its_own_part(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        msg = wa_msg("image", image={"id": "1001", "mime_type": "image/jpeg", "caption": "dekho"})
        assert (await post(c, wa_body(msg))).status_code == 200
    (row,) = await su_fetch(pg_db, "SELECT kind, media_ref FROM inbox")
    assert row["kind"] == "image" and row["media_ref"] == "1001"
    assert [r["part"] for r in await su_fetch(pg_db, "SELECT part FROM raw_text")] == ["caption"]


async def test_rate_limit_persists_acks_once_scans_radar(pg_db: PgDb):
    await seed_edge_household(pg_db, rate=(2, 600))
    seen: list[str | None] = []

    def radar(text):
        seen.append(text)
        return scan(text)

    async with pg_edge(pg_db, radar=radar) as c:
        for n in range(1, 3):
            assert (await post(c, wa_body(wa_msg(mid=f"wamid.{n}")))).status_code == 200
        r = await post(c, wa_body(wa_msg(mid="wamid.3", text={"body": EMERGENCY})))
        assert r.status_code == 200  # over the limit: persisted, never refused
    rows = await su_fetch(pg_db, "SELECT provider_message_id, not_before, acked_at FROM inbox ORDER BY received_at, id")
    assert len(rows) == 3
    over = [r for r in rows if r["provider_message_id"] == "wa:wamid.3"][0]
    assert over["not_before"] is not None and over["acked_at"] is None  # the worker acknowledges it once
    assert all(r["not_before"] is None for r in rows if r["provider_message_id"] != "wa:wamid.3")
    assert EMERGENCY in seen  # the keyword match ran on the message over the limit
    alerts = await su_fetch(pg_db, "SELECT kind, origin, inbox_id FROM alert_events")
    assert [(a["kind"], a["origin"]) for a in alerts] == [("emergency", "radar_edge")]


async def test_over_limit_audio_opens_an_unscanned_alert(pg_db: PgDb):
    await seed_edge_household(pg_db, rate=(1, 600))
    async with pg_edge(pg_db) as c:
        assert (await post(c, wa_body(wa_msg(mid="wamid.1")))).status_code == 200
        audio = wa_msg("audio", mid="wamid.2", audio={"id": "2002", "mime_type": "audio/ogg"})
        assert (await post(c, wa_body(audio))).status_code == 200
    alerts = await su_fetch(pg_db, "SELECT kind, origin FROM alert_events")
    assert [(a["kind"], a["origin"]) for a in alerts] == [("unscanned", "unscanned_edge")]


async def test_text_emergency_alert_in_same_tx_worker_stopped(pg_db: PgDb):
    """The hit and the alert row commit with the inbox row; no worker runs at all."""
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        assert (await post(c, wa_body(wa_msg(text={"body": EMERGENCY})))).status_code == 200
    (alert,) = await su_fetch(pg_db, "SELECT * FROM alert_events")
    (inbox,) = await su_fetch(pg_db, "SELECT id, state FROM inbox")
    assert alert["inbox_id"] == inbox["id"] and alert["kind"] == "emergency" and alert["origin"] == "radar_edge"
    assert alert["state"] == "open" and alert["rule_ids"] and alert["next_due_at"] == alert["t0"]
    assert inbox["state"] == "received"
    assert await su_fetch(pg_db, "SELECT 1 FROM requests") == []  # no request for an emergency (L-1)


async def test_scam_text_opens_a_scam_alert(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        text = "aapka account band ho jayega, abhi OTP bhejiye"
        assert scan(text).scam, "the fixture phrase must be one the radar flags"
        assert (await post(c, wa_body(wa_msg(text={"body": text})))).status_code == 200
    assert [a["kind"] for a in await su_fetch(pg_db, "SELECT kind FROM alert_events")] == ["scam"]


async def test_failed_insert_returns_5xx(pg_db: PgDb):
    await seed_edge_household(pg_db, with_key=False)  # no content key: the insert cannot seal
    async with pg_edge(pg_db) as c:
        r = await post(c, wa_body(wa_msg()))
    assert r.status_code >= 500
    assert await su_fetch(pg_db, "SELECT 1 FROM inbox") == []  # rolled back whole


async def test_duplicate_id_inserts_nothing_returns_200(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        body = wa_body(wa_msg(text={"body": EMERGENCY}))
        assert (await post(c, body)).status_code == 200
        assert (await post(c, body)).status_code == 200  # the platform retried
    assert len(await su_fetch(pg_db, "SELECT 1 FROM inbox")) == 1
    assert len(await su_fetch(pg_db, "SELECT 1 FROM raw_text")) == 1
    assert len(await su_fetch(pg_db, "SELECT 1 FROM alert_events")) == 1


async def test_telegram_elder_text_uses_the_same_path(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        assert (await c.post(tg_url(), content=tg_body(EMERGENCY), headers=tg_headers("wrong"))).status_code == 401
        assert (await c.post(tg_url(), content=tg_body(EMERGENCY), headers=tg_headers())).status_code == 200
    (row,) = await su_fetch(pg_db, "SELECT source, provider_message_id FROM inbox")
    assert row["source"] == "telegram_elder" and row["provider_message_id"] == "te:4242:1"
    assert len(await su_fetch(pg_db, "SELECT 1 FROM alert_events")) == 1


async def test_rate_state_returns_a_time_or_none(pg_db: PgDb):
    await seed_edge_household(pg_db, rate=(2, 600))
    async with pg_edge(pg_db) as c:
        for n in (1, 2):
            await post(c, wa_body(wa_msg(mid=f"wamid.{n}")))
    clock = FakeClock(NOW)
    async with open_store(pg_db, Role.EDGE, clock=clock) as edge:
        async with edge.tx(household="h1") as tx:  # type: ignore[arg-type]
            got = await rate_state(tx, ElderId("eh1"), NOW + timedelta(seconds=1))
            assert got is not None and got > NOW  # the oldest message in the window, plus the window
            assert await rate_state(tx, ElderId("eh1"), NOW + timedelta(hours=1)) is None  # the window has passed
            assert await rate_state(tx, ElderId("other"), NOW + timedelta(seconds=1)) is None


# --- structure ---------------------------------------------------------------------------------------------


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def test_no_seen_set_in_modules():
    banned_names = ("seen", "dedupe", "dedup", "_done", "processed_ids")
    for path in EDGE_FILES:
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "collections":
                assert "OrderedDict" not in {a.name for a in node.names}, path.name
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
            assert not any(b in name.lower() for b in banned_names), f"{path.name}: {name}"
        for stmt in tree.body:  # module level: no mutable container is created
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
                v = stmt.value
                assert not isinstance(v, ast.Set), path.name
                assert not (isinstance(v, ast.Call) and getattr(v.func, "id", "") in {"set", "OrderedDict", "deque"})


def test_edge_never_reads_or_calls_model():
    forbidden = ("saathi.llm", "saathi.understand", "saathi.perception", "saathi.policy", "saathi.pipeline", "httpx")
    for path in EDGE_FILES:
        for node in ast.walk(_tree(path)):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not m.startswith(forbidden), f"{path.name} imports {m}"
    handler = (SRC / "edge" / "handler.py").read_text()
    assert "SELECT" not in handler.replace("SELECT control.rate_state", "")  # the edge reads no table
    wiring = (SRC / "edge" / "wiring.py").read_text()
    assert "kek_file" not in wiring and "KeyRing(kek=None)" in wiring  # the edge holds no key-encryption key


def test_edge_uses_the_keyword_scan_not_the_awaiting_combine():
    handler = (SRC / "edge" / "handler.py").read_text()
    assert "combine" not in handler  # the combine waits on a classifier; the edge never waits


def test_radar_result_type_is_what_the_edge_expects():
    assert isinstance(scan("x"), RadarResult)


# --- health route ------------------------------------------------------------------------------------------


async def test_health_route_body_is_ok_only(pg_db: PgDb):
    clock = FakeClock(NOW)

    async def fine() -> ProbeResult:
        return ProbeResult("fine", ProbeStatus.OK)

    await seed_edge_household(pg_db)
    async with open_store(pg_db, Role.CORE, clock=clock) as core, pg_edge(pg_db, clock=clock) as c:
        assert (await c.get("/health")).status_code == 503  # no core beat yet
        assert await beat_once(core, clock, [fine]) == "ok"
        r = await c.get("/health")
        assert r.status_code == 200 and r.text == "ok"
        assert r.headers["content-type"].startswith("text/plain")
        assert set(r.headers) <= {"content-type", "content-length"}  # no version, name or count in a header


async def test_health_route_503_failed_write(pg_db: PgDb):
    clock = FakeClock(NOW)

    async def fine() -> ProbeResult:
        return ProbeResult("fine", ProbeStatus.OK)

    async with open_store(pg_db, Role.CORE, clock=clock) as core:
        await beat_once(core, clock, [fine])
        async with pg_edge(pg_db, clock=clock) as c:
            assert (await c.get("/health")).status_code == 200
            await clock.advance(31)
            r = await c.get("/health")
            assert r.status_code == 503 and r.content == b""  # the core went quiet
            await c.app.state.edge.store.close()  # type: ignore[attr-defined]  # the edge can no longer write
            r = await c.get("/health")
            assert r.status_code == 503 and r.content == b""


async def test_health_route_503_when_the_store_fails_fake():
    store = EdgeFakeStore()
    store.fail("tx", RuntimeError("down"), times=2)
    async with client(fake_app(store)) as c:
        r = await c.get("/health")
    assert r.status_code == 503 and r.content == b""


# ---------------------------------------------------------------- deployment files (review fixes F1, F2, F4)

INFRA = Path(__file__).resolve().parent.parent / "infra"


def _compose_service_block(name: str) -> str:
    lines = (INFRA / "docker-compose.yml").read_text().splitlines()
    start = lines.index(f"  {name}:")
    block = []
    for line in lines[start + 1 :]:
        if line and not line.startswith("   "):  # next service, comment or top-level key
            break
        block.append(line)
    return "\n".join(block)


def test_edge_service_lists_no_approval_secret_and_no_kek():
    block = _compose_service_block("edge")
    assert "sender_hmac_key" in block
    assert "approval_secret" not in block.lower()
    assert "kek" not in block.lower()


def test_build_edge_does_not_check_the_approval_secret():
    text = (SRC / "edge" / "wiring.py").read_text()
    assert "require_secrets" not in text


def test_caddyfile_bounds_slow_and_large_uploads():
    text = (INFRA / "Caddyfile").read_text()
    assert "read_header 5s" in text
    assert "read_body 10s" in text
    public = text[text.index("handle @public") : text.index("handle {")]
    assert "request_body" in public and "max_size 1MB" in public


def test_caddy_health_path_comes_from_the_setting():
    assert "{$SAATHI_HEALTH_PATH:/health}" in (INFRA / "Caddyfile").read_text()
    assert "SAATHI_HEALTH_PATH" in _compose_service_block("caddy")


def test_uvicorn_config_limits_concurrency():
    from fastapi import FastAPI

    from saathi.edge.wiring import EDGE_LIMIT_CONCURRENCY, uvicorn_config

    cfg = uvicorn_config(FastAPI(), host="127.0.0.1", port=8000)
    assert cfg.limit_concurrency == EDGE_LIMIT_CONCURRENCY > 0


def test_edge_secrets_come_from_files(tmp_path):
    from saathi.config import Settings
    from saathi.domain import Source
    from saathi.edge.wiring import build_adapters

    files = {}
    for name in ("app", "tg"):
        f = tmp_path / name
        f.write_text("synthetic-secret\n")
        files[name] = str(f)
    s = Settings(_env_file=None, wa_app_secret_file=files["app"], tg_elder_secret_file=files["tg"])  # type: ignore[call-arg]
    assert set(build_adapters(s)) == {Source.WHATSAPP, Source.TELEGRAM_ELDER}
    assert set(build_adapters(Settings(_env_file=None))) == set()  # type: ignore[call-arg]


async def test_verification_token_comes_from_its_file(tmp_path):
    from edge_helpers import adapters, settings

    from saathi.edge import create_edge_app
    from saathi.store.keys import KeyRing

    tok = tmp_path / "verify"
    tok.write_text("synthetic-file-verify\n")
    base = settings()
    s = base.model_copy(update={"wa_verify_token_file": str(tok), "wa_verify_token": type(base.wa_verify_token)("")})
    store = EdgeFakeStore()
    app = create_edge_app(s, store, KeyRing(kek=None), adapters(), scan, store.clock, sender_key=b"k" * 32)  # type: ignore[arg-type]
    async with client(app) as c:
        q = {"hub.mode": "subscribe", "hub.challenge": "5"}
        ok = await c.get(wa_url(), params={**q, "hub.verify_token": "synthetic-file-verify"})
        assert (ok.status_code, ok.text) == (200, "5")
        assert (await c.get(wa_url(), params={**q, "hub.verify_token": "synthetic-verify"})).status_code == 403
