"""LT-29: the outbound host table and the egress proxy that enforces it (M02; RQ-43, RQ-102; PL-04).

The table in `saathi.hosts` is checked against the data policy, section 2. The egress Caddyfile is checked against the
table in both directions. A configured base URL whose host has no row is refused at start-up, and a process that is
traced while it calls each client connects only to the addresses it was given.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from saathi.channels.telegram_elder import TelegramClient
from saathi.channels.whatsapp import WhatsAppClient
from saathi.config import ConfigError, Settings
from saathi.hosts import (
    ALLOWED_HOSTS,
    EGRESS_HOST,
    EGRESS_ROUTES,
    EGRESS_TIER_ROUTES,
    ROW_SELF_HOSTED,
    configured_hosts,
    host_of,
    require_listed_hosts,
    unlisted_hosts,
)

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "docs/design/POLICY-saathi-data-2026-11-09.md"
EGRESS_CADDYFILE = ROOT / "infra/egress/Caddyfile"
HERE = Path(__file__).resolve().parent


def policy_destinations() -> set[str]:
    """The first column of the table in section 2 of the data policy."""
    text = POLICY.read_text()
    section = text[text.index("## 2. Where data leaves the server") : text.index("## 3. Law")]
    rows = [ln for ln in section.splitlines() if ln.startswith("| ") and not ln.startswith(("| Destination", "|---"))]
    return {ln.split("|")[1].strip() for ln in rows}


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_base_urls_are_policy_rows():
    rows = policy_destinations()
    assert "WhatsApp Cloud API" in rows  # the parser found the table
    section = POLICY.read_text()
    for host, row in ALLOWED_HOSTS.items():
        assert row in rows or (row == ROW_SELF_HOSTED and ROW_SELF_HOSTED in section), f"{host}: no row {row!r}"
    # the defaults of every base URL setting are rows, with and without the proxy
    assert unlisted_hosts(configured_hosts(settings())) == []
    through_proxy = settings(egress_url=f"http://{EGRESS_HOST}:8081", tts_url="http://tts:1")
    assert unlisted_hosts(configured_hosts(through_proxy)) == []
    assert EGRESS_HOST in configured_hosts(settings(egress_url=f"http://{EGRESS_HOST}:8081"))
    assert "graph.facebook.com" not in configured_hosts(settings(egress_url=f"http://{EGRESS_HOST}:8081"))


def test_a_new_policy_row_name_is_never_invented():
    assert set(ALLOWED_HOSTS.values()) - {ROW_SELF_HOSTED} <= policy_destinations()


def test_unlisted_host_fails():
    fields = ("t2_base_url", "t2_free_base_url", "t3_base_url", "t4_base_url", "whispercpp_url", "tts_url")
    fields += ("egress_url",)
    for field in fields:
        with pytest.raises(ConfigError) as err:
            require_listed_hosts(settings(**{field: "https://unlisted.example.org/v1"}))
        assert "unlisted.example.org" in str(err.value)
    with pytest.raises(ConfigError):
        host_of("not a url")


def test_unlisted_roster_host_fails(tmp_path: Path):
    roster = (ROOT / "infra/roster.example.toml").read_text()  # its hosted entry names a host with no row
    path = tmp_path / "roster.toml"
    path.write_text(roster)
    with pytest.raises(ConfigError) as err:
        require_listed_hosts(settings(roster_path=str(path)))
    assert "example.invalid" in str(err.value)
    path.write_text(roster.replace("https://example.invalid", f"http://{EGRESS_HOST}:8081/t2"))
    require_listed_hosts(settings(roster_path=str(path)))  # through the proxy, the same roster passes


async def test_start_up_refuses_an_unlisted_host_before_building():
    from saathi.worker.loop import serve

    built: list[int] = []

    def build(_s: Settings):  # type: ignore[no-untyped-def]
        built.append(1)

    with pytest.raises(ConfigError):
        await serve(settings(approval_secret="x" * 32, t2_base_url="https://unlisted.example.org/v1"), build)
    assert built == []


async def test_start_up_with_the_caregiver_stand_in_is_refused_in_prod():
    deps, _alerts = _stand_in_deps()
    with pytest.raises(ConfigError, match="WU-18"):
        await _serve_once(settings(approval_secret="x" * 32, env="prod"), deps)


async def test_start_up_with_the_caregiver_stand_in_tells_the_owner_once_outside_prod():
    deps, alerts = _stand_in_deps()
    await _serve_once(settings(approval_secret="x" * 32, env="dev"), deps)
    assert alerts == ["caregiver_pending"]


def _stand_in_deps():  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from saathi.worker.wiring import PendingCaregiverChannel

    alerts: list[str] = []

    async def owner_alert(what: str) -> None:
        alerts.append(what)

    async def close() -> None:
        return None

    store = SimpleNamespace(close=close)
    caregiver = PendingCaregiverChannel()
    return SimpleNamespace(store=store, clock=None, caregiver=caregiver, owner_alert=owner_alert), alerts


async def _serve_once(s: Settings, deps) -> None:  # type: ignore[no-untyped-def]
    import saathi.worker.loop as loop

    class Idle:
        def __init__(self, *_a: object) -> None: ...

        async def run_forever(self) -> None:
            return None

    async def no_beat(*_a: object, **_k: object) -> None:
        return None

    mp = pytest.MonkeyPatch()
    mp.setattr(loop, "Worker", Idle)
    mp.setattr(loop, "beat_loop", no_beat)
    try:
        await loop.serve(s, lambda _s: deps)
    finally:
        mp.undo()


# --- the egress proxy enforces the same table -------------------------------------------------------------


def caddy_routes() -> dict[str, str]:
    """Route name to the upstream written after `reverse_proxy` in its block."""
    text = EGRESS_CADDYFILE.read_text()
    routes = {}
    for m in re.finditer(r"handle_path /([\w-]+)/\*\s*\{\s*reverse_proxy (\S+)", text):
        routes[m.group(1)] = m.group(2)
    return routes


def test_egress_routes_are_the_table():
    routes = caddy_routes()
    literal = {name: up for name, up in routes.items() if not up.startswith("{$")}
    assert {name: host_of(up) for name, up in literal.items()} == dict(EGRESS_ROUTES)
    for host in EGRESS_ROUTES.values():
        assert host in ALLOWED_HOSTS
    assert all(up.startswith("https://") for up in literal.values())  # the proxy talks TLS to the platforms
    tiers = {name for name, up in routes.items() if up.startswith("{$")}
    assert tiers == set(EGRESS_TIER_ROUTES)
    for name in EGRESS_TIER_ROUTES:  # an unset upstream names a host that never resolves, never an open default
        default = rf"\{{\$SAATHI_EGRESS_{name.upper()}_UPSTREAM:https://[\w.-]+\.invalid\}}"
        assert re.search(default, EGRESS_CADDYFILE.read_text())


def test_egress_proxy_has_no_other_exit_and_no_interception():
    text = EGRESS_CADDYFILE.read_text()
    assert len(re.findall(r"reverse_proxy", text)) == len(EGRESS_ROUTES) + len(EGRESS_TIER_ROUTES)
    assert re.search(r"handle \{\s*respond 404\s*\}", text)  # a path with no route gets 404
    assert "auto_https off" in text and "admin off" in text
    for forbidden in ("tls internal", "pki", "forward_proxy", "tls_insecure_skip_verify"):
        assert forbidden not in text
    hosts_in_file = set(re.findall(r"https://([\w.-]+)", text))
    assert {h for h in hosts_in_file if not h.endswith(".invalid")} == set(EGRESS_ROUTES.values())


def test_client_base_urls_go_through_the_proxy_routes():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"content-length": "2"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    wa = WhatsAppClient(
        phone_number_id="1", access_token="synthetic", graph_version="v1.0", client=http,
        api_base="http://egress:8081/whatsapp", media_base="http://egress:8081/whatsapp-media",
    )  # fmt: skip
    tg = TelegramClient(token="000000:synthetic", client=http, api="http://egress:8081/telegram")

    import asyncio

    async def run() -> None:
        await wa.get("1", fields="id")
        await wa.download("https://lookaside.fbsbx.com/path/file?x=1")
        await tg.call("getMe")

    asyncio.run(run())
    urls = [str(r.url) for r in seen]
    assert urls[0].startswith("http://egress:8081/whatsapp/v1.0/1")
    assert urls[1] == "http://egress:8081/whatsapp-media/path/file?x=1"
    assert urls[2] == "http://egress:8081/telegram/bot000000:synthetic/getMe"
    assert {r.url.host for r in seen} == {"egress"}


# --- the composed network: what the worker can reach ---------------------------------------------------------


def _compose_services() -> dict[str, dict]:
    import yaml

    return yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())["services"]


def test_the_worker_reaches_no_public_homed_service_but_egress_and_the_edge():
    """RQ-102: a service on both networks can dial out. The worker may talk to the proxy (the one exit) and to the
    edge (our own app, which fetches nothing for a caller); any other dual-homed service would be a way round it."""
    svcs = _compose_services()
    worker_nets = set(svcs["worker"]["networks"])
    reachable = {n for n, s in svcs.items() if n != "worker" and worker_nets & set(s["networks"])}
    public_homed = {n for n in reachable if "public" in svcs[n]["networks"]}
    allowed = {"egress", "edge"}
    # the trace UI is opt-in (profile langfuse) and the worker never calls it in Phase 0
    public_homed -= {n for n in public_homed if svcs[n].get("profiles") == ["langfuse"]}
    assert public_homed == allowed, sorted(public_homed ^ allowed)


def test_ollama_is_internal_only_and_its_pull_runs_apart_from_the_worker_network():
    svcs = _compose_services()
    assert svcs["ollama"]["networks"] == ["internal"]
    pull = svcs["ollama-pull"]
    assert pull["networks"] == ["public"] and pull["profiles"] == ["pull"]
    assert "OLLAMA_HOST" not in (pull.get("environment") or {})
    assert "ollama_data:/root/.ollama" in pull["volumes"]  # it fills the volume the server reads
    assert not pull.get("depends_on")


def test_postgres_is_internal_only_and_publishes_no_port():
    pg = _compose_services()["postgres"]
    assert pg["networks"] == ["internal"]
    assert not pg.get("ports")


# --- strace: the process connects only to the addresses it was given ---------------------------------------


class _Echo(BaseHTTPRequestHandler):
    def _reply(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({"text": "synthetic"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _reply  # noqa: N815

    def log_message(self, *_a: object) -> None:
        return None


def _serve() -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _traced_connects(tmp_path: Path, ports: list[int]) -> set[tuple[str, int]]:
    strace = shutil.which("strace")
    if strace is None:
        if os.environ.get("CI") == "true":
            pytest.fail("strace is a prerequisite of this test under CI=true")
        pytest.skip("strace is not installed")
    out = tmp_path / "connect.txt"
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env["PYTHONPATH"] = f"{ROOT / 'src'}"
    run = subprocess.run(
        [strace, "-f", "-e", "trace=connect", "-o", str(out), sys.executable, "-I", str(HERE / "net_probe.py"),
         *map(str, ports)],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    if not out.exists() or out.stat().st_size == 0:
        if os.environ.get("CI") == "true":
            pytest.fail(f"strace produced no trace: {run.stderr[-200:]}")
        pytest.skip("strace cannot trace here")
    assert run.returncode == 0, run.stderr[-400:]
    found = set()
    for line in out.read_text().splitlines():
        m = re.search(r'sa_family=AF_INET6?, sin6?_port=htons\((\d+)\).*?inet_(?:addr|pton)\([^"]*"([^"]+)"', line)
        if m:
            found.add((m.group(2), int(m.group(1))))
    return found


def test_strace_connect_only_listed_hosts(tmp_path: Path):
    srv = _serve()
    try:
        port = srv.server_address[1]
        allowed = {("127.0.0.1", port)}  # the host of every base URL given to the probe, and its port
        assert unlisted_hosts({host_of(f"http://127.0.0.1:{port}")}) == []
        connects = _traced_connects(tmp_path, [port])
        assert connects, "the trace saw no connection: the probe did not run"
        assert connects <= allowed, f"connected outside the table: {sorted(connects - allowed)}"
    finally:
        srv.shutdown()


def test_the_connect_check_notices_a_connection_outside_the_list(tmp_path: Path):
    srv, other = _serve(), _serve()
    try:
        port, stray = srv.server_address[1], other.server_address[1]
        connects = _traced_connects(tmp_path, [port, stray])
        assert connects - {("127.0.0.1", port)} == {("127.0.0.1", stray)}
    finally:
        srv.shutdown()
        other.shutdown()


def test_schema_is_migrated_before_edge_and_worker_start():
    """DI-7: a one-shot `migrate` service applies infra/sql/schema as saathi_migrator; edge and worker wait for it."""
    svcs = _compose_services()
    mig = svcs["migrate"]
    assert mig["command"] == ["saathi-migrate"]
    assert mig["restart"] == "no"
    assert mig["networks"] == ["internal"]
    assert not mig.get("ports") and not mig.get("env_file") and not mig.get("secrets")
    env = mig["environment"]
    assert env["SAATHI_MIGRATE_DSN"].startswith("postgresql://saathi_migrator:")
    assert env["SAATHI_SCHEMA_DIR"] == "/app/schema"
    assert "./sql/schema:/app/schema:ro" in mig["volumes"]
    assert mig["depends_on"]["postgres"] == {"condition": "service_healthy"}
    for name in ("edge", "worker"):
        assert svcs[name]["depends_on"]["migrate"] == {"condition": "service_completed_successfully"}, name
    import tomllib
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["saathi-migrate"] == "saathi.store.migrate:main"
