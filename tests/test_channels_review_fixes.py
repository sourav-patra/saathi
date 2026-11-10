"""Review fixes for the elder channels (RQ-81, RQ-105): typing, signature input, media download, malformed
payloads, error bodies, idempotent sends under concurrency, and one E.164 form. Payloads are synthetic."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import typing
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fakes import FakeClock, FakePersonDirectory

from saathi import ports
from saathi.channels import ChannelParseError
from saathi.channels.telegram_elder import TelegramClient, TelegramElderChannel, parse_update
from saathi.channels.whatsapp import (
    MAX_MEDIA_BYTES,
    WhatsAppClient,
    WhatsAppElderChannel,
    parse_webhook,
    verify_signature,
)
from saathi.domain import MediaRef, Source
from saathi.errors import ChannelDown, MediaExpired, RateLimited, SendRejected
from saathi.ids import PersonRef, TemplateId, idem_key, normalize_e164

ROOT = Path(__file__).resolve().parent.parent
PERSON = PersonRef("p1")
NOW = datetime(2026, 11, 10, tzinfo=UTC)
WA_HOST = "lookaside.example"


def _wa(handler, *, media_hosts=frozenset({WA_HOST})) -> tuple[WhatsAppElderChannel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        out = handler(request)
        return await out if asyncio.iscoroutine(out) else out

    client = WhatsAppClient(
        phone_number_id="1000000000",
        access_token="test-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)),
        media_hosts=media_hosts,
    )
    people = FakePersonDirectory({("p1", "whatsapp"): "+910000000001"})
    return WhatsAppElderChannel(client, people, FakeClock(NOW)), seen


def _tg(handler) -> tuple[TelegramElderChannel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        out = handler(request)
        return await out if asyncio.iscoroutine(out) else out

    client = TelegramClient(token="000000:test-token", client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    people = FakePersonDirectory({("p1", "telegram_elder"): "1000000001"})
    return TelegramElderChannel(client, people), seen


# ----------------------------------------------------------------------------- F1 typing
def test_kind_annotation_matches_port_literal() -> None:
    port_kind = typing.get_type_hints(ports.ElderChannel)["kind"]
    for cls in (WhatsAppElderChannel, TelegramElderChannel):
        assert typing.get_type_hints(cls)["kind"] == port_kind


def test_channels_type_check_as_elder_channel() -> None:
    runner = (
        [shutil.which("mypy")]
        if shutil.which("mypy")
        else ([shutil.which("uvx"), "mypy"] if shutil.which("uvx") else None)
    )
    if runner is None:
        pytest.skip("mypy is not available")
    env = {**os.environ, "MYPYPATH": f"{ROOT / 'src'}:{ROOT / 'tests'}"}
    target = ROOT / "tests" / "typing_cases" / "elder_channel_assign.py"
    done = subprocess.run(
        [*runner, "--python-version", "3.12", "--follow-imports=silent", "--ignore-missing-imports",
         "--no-incremental", "--explicit-package-bases", str(target)],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr


# ----------------------------------------------------------------------------- F2 signature
def test_non_ascii_signature_header_is_false_not_an_error() -> None:
    body = b"{}"
    assert verify_signature("secret", body, "sha256=éé") is False
    assert verify_signature("secret", body, "sha256=" + "٠" * 64) is False
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, good) is True


# ----------------------------------------------------------------------------- F3 media download
def _media_handler(url: str, content: bytes = b"data", headers: dict[str, str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": url, "mime_type": "audio/ogg"})
        return httpx.Response(200, content=content, headers=headers)

    return handler


async def test_media_handle_must_be_digits() -> None:
    channel, seen = _wa(_media_handler(f"https://{WA_HOST}/x"))
    for bad in ("https://evil.example/x", "http://evil.example", "12ab", "../1", "1 2", "", "١٢"):
        with pytest.raises(SendRejected):
            await channel.fetch_media(MediaRef(source=Source.WHATSAPP, handle=bad, mime=None))
    assert seen == []  # nothing left the process, so the token went nowhere


async def test_media_token_only_to_allowlisted_https_host() -> None:
    for url in (
        "http://lookaside.example/x",  # not https
        "https://evil.example/x",  # not allowlisted
        "https://lookaside.example.evil.example/x",
        "https://" + "credentials" + chr(64) + "evil.example/x",
        "ftp://lookaside.example/x",
        "//evil.example/x",
    ):
        channel, seen = _wa(_media_handler(url))
        with pytest.raises(SendRejected):
            await channel.fetch_media(MediaRef(source=Source.WHATSAPP, handle="123", mime=None))
        assert [r.url.host for r in seen] == ["graph.facebook.com"], url  # only the metadata call went out
    channel, seen = _wa(_media_handler(f"https://{WA_HOST}/blob/123", b"ok"))
    blob = await channel.fetch_media(MediaRef(source=Source.WHATSAPP, handle="123", mime=None))
    assert blob.data == b"ok" and seen[-1].headers["authorization"] == "Bearer test-token"


async def test_media_download_is_capped() -> None:
    assert MAX_MEDIA_BYTES == 16 * 1024 * 1024
    big = b"x" * (MAX_MEDIA_BYTES + 1)
    channel, _ = _wa(_media_handler(f"https://{WA_HOST}/b", big))
    with pytest.raises(SendRejected) as info:
        await channel.fetch_media(MediaRef(source=Source.WHATSAPP, handle="123", mime=None))
    assert info.value.code == "media_too_large"
    channel, _ = _wa(_media_handler(f"https://{WA_HOST}/b", b"x", {"content-length": str(MAX_MEDIA_BYTES + 1)}))
    with pytest.raises(SendRejected):
        await channel.fetch_media(MediaRef(source=Source.WHATSAPP, handle="123", mime=None))
    channel, _ = _wa(_media_handler(f"https://{WA_HOST}/b", b"x" * MAX_MEDIA_BYTES))
    blob = await channel.fetch_media(MediaRef(source=Source.WHATSAPP, handle="123", mime=None))
    assert len(blob.data) == MAX_MEDIA_BYTES


async def test_telegram_media_download_is_capped() -> None:
    from saathi.channels.telegram_elder import MAX_MEDIA_BYTES as TG_MAX

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "files/a"}})
        return httpx.Response(200, content=b"x" * (TG_MAX + 1))

    channel, _ = _tg(handler)
    with pytest.raises(SendRejected) as info:
        await channel.fetch_media(MediaRef(source=Source.TELEGRAM_ELDER, handle="f", mime=None))
    assert info.value.code == "media_too_large"


# ----------------------------------------------------------------------------- F4 malformed payloads
def _wa_msg(**over: object) -> dict:
    return {
        "from": "910000000001",
        "id": "wamid.1",
        "timestamp": "1700000000",
        "type": "text",
        "text": {"body": "hi"},
        **over,
    }


def _env(*messages: object) -> dict:
    return {"entry": [{"changes": [{"value": {"messages": list(messages)}}]}]}


def test_malformed_item_is_skipped_and_the_batch_survives() -> None:
    good = _wa_msg(id="wamid.good")
    parsed = parse_webhook(
        _env(
            "not a dict",
            None,
            42,
            _wa_msg(id=""),  # empty id is rejected
            {k: v for k, v in _wa_msg().items() if k != "id"},  # missing id
            _wa_msg(id=None),
            _wa_msg(**{"from": ""}),
            _wa_msg(**{"from": "not a number"}),
            good,
        )
    )
    assert [m.provider_message_id for m in parsed] == ["wamid.good"]


def test_wrong_shaped_fields_inside_an_item_become_unsupported_or_empty() -> None:
    for item in (
        _wa_msg(text="plain string"),
        _wa_msg(text=["x"]),
        _wa_msg(text={"body": 5}),
        _wa_msg(type="image", image="oops"),
        _wa_msg(type="image", image={"id": ["a"], "caption": {"x": 1}}),
        _wa_msg(type="interactive", interactive=[1]),
        _wa_msg(type="interactive", interactive={"button_reply": "x"}),
        _wa_msg(type="button", button="x"),
        _wa_msg(type=["text"]),
        _wa_msg(timestamp={"a": 1}),
    ):
        (m,) = parse_webhook(_env(item), now=NOW)
        assert m.text is None or isinstance(m.text, str)
        assert m.media is None or m.media.handle.isdigit() or isinstance(m.media.handle, str)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        "x",
        {"entry": "x"},
        {"entry": {"changes": []}},
        {"entry": ["x"]},
        {"entry": [{"changes": "x"}]},
        {"entry": [{"changes": ["x"]}]},
        {"entry": [{"changes": [{"value": "x"}]}]},
        {"entry": [{"changes": [{"value": {"messages": "x"}}]}]},
        {"entry": [{"changes": [{"value": {"messages": {"a": 1}}}]}]},
    ],
)
def test_malformed_envelope_raises_one_typed_error(bad: object) -> None:
    with pytest.raises(ChannelParseError):
        parse_webhook(bad)  # type: ignore[arg-type]


def test_empty_and_missing_envelope_parts_stay_empty() -> None:
    assert parse_webhook({}) == [] and parse_webhook({"entry": None}) == []
    assert parse_webhook({"entry": [{"changes": [{"value": None}]}]}) == []


def test_telegram_malformed_updates() -> None:
    with pytest.raises(ChannelParseError):
        parse_update([])  # type: ignore[arg-type]
    base = {"message_id": 5, "date": 1_700_000_000, "chat": {"id": 1000000001, "type": "private"}}
    assert parse_update({"message": "x"}) == [] and parse_update({"message": {"chat": "x"}}) == []
    assert parse_update({"message": {**base, "message_id": None, "text": "hi"}}) == []  # no id: rejected
    for odd in (
        {"text": ["x"]},
        {"voice": "x"},
        {"audio": ["x"]},
        {"photo": "x"},
        {"photo": []},
        {"photo": ["x"]},
        {"video": 3},
        {"document": []},
        {"caption": {"a": 1}, "voice": {"file_id": "f"}},
        {"voice": {"file_id": ["f"]}},
    ):
        (m,) = parse_update({"message": {**base, **odd}})
        assert m.text is None or isinstance(m.text, str)
        assert m.media is None or isinstance(m.media.handle, str)


# ----------------------------------------------------------------------------- F5 telegram bodies
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, [1, 2], SendRejected),
        (400, "text", SendRejected),
        (400, {"ok": False, "error_code": "abc"}, SendRejected),
        (400, {"ok": False, "error_code": None}, SendRejected),
        (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": "soon"}}, RateLimited),
        (429, {"ok": False, "error_code": 429, "parameters": "x"}, RateLimited),
        (429, {"ok": False, "error_code": "429", "parameters": {"retry_after": [1]}}, RateLimited),
        (500, [1], ChannelDown),
        (200, [1], SendRejected),
        (200, {"ok": True, "result": [1]}, SendRejected),
        (200, {"ok": True, "result": "x"}, SendRejected),
    ],
)
async def test_telegram_odd_error_bodies_map_to_port_errors(status: int, body: object, expected: type) -> None:
    channel, _ = _tg(lambda r: httpx.Response(status, json=body))
    with pytest.raises(expected):
        await channel.send_text(PERSON, "hi", key=idem_key("i", "a"))


async def test_telegram_non_json_and_bad_file_path() -> None:
    channel, _ = _tg(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(SendRejected):
        await channel.send_text(PERSON, "hi", key=idem_key("i", "a"))
    channel, _ = _tg(lambda r: httpx.Response(200, json={"ok": True, "result": {"file_path": ["x"]}}))
    with pytest.raises(MediaExpired):
        await channel.fetch_media(MediaRef(source=Source.TELEGRAM_ELDER, handle="f", mime=None))


# ----------------------------------------------------------------------------- F6 unknown person
async def test_unknown_person_is_send_rejected_on_both_channels() -> None:
    wa, wa_seen = _wa(lambda r: httpx.Response(200, json={"messages": [{"id": "w"}]}))
    tg, tg_seen = _tg(lambda r: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}))
    nobody = PersonRef("nobody")
    for coro in (
        wa.send_text(nobody, "hi", key=idem_key("i", "a")),
        wa.send_template(nobody, TemplateId("T-01"), {}, key=idem_key("i", "b")),
        tg.send_text(nobody, "hi", key=idem_key("i", "c")),
    ):
        with pytest.raises(SendRejected) as info:
            await coro
        assert info.value.code == "unknown_person"
    assert wa_seen == [] and tg_seen == []


# ----------------------------------------------------------------------------- F7 concurrent same key
async def test_concurrent_sends_with_one_key_deliver_once() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "w"}]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    for channel, seen in (_wa(slow), _tg(slow)):
        k = idem_key("i", "same")
        results = await asyncio.gather(
            *(channel.send_text(PERSON, "hi", key=k) for _ in range(3)), return_exceptions=True
        )
        assert sum(1 for r in results if not isinstance(r, BaseException)) == 1
        dupes = [r for r in results if isinstance(r, SendRejected)]
        assert len(dupes) == 2 and all(d.code == "duplicate_key" for d in dupes)
        assert len(seen) == 1


async def test_failed_send_releases_the_key_for_the_waiter() -> None:
    calls = {"n": 0}

    async def flaky(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.005)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"messages": [{"id": "w"}]})

    channel, _ = _wa(flaky)
    k = idem_key("i", "retry")
    results = await asyncio.gather(*(channel.send_text(PERSON, "hi", key=k) for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(r, ChannelDown) for r in results) == 1
    assert sum(not isinstance(r, BaseException) for r in results) == 1


async def test_sent_keys_are_bounded() -> None:
    from saathi.channels import SentKeys

    keys = SentKeys(limit=3)
    for n in range(5):
        async with keys.claim(idem_key("i", str(n))):
            keys.record(idem_key("i", str(n)))
    assert len(keys) == 3
    async with keys.claim(idem_key("i", "0")):  # evicted, so no longer remembered
        pass
    with pytest.raises(SendRejected):
        async with keys.claim(idem_key("i", "4")):
            pass
    assert keys._locks == {}  # no lock is left behind


# ----------------------------------------------------------------------------- F9 one E.164 form
def test_normalize_e164_is_one_form() -> None:
    forms = [
        "+91 00000 00001",
        "910000000001",
        "+910000000001",
        "0091-0000-000001",
        "(+91) 0000.000001",
        " 910000000001 ",
    ]
    assert {normalize_e164(f) for f in forms} == {"+910000000001"}
    for bad in ("", "+", "abc", "91 0000 x", "12", "+" + "9" * 16, "٩١" * 5):
        with pytest.raises(ValueError):
            normalize_e164(bad)


def test_webhook_sender_uses_the_shared_form() -> None:
    for raw in ("910000000001", "+910000000001"):
        (m,) = parse_webhook(_env(_wa_msg(**{"from": raw})))
        assert m.sender_ref == normalize_e164(raw) == "+910000000001"


async def test_send_address_uses_the_shared_form() -> None:
    for stored in ("+910000000001", "91 0000 000001", "910000000001"):
        channel, seen = _wa(lambda r: httpx.Response(200, json={"messages": [{"id": "w"}]}))
        channel._people.addresses[("p1", "whatsapp")] = stored  # type: ignore[attr-defined]
        await channel.send_text(PERSON, "hi", key=idem_key("i", "a"))
        assert json.loads(seen[0].read())["to"] == "910000000001"
    channel, seen = _wa(lambda r: httpx.Response(200, json={}))
    channel._people.addresses[("p1", "whatsapp")] = "not a number"  # type: ignore[attr-defined]
    with pytest.raises(SendRejected) as info:
        await channel.send_text(PERSON, "hi", key=idem_key("i", "a"))
    assert info.value.code == "bad_address" and seen == []
