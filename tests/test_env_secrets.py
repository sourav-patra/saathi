"""LT-31: where each secret lives (M02, compose; PL-13, PL-14, RQ-106, RQ-102). Reads the files; no service runs.

The compose file is the deployment's statement of which service holds which secret, so the tests read it as data.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from saathi.config import ConfigError, Settings, file_is_private, require_secrets, secret_text

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "infra/docker-compose.yml"
MODEL_DRIVEN = {"worker"}  # a process that sends data to a model, acts on its output or reads untrusted content
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$")


def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text())


def services() -> dict[str, dict[str, Any]]:
    return compose()["services"]


def env_names(svc: dict[str, Any]) -> list[str]:
    env = svc.get("environment") or {}
    return list(env) if isinstance(env, dict) else [e.split("=")[0] for e in env]


def listed_secrets(svc: dict[str, Any]) -> list[str]:
    return [s if isinstance(s, str) else s["source"] for s in svc.get("secrets", [])]


def test_no_secret_in_model_env():
    """PL-13: a model-driven process holds no *_KEY, *_TOKEN, *_SECRET or *_PASSWORD variable."""
    svcs = services()
    for name in MODEL_DRIVEN:
        svc = svcs[name]
        assert "env_file" not in svc, f"{name} loads a whole environment file"
        bad = [n for n in env_names(svc) if SECRET_NAME.search(n)]
        assert bad == [], f"{name} holds secret variables: {bad}"
    # the credential headers of the hosted tiers live with the proxy alone
    assert [n for n, s in svcs.items() if "env_file" in s] == ["egress"]


def test_model_driven_services_have_no_route_out_but_the_proxy():
    """RQ-102: internal network only, never the default bridge or the host network."""
    cfg = compose()
    assert cfg["networks"]["internal"] == {"internal": True}
    for name in MODEL_DRIVEN:
        svc = cfg["services"][name]
        assert svc.get("networks") == ["internal"], f"{name} must list the internal network alone"
        assert "network_mode" not in svc and not svc.get("ports")
        assert svc["environment"]["SAATHI_EGRESS_URL"].startswith("http://egress:")
    assert "internal" in cfg["services"]["egress"]["networks"] and "public" in cfg["services"]["egress"]["networks"]
    for name, svc in cfg["services"].items():  # no service falls back to the default network or the host network
        assert svc.get("networks"), f"{name} names no network"
        assert svc.get("network_mode") != "host"


def test_caregiver_bot_token_absent_from_edge_env():
    edge = services()["edge"]
    names = env_names(edge)
    assert not [n for n in names if "CAREGIVER" in n]
    assert "tg_caregiver_token" not in listed_secrets(edge)
    assert "env_file" not in edge
    # and the elder bot's token and the platform access token are the worker's, not the edge's
    assert not {"tg_elder_token", "wa_access_token"} & set(listed_secrets(edge))
    assert not [n for n in names if n.endswith(("_ACCESS_TOKEN", "_ELDER_TOKEN"))]


def test_edge_env_holds_no_key_or_kek():
    edge = services()["edge"]
    assert sorted(listed_secrets(edge)) == ["sender_hmac_key", "tg_elder_secret", "wa_app_secret", "wa_verify_token"]
    assert not [n for n in env_names(edge) if n.endswith(("_SECRET", "_TOKEN"))]  # PL-14: files, not variables
    for name, path in {
        "SAATHI_WA_APP_SECRET_FILE": "wa_app_secret",
        "SAATHI_WA_VERIFY_TOKEN_FILE": "wa_verify_token",
        "SAATHI_TG_ELDER_SECRET_FILE": "tg_elder_secret",
    }.items():
        assert edge["environment"][name] == f"/run/secrets/{path}"
    blob = " ".join(env_names(edge) + [str(v) for v in (edge.get("environment") or {}).values()]).lower()
    assert "kek" not in blob and "approval" not in blob and "/run/secrets/kek" not in blob


def test_only_the_worker_lists_the_kek_and_the_approval_secret():
    holders = {
        secret: sorted(n for n, s in services().items() if secret in listed_secrets(s))
        for secret in ("kek", "approval_secret")
    }
    assert holders == {"kek": ["worker"], "approval_secret": ["worker"]}
    worker = services()["worker"]
    assert "sender_hmac_key" not in listed_secrets(worker)  # the worker never hashes a sender
    assert worker["environment"]["SAATHI_KEK_FILE"] == "/run/secrets/kek"
    assert worker["environment"]["SAATHI_APPROVAL_SECRET_FILE"] == "/run/secrets/approval_secret"


def test_secrets_are_separate_files():
    secrets = compose()["secrets"]
    files = [v["file"] for v in secrets.values()]
    assert len(set(files)) == len(files), "two secrets share a file"
    for name, v in secrets.items():
        assert v["file"].endswith(f"/{name}"), f"{name}: the file is named for the secret"
        assert v["file"].startswith("${SAATHI_SECRETS_DIR:-/etc/saathi/secrets}/")
    for must in ("kek", "approval_secret", "sender_hmac_key"):
        assert must in secrets
    # every secret listed by a service is declared, and every declared secret has a user
    used = {s for svc in services().values() for s in listed_secrets(svc)}
    assert used == set(secrets)
    # the settings keep the three apart too
    assert len({Settings.model_fields[f].default for f in ("kek_file", "sender_hmac_key_file")}) == 1  # both unset
    s = Settings(_env_file=None, kek_file="/a", sender_hmac_key_file="/b", approval_secret_file="/c")  # type: ignore[arg-type]
    assert len({s.kek_file, s.sender_hmac_key_file, s.approval_secret_file}) == 3


def test_env_file_mode_600(tmp_path: Path):
    f = tmp_path / "secret"
    f.write_text("synthetic")
    f.chmod(0o600)
    assert file_is_private(f)
    f.chmod(0o400)
    assert file_is_private(f)
    for mode in (0o640, 0o604, 0o644, 0o660):
        f.chmod(mode)
        assert not file_is_private(f), oct(mode)
    assert not file_is_private(tmp_path / "missing")
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored
    tracked = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert tracked.stdout.strip() == ""


def test_secret_files_win_over_values_and_hold_no_default(tmp_path: Path):
    f = tmp_path / "tok"
    f.write_text("from-file\n")
    s = Settings(_env_file=None, wa_access_token="from-env", wa_access_token_file=str(f))  # type: ignore[arg-type]
    assert secret_text(s.wa_access_token, s.wa_access_token_file) == "from-file"
    empty = Settings(_env_file=None)
    assert secret_text(empty.tg_caregiver_token, empty.tg_caregiver_token_file) == ""  # no default for a secret
    with pytest.raises(ConfigError):
        secret_text(empty.tg_elder_token, str(tmp_path / "missing"))


def test_empty_approval_secret_refused(tmp_path: Path):
    with pytest.raises(ConfigError):
        require_secrets(Settings(_env_file=None))  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        require_secrets(Settings(_env_file=None, approval_secret="x" * 31))  # type: ignore[arg-type]
    short = tmp_path / "approval_secret"
    short.write_text("y" * 31 + "\n")
    with pytest.raises(ConfigError):
        require_secrets(Settings(_env_file=None, approval_secret_file=str(short)))  # type: ignore[arg-type]
    short.write_text("y" * 32 + "\n")
    require_secrets(Settings(_env_file=None, approval_secret_file=str(short)))  # type: ignore[arg-type]


async def test_the_worker_refuses_to_start_without_the_approval_secret():
    from saathi.worker.loop import serve

    built: list[int] = []

    def build(_s: Settings):  # type: ignore[no-untyped-def]
        built.append(1)

    with pytest.raises(ConfigError):
        await serve(Settings(_env_file=None), build)
    assert built == []


async def test_worker_composition_needs_its_key_file_and_a_channel(tmp_path: Path):
    from saathi.channels.worker_main import build_worker

    with pytest.raises(ConfigError):
        await build_worker(Settings(_env_file=None))  # type: ignore[arg-type]
    kek = tmp_path / "kek"
    kek.write_bytes(b"k" * 31)
    with pytest.raises(ConfigError):
        await build_worker(Settings(_env_file=None, kek_file=str(kek)))  # type: ignore[arg-type]


# --- the file set --------------------------------------------------------------------------------------------


def required_variables() -> set[str]:
    return set(re.findall(r"\$\{(\w+):\?", COMPOSE.read_text()))


def placeholder_env(tmp_path: Path) -> Path:
    """A throwaway `.env` with a fixed synthetic word for every required variable; never committed."""
    path = tmp_path / "placeholder.env"
    path.write_text("".join(f"{name}=placeholder-{name.lower()}\n" for name in sorted(required_variables())))
    path.chmod(0o600)
    return path


def _docker(*args: str, env_file: Path | None) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("CI") == "true":
            pytest.fail("docker is a prerequisite of the compose check under CI=true")
        pytest.skip("docker is not installed")
    cmd = [docker, "compose", "--env-file", str(env_file or os.devnull), "-f", str(COMPOSE), *args, "config", "--quiet"]
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SAATHI_")}
    return subprocess.run(cmd, capture_output=True, text=True, env=clean, timeout=60, check=False)


def test_compose_validates_with_a_placeholder_env(tmp_path: Path):
    env = placeholder_env(tmp_path)
    assert {"SAATHI_DOMAIN", "POSTGRES_PASSWORD", "SAATHI_DB_PASSWORD_CORE"} <= required_variables()
    ok = _docker(env_file=env)
    assert ok.returncode == 0, ok.stderr[-400:]
    assert _docker("--profile", "langfuse", env_file=env).returncode == 0
    bare = _docker(env_file=None)  # with nothing set, a required variable stops the file
    assert bare.returncode != 0 and "required variable" in bare.stderr


def test_the_trace_stack_is_an_opt_in_profile():
    svcs = services()
    langfuse = {"langfuse-web", "langfuse-worker", "clickhouse", "valkey", "seaweedfs", "seaweedfs-init"}
    for name in langfuse:
        assert svcs[name]["profiles"] == ["langfuse"], name
    for name, svc in svcs.items():
        if name in langfuse or name == "ollama-pull":  # the model pull is the other opt-in, profile "pull"
            continue
        assert not svc.get("profiles"), f"{name} is off by default"
        assert not langfuse & set(svc.get("depends_on") or {}), f"{name} needs the trace stack"
    # nothing in the profile is a secret value in the repository
    text = COMPOSE.read_text()
    literal = r"(SALT|ENCRYPTION_KEY|NEXTAUTH_SECRET|PASSWORD|SECRET_KEY): [A-Za-z0-9+/=]{16,}\s*$"
    assert not re.search(literal, text, re.M)


def test_edge_port_is_one_setting():
    edge = services()["edge"]
    assert edge["environment"]["SAATHI_EDGE_PORT"] == "${SAATHI_EDGE_PORT:-8000}"
    assert edge["ports"] == ["127.0.0.1:${SAATHI_EDGE_PORT:-8000}:${SAATHI_EDGE_PORT:-8000}"]
    assert "edge:{$SAATHI_EDGE_PORT:8000}" in (ROOT / "infra/Caddyfile").read_text()
    assert Settings(_env_file=None).edge_port == 8000  # type: ignore[arg-type]
    assert Settings(_env_file=None, edge_port=8123).edge_port == 8123  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Settings(_env_file=None, edge_port=0)  # type: ignore[arg-type]


def test_env_example_lists_every_setting():
    text = (ROOT / ".env.example").read_text()
    listed = set(re.findall(r"^#?\s*(SAATHI_[A-Z0-9_]+)=", text, re.M))
    # names the application reads from the environment: the setting names with the prefix
    wanted = {f"SAATHI_{name.upper()}" for name in Settings.model_fields}
    assert wanted - listed == set(), f"missing from .env.example: {sorted(wanted - listed)}"


def test_worker_image_has_opus_tools_and_no_ffmpeg():
    text = (ROOT / "infra/Dockerfile.worker").read_text()
    assert "opus-tools" in text
    assert "CMD" in text and "saathi-worker" in text
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/license_audit.py"), "--dockerfiles"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert run.returncode == 0, run.stdout[-400:]
    assert services()["worker"]["build"]["dockerfile"] == "infra/Dockerfile.worker"


def test_every_application_service_pins_the_environment_name_to_prod():
    """R16-1: the fault hooks arm only outside `prod`, and an .env file must never be able to change that."""
    for name in ("edge", "worker"):
        assert services()[name]["environment"]["SAATHI_ENV"] == "prod", name
