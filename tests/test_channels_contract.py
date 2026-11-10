"""LT-10: one contract suite for `WhatsAppElderChannel`, `TelegramElderChannel` and `FakeElderChannel` (RQ-105).

HTTP is mocked with `httpx.MockTransport`; no network. Addresses are synthetic placeholders.
"""

from __future__ import annotations

import ast
import hashlib
import json
import typing
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fakes import FakeClock, FakeElderChannel, FakePersonDirectory

from saathi import ports
from saathi.channels.telegram_elder import (
    TelegramClient,
    TelegramElderChannel,
    parse_update,
    verify_secret_token,
)
from saathi.channels.whatsapp import WhatsAppClient, WhatsAppElderChannel
from saathi.domain import (
    ChannelCap,
    ChannelHealth,
    ImageBlob,
    MediaBlob,
    MediaKind,
    MediaRef,
    SendReceipt,
    Source,
    VoiceClip,
)
from saathi.errors import (
    ChannelDown,
    MediaExpired,
    ProactiveNotAllowed,
    RateLimited,
    SendRejected,
    TemplateMissing,
)
from saathi.ids import IdemKey, PersonRef, ReplyKey, TemplateId, idem_key

ROOT = Path(__file__).resolve().parent.parent
PERSON = PersonRef("person-0001")
WA_ADDRESS = "+910000000001"
TG_ADDRESS = "1000000001"
CLIP = VoiceClip(key=ReplyKey("ack"), data=b"opus-bytes", sha256=hashlib.sha256(b"opus-bytes").hexdigest())
IMAGE = ImageBlob(data=b"jpeg-bytes")
TEMPLATE = TemplateId("T-01")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

KINDS = ["whatsapp", "telegram_elder", "fake"]
SENDS = ["send_text", "send_audio", "send_image", "send_template"]
ERROR_OUTCOMES = {
    "window_closed": ProactiveNotAllowed,
    "template_missing": TemplateMissing,
    "down": ChannelDown,
    "rate_limited": RateLimited,
    "rejected": SendRejected,
    "media_expired": MediaExpired,
}


def key(n: int) -> IdemKey:
    return idem_key("inbox-contract", f"effect-{n}")


def _json(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


class Harness:
    """A channel plus a way to script what the platform answers, the same for all three kinds."""

    kind: str
    channel: ports.ElderChannel
    address: str
    requests: list[httpx.Request]

    def inject(self, outcome: str) -> None:
        """The next call that reaches the platform fails the way `outcome` says."""
        raise NotImplementedError

    def set_health(self, state: ChannelHealth) -> None:
        raise NotImplementedError

    def add_media(self, handle: str, data: bytes, mime: str) -> MediaRef:
        raise NotImplementedError

    def deliveries(self) -> int:
        """How many sends reached the platform (a refused duplicate adds none)."""
        raise NotImplementedError


class WhatsAppHarness(Harness):
    kind = "whatsapp"
    address = WA_ADDRESS

    def __init__(self) -> None:
        self.requests = []
        self.next_failure: Callable[[httpx.Request], httpx.Response | Exception] | None = None
        self.health_state = ChannelHealth.UP
        self.media: dict[str, tuple[bytes, str]] = {}
        self.clock = FakeClock(NOW)
        people = FakePersonDirectory({(str(PERSON), "whatsapp"): WA_ADDRESS})
        client = WhatsAppClient(
            phone_number_id="1000000000",
            access_token="test-token",
            client=httpx.AsyncClient(transport=httpx.MockTransport(self._handle)),
            media_hosts=frozenset({"lookaside.example"}),
        )
        self.channel = WhatsAppElderChannel(client, people, self.clock)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.next_failure is not None:
            failure, self.next_failure = self.next_failure, None
            out = failure(request)
            if isinstance(out, Exception):
                raise out
            return out
        path = request.url.path
        if request.method == "POST" and path.endswith("/messages"):
            return _json(200, {"messages": [{"id": f"wamid.{len(self.requests)}"}]})
        if request.method == "POST" and path.endswith("/media"):
            return _json(200, {"id": "media-up-1"})
        if request.url.host == "lookaside.example":
            data, _ = self.media[path.rsplit("/", 1)[-1]]
            return httpx.Response(200, content=data)
        handle = path.rsplit("/", 1)[-1]
        if handle == "1000000000":
            return {
                ChannelHealth.UP: _json(200, {"id": handle}),
                ChannelHealth.DEGRADED: _json(429, {"error": {"code": 80007}}),
                ChannelHealth.DOWN: _json(503, {}),
            }[self.health_state]
        if handle in self.media:
            return _json(200, {"url": f"https://lookaside.example/blob/{handle}", "mime_type": self.media[handle][1]})
        return _json(404, {"error": {"code": 100}})

    def inject(self, outcome: str) -> None:
        responses: dict[str, Callable[[httpx.Request], httpx.Response | Exception]] = {
            "window_closed": lambda r: _json(400, {"error": {"code": 131047, "message": "Re-engagement message"}}),
            "template_missing": lambda r: _json(404, {"error": {"code": 132001}}),
            "down": lambda r: httpx.ConnectError("refused", request=r),
            "rate_limited": lambda r: httpx.Response(
                429, headers={"retry-after": "7"}, json={"error": {"code": 130429}}
            ),
            "rejected": lambda r: _json(400, {"error": {"code": 100}}),
            "media_expired": lambda r: _json(404, {"error": {"code": 100}}),
        }
        self.next_failure = responses[outcome]

    def set_health(self, state: ChannelHealth) -> None:
        self.health_state = state

    def add_media(self, handle: str, data: bytes, mime: str) -> MediaRef:
        self.media[handle] = (data, mime)
        return MediaRef(source=Source.WHATSAPP, handle=handle, mime=mime)

    def deliveries(self) -> int:
        return sum(1 for r in self.requests if r.method == "POST" and r.url.path.endswith("/messages"))


class TelegramHarness(Harness):
    kind = "telegram_elder"
    address = TG_ADDRESS

    def __init__(self) -> None:
        self.requests = []
        self.next_failure: Callable[[httpx.Request], httpx.Response | Exception] | None = None
        self.health_state = ChannelHealth.UP
        self.media: dict[str, tuple[bytes, str]] = {}
        people = FakePersonDirectory({(str(PERSON), "telegram_elder"): TG_ADDRESS})
        client = TelegramClient(
            token="000000:test-token", client=httpx.AsyncClient(transport=httpx.MockTransport(self._handle))
        )
        self.channel = TelegramElderChannel(client, people)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.next_failure is not None:
            failure, self.next_failure = self.next_failure, None
            out = failure(request)
            if isinstance(out, Exception):
                raise out
            return out
        path = request.url.path
        if "/file/bot" in path:
            return httpx.Response(200, content=self.media[path.rsplit("/", 1)[-1]][0])
        method = path.rsplit("/", 1)[-1]
        if method in ("sendMessage", "sendVoice", "sendPhoto"):
            return _json(200, {"ok": True, "result": {"message_id": len(self.requests)}})
        if method == "getMe":
            return {
                ChannelHealth.UP: _json(200, {"ok": True, "result": {"id": 1}}),
                ChannelHealth.DEGRADED: _json(429, {"ok": False, "error_code": 429}),
                ChannelHealth.DOWN: _json(502, {"ok": False, "error_code": 502}),
            }[self.health_state]
        if method == "getFile":
            handle = json.loads(request.read())["file_id"]
            if handle in self.media:
                return _json(200, {"ok": True, "result": {"file_id": handle, "file_path": f"files/{handle}"}})
            return _json(400, {"ok": False, "error_code": 400, "description": "Bad Request: file not found"})
        return _json(404, {"ok": False, "error_code": 404})

    def inject(self, outcome: str) -> None:
        responses: dict[str, Callable[[httpx.Request], httpx.Response | Exception]] = {
            "window_closed": lambda r: _json(
                403,
                {
                    "ok": False,
                    "error_code": 403,
                    "description": "Forbidden: bot can't initiate conversation with a user",
                },
            ),
            "template_missing": lambda r: _json(404, {"ok": False, "error_code": 404}),  # never reached: no templates
            "down": lambda r: httpx.ConnectError("refused", request=r),
            "rate_limited": lambda r: _json(
                429,
                {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}},
            ),
            "rejected": lambda r: _json(
                400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
            ),
            "media_expired": lambda r: _json(
                400, {"ok": False, "error_code": 400, "description": "Bad Request: file is too big"}
            ),
        }
        self.next_failure = responses[outcome]

    def set_health(self, state: ChannelHealth) -> None:
        self.health_state = state

    def add_media(self, handle: str, data: bytes, mime: str) -> MediaRef:
        self.media[handle] = (data, mime)
        return MediaRef(source=Source.TELEGRAM_ELDER, handle=handle, mime=mime)

    def deliveries(self) -> int:
        return sum(
            1 for r in self.requests if r.url.path.rsplit("/", 1)[-1] in ("sendMessage", "sendVoice", "sendPhoto")
        )


class FakeHarness(Harness):
    kind = "fake"
    address = ""

    def __init__(self) -> None:
        self.requests = []
        self.fake = FakeElderChannel()
        self.channel = self.fake

    def inject(self, outcome: str) -> None:
        if outcome == "window_closed":
            self.fake.window_open = False
            return
        for method in (*SENDS, "fetch_media", "health"):
            self.fake.fail(method, ERROR_OUTCOMES[outcome])

    def set_health(self, state: ChannelHealth) -> None:
        self.fake.channel_health = state

    def add_media(self, handle: str, data: bytes, mime: str) -> MediaRef:
        ref = MediaRef(source=Source.WHATSAPP, handle=handle, mime=mime)
        self.fake.media[handle] = MediaBlob(data=data, mime=mime)
        return ref

    def deliveries(self) -> int:
        return len(self.fake.sent)


@pytest.fixture(params=KINDS)
def h(request: pytest.FixtureRequest) -> Harness:
    return {"whatsapp": WhatsAppHarness, "telegram_elder": TelegramHarness, "fake": FakeHarness}[request.param]()


async def send(channel: ports.ElderChannel, method: str, k: IdemKey) -> SendReceipt:
    if method == "send_text":
        return await channel.send_text(PERSON, "namaste", key=k)
    if method == "send_audio":
        return await channel.send_audio(PERSON, CLIP, key=k)
    if method == "send_image":
        return await channel.send_image(PERSON, IMAGE, "card", key=k)
    return await channel.send_template(PERSON, TEMPLATE, {"1": "x"}, key=k)


def sends_for(channel: ports.ElderChannel) -> list[str]:
    return [m for m in SENDS if m != "send_template" or ChannelCap.TEMPLATES in channel.capabilities]


# --------------------------------------------------------------------------- the contract
def test_implements_port(h: Harness) -> None:
    assert isinstance(h.channel, ports.ElderChannel)
    assert h.channel.kind in ("whatsapp", "telegram_elder")
    assert ChannelCap.TEXT in h.channel.capabilities


@pytest.mark.parametrize("kind", KINDS)
async def test_contract_each_send_method(kind: str) -> None:
    h = {"whatsapp": WhatsAppHarness, "telegram_elder": TelegramHarness, "fake": FakeHarness}[kind]()
    for n, method in enumerate(sends_for(h.channel)):
        receipt = await send(h.channel, method, key(n))
        assert isinstance(receipt, SendReceipt) and receipt.provider_id and receipt.key == key(n)
    assert h.deliveries() == len(sends_for(h.channel))


async def test_contract_fetch_media(h: Harness) -> None:
    ref = h.add_media("1001", b"\x00voice", "audio/ogg")
    blob = await h.channel.fetch_media(ref)
    assert isinstance(blob, MediaBlob) and blob.data == b"\x00voice"


async def test_contract_health(h: Harness) -> None:
    for state in (ChannelHealth.UP, ChannelHealth.DEGRADED, ChannelHealth.DOWN):
        h.set_health(state)
        assert await h.channel.health() == state


async def test_health_down_on_transport_error(h: Harness) -> None:
    if h.kind == "fake":
        pytest.skip("the fake has no transport")
    h.inject("down")
    assert await h.channel.health() == ChannelHealth.DOWN


@pytest.mark.parametrize("outcome", ["down", "rate_limited", "rejected"])
@pytest.mark.parametrize("method", SENDS)
async def test_contract_each_error_on_send(h: Harness, method: str, outcome: str) -> None:
    if method == "send_template" and ChannelCap.TEMPLATES not in h.channel.capabilities:
        pytest.skip("no templates on this channel")
    h.inject(outcome)
    with pytest.raises(ERROR_OUTCOMES[outcome]) as info:
        await send(h.channel, method, key(1))
    if outcome == "rate_limited" and h.kind != "fake":
        assert info.value.retry_after == 7  # type: ignore[attr-defined]
    # a failed send is not remembered as sent: the retry with the same key goes through
    receipt = await send(h.channel, method, key(1))
    assert receipt.key == key(1)


async def test_contract_template_missing(h: Harness) -> None:
    if ChannelCap.TEMPLATES not in h.channel.capabilities:
        with pytest.raises(TemplateMissing):
            await h.channel.send_template(PERSON, TEMPLATE, {}, key=key(1))
        assert h.deliveries() == 0
        return
    h.inject("template_missing")
    with pytest.raises(TemplateMissing):
        await h.channel.send_template(PERSON, TEMPLATE, {}, key=key(1))


async def test_contract_media_errors(h: Harness) -> None:
    ref = h.add_media("1001", b"x", "image/jpeg")
    h.inject("media_expired")
    with pytest.raises(MediaExpired):
        await h.channel.fetch_media(ref)
    h.inject("down")
    with pytest.raises(ChannelDown):
        await h.channel.fetch_media(ref)
    missing = MediaRef(source=ref.source, handle="9999", mime="image/jpeg")
    with pytest.raises(MediaExpired):
        await h.channel.fetch_media(missing)


async def test_contract_media_wrong_source_rejected(h: Harness) -> None:
    other = Source.TELEGRAM_ELDER if h.channel.kind == "whatsapp" else Source.WHATSAPP
    with pytest.raises(SendRejected) as info:
        await h.channel.fetch_media(MediaRef(source=other, handle="1001", mime=None))
    assert info.value.code == "wrong_source"


async def test_contract_repeat_key_is_refused_and_sends_nothing(h: Harness) -> None:
    await h.channel.send_text(PERSON, "one", key=key(1))
    with pytest.raises(SendRejected) as info:
        await h.channel.send_text(PERSON, "one", key=key(1))
    assert info.value.code == "duplicate_key"
    assert h.deliveries() == 1


# --------------------------------------------------------------------------- RQ-105 rows
def test_capability_sets() -> None:
    wa, tg = WhatsAppHarness().channel, TelegramHarness().channel
    common = {
        ChannelCap.VOICE_IN, ChannelCap.VOICE_OUT, ChannelCap.IMAGE_IN, ChannelCap.IMAGE_OUT,
        ChannelCap.VIDEO_IN, ChannelCap.TEXT,
    }  # fmt: skip
    assert common <= wa.capabilities and common <= tg.capabilities
    assert ChannelCap.TEMPLATES in wa.capabilities and ChannelCap.PROACTIVE_FREE not in wa.capabilities
    assert ChannelCap.TEMPLATES not in tg.capabilities and ChannelCap.PROACTIVE_FREE in tg.capabilities
    assert wa.kind == "whatsapp" and tg.kind == "telegram_elder"
    assert FakeHarness().channel.capabilities == frozenset(ChannelCap)


async def test_window_maps_to_proactive_not_allowed(h: Harness) -> None:
    h.inject("window_closed")
    with pytest.raises(ProactiveNotAllowed) as info:
        await h.channel.send_text(PERSON, "late reminder", key=key(1))
    assert info.value.reason in ("window_closed", "chat_not_started")


async def test_whatsapp_window_from_clock_blocks_free_form_but_not_template() -> None:
    h = WhatsAppHarness()
    assert isinstance(h.channel, WhatsAppElderChannel)
    h.channel.note_inbound(PERSON, NOW - timedelta(hours=25))
    for n, method in enumerate(("send_text", "send_audio", "send_image")):
        with pytest.raises(ProactiveNotAllowed) as info:
            await send(h.channel, method, key(n))
        assert info.value.reason == "window_closed"
    assert h.requests == []  # decided before any call
    await h.channel.send_template(PERSON, TEMPLATE, {"1": "x"}, key=key(9))
    h.channel.note_inbound(PERSON, NOW - timedelta(hours=1))  # the elder wrote again: window open
    await h.channel.send_text(PERSON, "hello", key=key(10))


async def test_core_holds_person_ref_only(h: Harness) -> None:
    hints = typing.get_type_hints(ports.ElderChannel.send_text)
    for name in SENDS:
        assert typing.get_type_hints(getattr(ports.ElderChannel, name))["to"] is PersonRef
    assert hints["return"] is SendReceipt
    ref = h.add_media("1001", b"x", "image/jpeg")
    returned = [
        await h.channel.send_text(PERSON, "hi", key=key(1)),
        await h.channel.fetch_media(ref),
        await h.channel.health(),
    ]
    for value in returned:
        for secret in (WA_ADDRESS, WA_ADDRESS.lstrip("+"), TG_ADDRESS):
            assert secret not in repr(value)
    if h.kind != "fake":  # the address was resolved inside the channel and reached the platform
        assert any(h.address.lstrip("+") in r.read().decode() for r in h.requests)


async def test_unknown_person_ref_is_not_guessed() -> None:
    h = TelegramHarness()
    with pytest.raises(SendRejected) as info:
        await h.channel.send_text(PersonRef("someone-else"), "hi", key=key(1))
    assert info.value.code == "unknown_person"
    assert h.requests == []


PLATFORM_NAMES = {"whatsapp", "telegram", "telegram_elder", "telegram_forward"}
SOURCE_MEMBERS = {"WHATSAPP", "TELEGRAM_ELDER", "CAREGIVER_FORWARD"}
# Every module outside `channels/` and `edge/` is checked, the composition root `app.py` included. The vocabulary
# modules (domain, ports, config, ids), pipeline.py and legacy.py have no hits.
LEGACY_PLATFORM_MODULES: set[str] = set()
ALLOWED_PREFIXES = ("src/saathi/channels/", "src/saathi/edge/")


def _core_modules() -> list[Path]:
    out = []
    for path in sorted((ROOT / "src/saathi").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(ALLOWED_PREFIXES) or rel in LEGACY_PLATFORM_MODULES:
            continue
        out.append(path)
    return out


def _is_channel_module(name: str) -> bool:
    return name == "saathi.channels" or name.startswith("saathi.channels.")


def _source_member(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "Source":
        return node.attr if node.attr in SOURCE_MEMBERS else None
    return None


def _violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] == "aiogram" or _is_channel_module(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "aiogram" or _is_channel_module(node.module):
                found.append(node.module)
        elif isinstance(node, ast.Compare):
            for n in ast.walk(node):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.lower() in PLATFORM_NAMES:
                    found.append(n.value)
                elif (member := _source_member(n)) is not None:
                    found.append(f"Source.{member}")
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for n in ast.walk(case.pattern):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.lower() in PLATFORM_NAMES:
                        found.append(n.value)
                    elif (member := _source_member(n)) is not None:
                        found.append(f"Source.{member}")
    return found


def test_no_platform_name_in_core() -> None:
    modules = _core_modules()
    assert any(p.name == "ports.py" for p in modules)
    assert any(p.name == "app.py" for p in modules)  # the composition root is checked like any core module
    bad = {}
    for path in modules:
        if hits := _violations(path.read_text()):
            bad[path.relative_to(ROOT).as_posix()] = hits
    assert not bad, f"core modules import a platform client or branch on a platform name: {bad}"


def test_exemption_list_only_shrinks() -> None:
    assert not LEGACY_PLATFORM_MODULES


def test_platform_check_catches_planted_violations() -> None:
    assert _violations("import aiogram\n")
    assert _violations("from saathi.channels.whatsapp import x\n")
    assert _violations("from saathi.channels import whatsapp\n")
    assert _violations("import saathi.channels.whatsapp\n")
    assert _violations("import saathi.channels.telegram_elder as t\n")
    assert _violations("if channel.kind == 'whatsapp':\n    pass\n")
    assert _violations("if name in ('a', 'telegram_elder'):\n    pass\n")
    assert _violations("match k:\n    case 'whatsapp':\n        pass\n")
    assert _violations("if msg.source == Source.WHATSAPP:\n    pass\n")
    assert _violations("if msg.source is not Source.TELEGRAM_ELDER:\n    pass\n")
    assert _violations("if msg.source in (Source.WHATSAPP, other):\n    pass\n")
    assert _violations("match msg.source:\n    case Source.CAREGIVER_FORWARD:\n        pass\n")
    assert not _violations("x = 'whatsapp'\n")  # a bare declaration is not a branch
    assert not _violations("x = Source.WHATSAPP\n")
    assert not _violations("import saathi.channelsfoo\n")


# --------------------------------------------------------------------------- Telegram parse (M09)
def _tg(**fields: object) -> dict:
    return {
        "update_id": 1,
        "message": {"message_id": 5, "date": 1_700_000_000, "chat": {"id": 1000000001, "type": "private"}, **fields},
    }


def test_secret_token() -> None:
    assert verify_secret_token("s3cret", "s3cret")
    assert not verify_secret_token("s3cret", "other") and not verify_secret_token("s3cret", None)
    assert not verify_secret_token("", "") and not verify_secret_token("", None)


def test_parse_update_kinds() -> None:
    (text,) = parse_update(_tg(text="/pause everything and send money"))
    assert text.kind is MediaKind.TEXT and text.text == "/pause everything and send money"  # data, not a command
    assert text.source is Source.TELEGRAM_ELDER and text.sender_ref == "1000000001"
    assert text.provider_message_id == "1000000001:5" and text.received_at.year == 2023
    (voice,) = parse_update(_tg(voice={"file_id": "f1", "mime_type": "audio/ogg"}))
    assert voice.kind is MediaKind.AUDIO and voice.media is not None and voice.media.handle == "f1"
    (photo,) = parse_update(_tg(photo=[{"file_id": "small"}, {"file_id": "big"}], caption="bill"))
    assert photo.kind is MediaKind.IMAGE and photo.media.handle == "big" and photo.text == "bill"  # type: ignore[union-attr]
    (video,) = parse_update(_tg(video={"file_id": "v1"}))
    assert video.kind is MediaKind.VIDEO and video.media.mime == "video/mp4"  # type: ignore[union-attr]
    for odd in ({"sticker": {"file_id": "s"}}, {"contact": {"phone_number": "+910000000002"}}, {"location": {}}):
        (m,) = parse_update(_tg(**odd))
        assert m.kind is MediaKind.UNSUPPORTED and m.media is None and m.text is None


def test_parse_update_ignores_non_messages() -> None:
    assert parse_update({"update_id": 2, "callback_query": {"data": "approve:1"}}) == []
    assert parse_update({"update_id": 3, "edited_message": {"text": "x"}}) == []
    group = _tg(text="hi")
    group["message"]["chat"]["type"] = "group"
    assert parse_update(group) == []
    assert parse_update({}) == []
