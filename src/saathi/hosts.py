"""The outbound host table (M02; RQ-43, PL-04, RQ-102).

`ALLOWED_HOSTS` maps every host the code can call to its row in the data policy, section 2 ("Where data leaves the
server"). A new outbound host needs a row there in the same change (PL-04). The egress proxy in `infra/` enforces the
same table for the model-driven processes, and `require_listed_hosts` lets start-up refuse a base URL whose host is not
a row. Provider names stay private: a hosted model tier is reached only through the proxy, whose service name is
`egress`, so the roster and the settings carry `http://egress:<port>/<route>` and never a provider address.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from urllib.parse import urlsplit

from saathi.config import ConfigError, Settings

# The destination names below are the first column of the policy table, word for word.
ROW_WHATSAPP = "WhatsApp Cloud API"
ROW_TELEGRAM_CAREGIVER = "Telegram Bot API, caregiver bot"
ROW_LOCAL_MODELS = "Local models (G1)"
ROW_HOSTED = "Hosted open-weight tier (G2)"
ROW_SELF_HOSTED = "Self-hosted trace and storage services"

# Hosts fixed in code: the platform APIs and the media host of the WhatsApp Graph API.
WHATSAPP_HOSTS = ("graph.facebook.com", "lookaside.fbsbx.com")
TELEGRAM_HOST = "api.telegram.org"

# The egress proxy's service name: the one exit of a model-driven process (RQ-102).
EGRESS_HOST = "egress"

# The egress proxy's literal routes (infra/egress/Caddyfile): route name to the upstream host. The hosted tier routes
# (`t2`, `t2free`, `t3`, `t4`) take a private upstream from the proxy's environment file and are not listed here.
EGRESS_ROUTES: Mapping[str, str] = MappingProxyType(
    {"whatsapp": "graph.facebook.com", "whatsapp-media": "lookaside.fbsbx.com", "telegram": TELEGRAM_HOST}
)
EGRESS_TIER_ROUTES = ("t2", "t2free", "t3", "t4")

ALLOWED_HOSTS: Mapping[str, str] = MappingProxyType(
    {
        "graph.facebook.com": ROW_WHATSAPP,
        "lookaside.fbsbx.com": ROW_WHATSAPP,
        # One host serves both bots; the table has one row per bot and this entry names the caregiver row.
        TELEGRAM_HOST: ROW_TELEGRAM_CAREGIVER,
        # In-compose services on the server: nothing leaves it.
        "ollama": ROW_LOCAL_MODELS,
        "whisper": ROW_LOCAL_MODELS,
        "tts": ROW_LOCAL_MODELS,
        "localhost": ROW_LOCAL_MODELS,
        "127.0.0.1": ROW_LOCAL_MODELS,
        "langfuse-web": ROW_SELF_HOSTED,
        # Hosted tiers go through the proxy; its route set is the per-process list of policy hosts.
        EGRESS_HOST: ROW_HOSTED,
    }
)


def host_of(url: str) -> str:
    """The lower-case host of a URL. `ConfigError` when there is none (a bare word or a malformed value)."""
    try:
        host = urlsplit(url).hostname
    except ValueError as exc:
        raise ConfigError("a base URL cannot be parsed") from exc
    if not host:
        raise ConfigError("a base URL has no host")
    return host.lower()


def _roster_urls(path: str) -> list[str]:
    from saathi.llm.roster import load_roster_file

    try:
        roster = load_roster_file(path)
    except Exception as exc:  # noqa: BLE001  (a bad file is a configuration error whatever its kind)
        raise ConfigError("the roster file cannot be read") from exc
    return [e.spec.base_url for e in roster.entries]


def configured_urls(s: Settings) -> list[str]:
    """Every base URL the code can call: the settings, the roster entries, and the fixed platform APIs."""
    urls = [
        s.t1_base_url, s.t2_base_url, s.t2_free_base_url, s.t3_base_url, s.t4_base_url,
        s.whispercpp_url, s.tts_url,
    ]  # fmt: skip
    if s.langfuse_public_key:
        urls.append(s.langfuse_host)
    if s.roster_path:
        urls.extend(_roster_urls(s.roster_path))
    if s.egress_url:  # every platform call goes through the proxy routes
        urls.append(s.egress_url)
    else:
        urls += [f"https://{h}/" for h in (*WHATSAPP_HOSTS, TELEGRAM_HOST)]
    return [u for u in urls if u]


def configured_hosts(s: Settings) -> set[str]:
    """The hosts of `configured_urls`."""
    return {host_of(u) for u in configured_urls(s)}


def unlisted_hosts(hosts: set[str]) -> list[str]:
    return sorted(h for h in hosts if h not in ALLOWED_HOSTS)


def require_listed_hosts(s: Settings) -> None:
    """Raise `ConfigError` naming each configured host that has no row in the table (PL-04)."""
    bad = unlisted_hosts(configured_hosts(s))
    if bad:
        raise ConfigError(f"base URL host has no row in the outbound table: {', '.join(bad)}")
