"""LT-51: shared foundations (M01, M03). Ids, clocks, errors, ports and their fakes (RQ-105, 109, 18; PL-01)."""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypeVar, get_type_hints

import pytest
from fakes import (
    FakeASR,
    FakeAudioCodec,
    FakeAudit,
    FakeCaregiverChannel,
    FakeClassifier,
    FakeClock,
    FakeCodec,
    FakeElderChannel,
    FakeEscalator,
    FakeGateway,
    FakeMediaStore,
    FakeOpener,
    FakeSealer,
    FakeStore,
    FakeTrace,
    FakeTTS,
    FakeTx,
    FakeVision,
    FakeVoiceStore,
)
from pydantic import BaseModel

from saathi import domain, errors, ports
from saathi.clock import SystemClock
from saathi.domain import (
    AlertEventRef,
    AlertOpen,
    Approver,
    ApproverUpdate,
    AttemptResult,
    AudioBlob,
    AuditEvent,
    Button,
    Card,
    ChannelCap,
    DataClass,
    Elder,
    Grade,
    ImageBlob,
    InboundMessage,
    Lease,
    MediaBlob,
    MediaKind,
    MediaRef,
    Message,
    ModelRequest,
    NewInbox,
    Sensitivity,
    Source,
    Transcript,
    VisionTask,
    VoiceCallback,
    VoiceClip,
    WavBlob,
)
from saathi.errors import ERRORS_OF, PORT_ERRORS, SaathiError
from saathi.ids import (
    ApproverId,
    ElderId,
    EventId,
    HouseholdId,
    IdemKey,
    InboxId,
    PersonRef,
    ReplyKey,
    RunId,
    TemplateId,
    VoiceId,
    derive_id,
    idem_key,
)

ROOT = Path(__file__).resolve().parents[1]
H = HouseholdId("hh-test")
KEY = IdemKey("k1")
PERSON = PersonRef("person-1")
APPROVER = ApproverId("approver-1")
T0 = datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# clock and ids
# --------------------------------------------------------------------------- #
async def test_fake_clock_wakes_sleepers_in_order() -> None:
    clock = FakeClock(T0)
    order: list[tuple[int, datetime]] = []

    async def sleeper(tag: int, seconds: int) -> None:
        await clock.sleep_until(T0 + timedelta(seconds=seconds))
        order.append((tag, clock.now()))

    tasks = [asyncio.create_task(sleeper(tag, secs)) for tag, secs in ((1, 30), (2, 10), (3, 20))]
    await asyncio.sleep(0)
    await clock.advance(5)
    assert order == []
    await clock.advance(60)
    await asyncio.gather(*tasks)
    assert [t for t, _ in order] == [2, 3, 1]
    assert [w for _, w in order] == [T0 + timedelta(seconds=s) for s in (10, 20, 30)]  # each saw its own due time
    assert clock.now() == T0 + timedelta(seconds=65)
    assert clock.monotonic() == 65


async def test_fake_clock_past_deadline_returns_at_once_and_never_goes_back() -> None:
    clock = FakeClock(T0)
    await asyncio.wait_for(clock.sleep_until(T0 - timedelta(seconds=1)), timeout=1)
    with pytest.raises(ValueError):
        await clock.advance(-1)


def test_system_clock_is_utc_and_monotonic() -> None:
    c = SystemClock()
    assert c.now().utcoffset() == timedelta(0)
    a = c.monotonic()
    assert c.monotonic() >= a


@pytest.mark.parametrize("parts", [(), ("a", ""), ("", "b"), ("a", " ")])
def test_idem_key_rejects_empty_part(parts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        idem_key(*parts)


def test_idem_key_joins_parts() -> None:
    assert idem_key("inbox1", "ack") == "inbox1:ack"


def test_derive_id_is_prefix_underscore_parent() -> None:
    assert derive_id("r", "inbox42") == "r_inbox42"  # the request of an inbox row (LLD build flow 1.1)
    assert derive_id("alert", "x") == derive_id("alert", "x")
    assert derive_id("alert", "x") != derive_id("req", "x")
    assert derive_id("alert", "x") != derive_id("alert", "y")
    for prefix, parent in (("", "x"), ("x", ""), (" ", "x"), ("x", " ")):
        with pytest.raises(ValueError):
            derive_id(prefix, parent)


def test_no_datetime_now_outside_system_clock() -> None:
    files = [ROOT / "src/saathi" / n for n in ("domain.py", "ids.py", "errors.py", "ports.py")]
    files += list((ROOT / "src/saathi/states").glob("*.py")) + list((ROOT / "tests/fakes").glob("*.py"))
    offenders = [f.name for f in files if re.search(r"datetime\.now\(", f.read_text())]
    assert offenders == []


# --------------------------------------------------------------------------- #
# errors (TRD ports section 3)
# --------------------------------------------------------------------------- #
def _trd_error_table() -> dict[str, set[str]]:
    text = (ROOT / "docs/design/TRD-saathi-ports-2026-11-09.md").read_text()
    section = text.split("## 3. Errors")[1].split("## 4.")[0]
    table: dict[str, set[str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 4 or cells[1] in {"Port", ""} or set(cells[1]) <= {"-"}:
            continue
        table[cells[1]] = set(re.findall(r"`([A-Z][A-Za-z]+)(?:\([^)]*\))?`", cells[2]))
    return table


def test_errors_cover_trd_port_list() -> None:
    trd = _trd_error_table()
    assert trd, "the TRD errors table was not found"
    assert set(PORT_ERRORS) == set(trd)
    for port, names in trd.items():
        assert {e.__name__ for e in PORT_ERRORS[port]} == names, port
    for err in {e for group in PORT_ERRORS.values() for e in group}:
        assert issubclass(err, SaathiError)
        assert err.__module__ == "saathi.errors"
        assert isinstance(err(), SaathiError)  # buildable with no arguments, so a fake can raise it


def test_error_payloads() -> None:
    assert errors.ProactiveNotAllowed("window_closed").reason == "window_closed"
    assert errors.RateLimited(3.0).retry_after == 3.0
    assert errors.SendRejected("131047").code == "131047"


# --------------------------------------------------------------------------- #
# ports and fakes
# --------------------------------------------------------------------------- #
FAKES: dict[type, object] = {
    ports.ElderChannel: FakeElderChannel(),
    ports.CaregiverChannel: FakeCaregiverChannel(),
    ports.Escalator: FakeEscalator(),
    ports.ASR: FakeASR(),
    ports.AudioCodec: FakeAudioCodec(),
    ports.TTS: FakeTTS(),
    ports.VoiceStore: FakeVoiceStore(),
    ports.Vision: FakeVision(),
    ports.Gateway: FakeGateway(),
    ports.AuditSink: FakeAudit(),
    ports.Clock: FakeClock(),
    ports.Store: FakeStore(),
    ports.Tx: FakeTx(household=HouseholdId("hh-test"), now=datetime(2026, 1, 1, tzinfo=UTC)),
    ports.Classifier: FakeClassifier(),
    ports.TraceExporter: FakeTrace(),
    ports.Sealer: FakeSealer(),
    ports.Opener: FakeOpener(),
    ports.MediaStore: FakeMediaStore(),
}


def _protocols() -> list[type]:
    return [
        c
        for _, c in inspect.getmembers(ports, inspect.isclass)
        if getattr(c, "_is_protocol", False) and c.__module__ == ports.__name__
    ]


def test_every_port_has_a_fake() -> None:
    protocols = _protocols()
    assert len(protocols) >= 17
    assert set(protocols) - set(FAKES) == set(), "a port has no fake"
    for proto, fake in FAKES.items():
        assert isinstance(fake, proto), f"{type(fake).__name__} does not satisfy {proto.__name__}"


def _norm(hint: object) -> object:
    """Type variables are made per function (PEP 695), so compare them by name."""
    if isinstance(hint, TypeVar):
        return f"~{hint.__name__}"
    meta = getattr(hint, "__pydantic_generic_metadata__", None)  # ModelRequest[T] is a new class per T
    if meta and meta["origin"] is not None:
        return (meta["origin"], tuple(_norm(a) for a in meta["args"]))
    return hint


def _hints(fn: object) -> dict[str, object]:
    out = {}
    for name, hint in get_type_hints(fn).items():
        origin = getattr(hint, "__origin__", None)
        args = getattr(hint, "__args__", ())
        out[name] = (origin, tuple(_norm(a) for a in args)) if origin and args else _norm(hint)
    return out


# `tx` is an @asynccontextmanager in the fake and returns the manager in the port; only its return differs.
RETURN_MAY_DIFFER = {("FakeStore", "tx")}


def test_fake_signatures_match_their_ports() -> None:
    for proto, fake in FAKES.items():
        for name, member in inspect.getmembers(proto, inspect.isfunction):
            if name.startswith("_"):
                continue
            where = f"{type(fake).__name__}.{name}"
            impl = getattr(fake, name)
            want = list(inspect.signature(member).parameters.values())[1:]
            got = list(inspect.signature(impl).parameters.values())
            assert [(p.name, p.kind) for p in got] == [(p.name, p.kind) for p in want], where
            assert [p.default for p in got] == [p.default for p in want], f"{where}: defaults differ"
            want_hints, got_hints = _hints(member), _hints(impl)
            if (type(fake).__name__, name) in RETURN_MAY_DIFFER:
                want_hints.pop("return", None)
                got_hints.pop("return", None)
            assert got_hints == want_hints, f"{where}: type hints differ"
            assert inspect.iscoroutinefunction(member) == (
                inspect.iscoroutinefunction(impl) or inspect.isasyncgenfunction(impl)
            ) or name in {"updates", "tx"}, f"{where} sync/async differs"


def test_signature_check_catches_a_wrong_annotation_or_default() -> None:
    """The check is shown to fail: the same comparison on a copy of FakeTTS with one hint and one default changed."""

    class Bad(FakeTTS):
        async def synthesize(self, text: str, *, voice: int, max_seconds: float = 3) -> domain.VoiceClip:  # type: ignore[override]
            return await super().synthesize(text, voice=VoiceId("v"), max_seconds=max_seconds)

    member = ports.TTS.synthesize
    assert _hints(Bad().synthesize) != _hints(member)
    ok = {p.name: p.default for p in inspect.signature(FakeTTS().synthesize).parameters.values()}
    bad = {p.name: p.default for p in inspect.signature(Bad().synthesize).parameters.values()}
    assert ok != bad


MEDIA_REF = MediaRef(source=Source.WHATSAPP, handle="m1")
CLIP = VoiceClip(key=ReplyKey("r"), data=b"x", sha256="0" * 64)
EVENT = AlertEventRef(household_id=H, event_id=EventId("e1"), kind="emergency")
CALLBACK = VoiceCallback(call_id="c", digit="1", signature="bad", timestamp=T0)


class _Out(BaseModel):
    answer: str = ""


def _gateway_call(f: FakeGateway):
    req = ModelRequest(step="s", messages=[Message(role="user", content="hi")], schema_=_Out, run=RunId("r"))
    return f.complete(req)


async def _tx_call(f: FakeStore):
    async with f.tx(household=H):
        pass


AUDIT_EVENT = AuditEvent(household=H, run_id=RunId("r"), actor="system:test", event="k")

# (port name in the TRD table, fake factory, {method: call})
CALLS = [
    ("ElderChannel", FakeElderChannel, {
        "send_text": lambda f: f.send_text(PERSON, "hi", key=KEY),
        "send_audio": lambda f: f.send_audio(PERSON, CLIP, key=KEY),
        "send_image": lambda f: f.send_image(PERSON, ImageBlob(data=b"i"), "c", key=KEY),
        "send_template": lambda f: f.send_template(PERSON, TemplateId("t"), {"a": "b"}, key=KEY),
        "fetch_media": lambda f: f.fetch_media(MEDIA_REF),
        "health": lambda f: f.health(),
    }),
    ("CaregiverChannel", FakeCaregiverChannel, {
        "send_card": lambda f: f.send_card(APPROVER, Card(title="k"), key=KEY),
        "send_text": lambda f: f.send_text(APPROVER, "hi", key=KEY),
        "send_audio": lambda f: f.send_audio(APPROVER, AudioBlob(data=b"a"), "c", key=KEY),
        "health": lambda f: f.health(),
    }),
    ("Escalator", FakeEscalator, {
        "notify": lambda f: f.notify(EVENT, APPROVER, key=KEY),
        "on_dtmf": lambda f: f.on_dtmf(CALLBACK),
    }),
    ("ASR, AudioCodec", FakeASR, {"transcribe": lambda f: f.transcribe(AudioBlob(data=b"a"))}),
    ("ASR, AudioCodec", FakeCodec, {
        "to_wav16k": lambda f: f.to_wav16k(AudioBlob(data=b"a"), max_seconds=5),
        "encode_clip": lambda f: f.encode_clip(WavBlob(data=b"w")),
    }),
    ("TTS, VoiceStore", FakeTTS, {"synthesize": lambda f: f.synthesize("hi", voice=VoiceId("v"), max_seconds=5)}),
    ("TTS, VoiceStore", FakeVoiceStore, {"fixed": lambda f: f.fixed(ReplyKey("r"))}),
    ("Vision", FakeVision, {"read": lambda f: f.read(ImageBlob(data=b"i"), VisionTask.OCR, run=RunId("r"))}),
    ("Gateway", FakeGateway, {"complete": _gateway_call}),
    ("AuditSink", FakeAudit, {"append": lambda f: f.append(AUDIT_EVENT)}),
    ("Store, KeyRing", FakeStore, {"tx": _tx_call}),
]


@pytest.mark.parametrize(("port", "factory", "calls"), CALLS, ids=lambda v: v if isinstance(v, str) else "")
async def test_fake_fails_with_each_listed_error(port: str, factory, calls) -> None:
    for err in PORT_ERRORS[port]:
        for method, call in calls.items():
            fake = factory()
            fake.fail(method, err)
            with pytest.raises(err):
                await call(fake)
            # the failure was one-shot: the next call is allowed to reach the fake's own behaviour
            assert fake.calls_of(method)


def test_every_listed_error_for_a_faked_port_is_exercised() -> None:
    exercised = {port for port, _, _ in CALLS}
    assert exercised == set(PORT_ERRORS) - {"CommerceAdapter", "PhoneDevice"}  # Phase 1 ports have no body yet


async def test_fake_fail_times_and_clear() -> None:
    f = FakeASR(text="hello")
    f.fail("transcribe", errors.AsrUnavailable, times=2)
    for _ in range(2):
        with pytest.raises(errors.AsrUnavailable):
            await f.transcribe(AudioBlob(data=b"a"))
    assert (await f.transcribe(AudioBlob(data=b"a"))).text == "hello"
    f.fail("transcribe", errors.AsrUnavailable)
    f.clear_failures()
    assert (await f.transcribe(AudioBlob(data=b"a"))).text == "hello"


# --------------------------------------------------------------------------- #
# fake behaviour the later units rely on
# --------------------------------------------------------------------------- #
async def test_channels_refuse_a_repeated_key_by_default() -> None:
    ch = FakeElderChannel()
    await ch.send_text(PERSON, "a", key=KEY)
    with pytest.raises(errors.SendRejected) as exc:
        await ch.send_text(PERSON, "a", key=KEY)
    assert exc.value.code == "duplicate_key" and len(ch.sent) == 1
    cg = FakeCaregiverChannel()
    await cg.send_card(APPROVER, Card(title="k"), key=KEY)
    with pytest.raises(errors.SendRejected):
        await cg.send_card(APPROVER, Card(title="k"), key=KEY)
    await cg.send_text(APPROVER, "t", key=IdemKey("k2"))
    with pytest.raises(errors.SendRejected):
        await cg.send_text(APPROVER, "t", key=IdemKey("k2"))
    assert len(cg.sent) == 2


async def test_channels_with_strict_keys_off_return_the_first_receipt() -> None:
    ch = FakeElderChannel(strict_keys=False)
    r1 = await ch.send_text(PERSON, "a", key=KEY)
    r2 = await ch.send_text(PERSON, "a", key=KEY)
    assert r1 == r2 and len(ch.sent) == 1
    cg = FakeCaregiverChannel(strict_keys=False)
    ref = await cg.send_card(APPROVER, Card(title="k"), key=KEY)
    assert await cg.send_card(APPROVER, Card(title="k"), key=KEY) == ref and len(cg.cards) == 1


async def test_elder_channel_window_maps_to_proactive_not_allowed() -> None:
    ch = FakeElderChannel()
    ch.window_open = False
    with pytest.raises(errors.ProactiveNotAllowed) as exc:
        await ch.send_text(PERSON, "a", key=KEY)
    assert exc.value.reason == "window_closed"
    await ch.send_template(PERSON, TemplateId("t"), {}, key=IdemKey("k2"))  # a template still goes
    with pytest.raises(errors.MediaExpired):
        await ch.fetch_media(MEDIA_REF)
    ch.media["m1"] = MediaBlob(data=b"x", mime="audio/ogg")
    assert (await ch.fetch_media(MEDIA_REF)).data == b"x"


def test_elder_channel_defaults_are_both_platforms_capable() -> None:
    assert FakeElderChannel("telegram_elder").kind == "telegram_elder"
    assert set(ChannelCap) == {
        "voice_in", "voice_out", "image_in", "image_out", "video_in", "text", "proactive_free", "templates",
    }  # fmt: skip


async def test_caregiver_updates_yield_pushed_updates() -> None:
    cg = FakeCaregiverChannel()
    up = ApproverUpdate(update_id=1, approver_id=APPROVER, kind="callback", data="ack", nonce="n")
    cg.push_update(up)
    assert [u async for u in cg.updates()] == [up]
    ref = await cg.send_card(APPROVER, Card(title="k", buttons=[Button(text="Acknowledge", data="ack:e1")]), key=KEY)
    await cg.edit_card(ref, Card(title="edited"))
    assert cg.cards[ref].title == "edited" and cg.cards[ref].lines == []


async def test_escalator_script_signoff_and_callbacks() -> None:
    esc = FakeEscalator("c")
    esc.will_return(AttemptResult.VOICEMAIL)
    assert await esc.notify(EVENT, APPROVER, key=KEY) == AttemptResult.VOICEMAIL
    assert await esc.notify(EVENT, APPROVER, key=KEY) == AttemptResult.VOICEMAIL  # same key, same attempt
    assert await esc.notify(EVENT, APPROVER, key=IdemKey("k2")) == AttemptResult.DELIVERED
    ts = T0
    with pytest.raises(errors.CallbackRejected):
        await esc.on_dtmf(VoiceCallback(call_id="call-1", digit="1", signature="forged", timestamp=ts))
    with pytest.raises(errors.CallbackRejected):
        await esc.on_dtmf(VoiceCallback(call_id="unknown", digit="1", signature="valid", timestamp=ts))
    ok = VoiceCallback(call_id="call-1", digit="1", signature="valid", timestamp=ts)
    assert (await esc.on_dtmf(ok)).approver_id == APPROVER
    with pytest.raises(errors.CallbackRejected):  # replay
        await esc.on_dtmf(ok)
    blocked = FakeEscalator("b")
    blocked.signoff_valid = False
    with pytest.raises(errors.SignoffMissing):
        await blocked.notify(EVENT, APPROVER, key=KEY)
    assert blocked.notified == []  # nothing sent
    assert await FakeEscalator("a").notify(EVENT, APPROVER, key=KEY) == AttemptResult.DELIVERED


async def test_voice_store_checks_the_hash() -> None:
    vs = FakeVoiceStore()
    with pytest.raises(errors.ClipMissing):
        await vs.fixed(ReplyKey("ack"))
    clip = vs.add(ReplyKey("ack"), b"bytes")
    assert await vs.fixed(ReplyKey("ack")) == clip
    vs.corrupt(ReplyKey("ack"))
    with pytest.raises(errors.ClipHashMismatch):
        await vs.fixed(ReplyKey("ack"))


async def test_gateway_fake_is_scripted_and_records_sensitivity() -> None:
    gw = FakeGateway()
    with pytest.raises(errors.NoAcceptedResult):
        await _gateway_call(gw)
    gw.will_return(_Out(answer="x"))
    res = await _gateway_call(gw)
    assert res.value == _Out(answer="x")
    assert gw.requests[-1].sensitivity == Sensitivity.PERSONAL  # elder content is personal by default
    assert res.cost_inr == Decimal(0) and res.grade == Grade.G1


async def test_store_requires_a_household() -> None:
    store = FakeStore(FakeClock(T0))
    with pytest.raises(TypeError):
        async with store.tx():  # type: ignore[call-arg]
            pass
    with pytest.raises(errors.TenantNotSet):
        async with store.tx(household=HouseholdId("")):
            pass
    async with store.tx(household=H, lock_household=True) as tx:
        await tx.execute("select 1")
        assert tx.household == H and tx.now == T0
    assert store.locked == [H] and store.transactions[0].statements == [("select 1", ())]


async def test_sealer_and_opener_bind_the_aad() -> None:
    sealer, opener = FakeSealer(), FakeOpener()
    store = FakeStore()
    blob = sealer.seal(b"pk", b"secret", aad=b"row-1")  # type: ignore[arg-type]
    async with store.tx(household=H) as tx:
        with pytest.raises(errors.KeyMissing):
            await opener.open(tx, blob, "k1", aad=b"row-1")
        opener.keys.add("k1")
        assert await opener.open(tx, blob, "k1", aad=b"row-1") == b"secret"
        with pytest.raises(errors.KeyMissing):  # moved to another row
            await opener.open(tx, blob, "k1", aad=b"row-2")


async def test_media_store_and_classifier_and_trace_fakes() -> None:
    ms = FakeMediaStore()
    key = await ms.put(MEDIA_REF, MediaBlob(data=b"x", mime="a/b"), household=H)
    assert (await ms.get(key)).data == b"x" and key.startswith(H)
    clf = FakeClassifier()
    from saathi.domain import ClassifierFlag

    clf.add_rule("chest pain", ClassifierFlag(kind="emergency", score=0.9))
    assert await clf.flags("synthetic chest pain text", run=RunId("r")) == [ClassifierFlag(kind="emergency", score=0.9)]
    assert await clf.flags("nothing", run=RunId("r")) == []
    tr = FakeTrace()
    tr.span("step", ok=True)
    assert tr.spans == [("step", {"ok": True})]


# --------------------------------------------------------------------------- #
# domain: tenancy (RQ-109), no addresses in core types (RQ-105), data classes (PL-01)
# --------------------------------------------------------------------------- #
def _models() -> list[type[BaseModel]]:
    return [
        c
        for _, c in inspect.getmembers(domain, inspect.isclass)
        if issubclass(c, BaseModel) and c.__module__ == domain.__name__
    ]


def test_elder_scoped_types_carry_household_id() -> None:
    for model in (Elder, Approver, NewInbox, Lease, AlertEventRef):
        assert "household_id" in model.model_fields, model.__name__
    assert "household" in AuditEvent.model_fields  # M17 names it `household`
    assert get_type_hints(Elder)["household_id"] is HouseholdId
    assert get_type_hints(Elder)["id"] is ElderId
    assert get_type_hints(Lease)["inbox_id"] is InboxId


def test_core_types_hold_no_phone_number_or_chat_id() -> None:
    banned = re.compile(r"phone|e164|chat_id|msisdn|wa_id|telegram_user", re.IGNORECASE)
    for model in _models():
        if model is Approver:  # Phase 1 type: the approver's Telegram id stays until the approval service (hub K)
            continue
        offenders = [f for f in model.model_fields if banned.search(f)]
        assert offenders == [], f"{model.__name__}: {offenders}"
    assert not hasattr(domain, "Channel")


def test_inbound_message_names_a_source_not_a_platform_client() -> None:
    msg = InboundMessage(
        source=Source.WHATSAPP, provider_message_id="p1", sender_ref="s", kind=MediaKind.TEXT, text="hi", received_at=T0
    )
    assert msg.source == "whatsapp"
    assert MediaKind.UNSUPPORTED == "unsupported"
    assert {s.value for s in Source} == {"whatsapp", "telegram_elder", "caregiver_forward"}
    assert AlertOpen(kind="unscanned", origin="edge").rule_ids == []


def test_data_classes_pl01() -> None:
    assert {c.value for c in DataClass} == {"restricted", "personal", "public", "synthetic"}


def test_m15_m17_m19_value_types_have_the_lld_fields() -> None:
    """Field names exactly as the LLD safety part gives them (M15 line 46, M17 line 76, M19 lines 105-111)."""
    assert list(Card.model_fields) == ["title", "lines", "buttons"]
    assert list(Button.model_fields) == ["text", "data"]
    assert list(AuditEvent.model_fields) == [
        "household", "run_id", "actor", "event", "subject", "detail", "sensitive",
    ]  # fmt: skip
    assert list(ModelRequest.model_fields) == [
        "step", "messages", "schema_", "run", "sensitivity", "intent", "media",
    ]  # fmt: skip
    assert list(domain.ModelResult.model_fields) == [
        "value", "refusal", "refusal_reason", "grade", "tokens", "cost_inr", "cached",
    ]  # fmt: skip
    assert {s.name: s.value for s in Sensitivity} == {"PERSONAL": "personal", "PUBLIC": "public"}
    assert [(g.name, int(g)) for g in Grade] == [(f"G{i}", i) for i in range(5)]
    assert ModelRequest(step="s", messages=[], schema_=_Out, run=RunId("r")).sensitivity is Sensitivity.PERSONAL


def test_audit_event_actor_and_detail_are_checked() -> None:
    from pydantic import ValidationError

    detail = {"n": 1, "k": "a:b.c"}
    ok = AuditEvent(household=H, run_id=RunId("r"), actor="approver:a-1", event="decided", detail=detail)
    assert ok.subject is None and ok.sensitive == {}
    for actor in ("someone", "approver:", "elder ", "system:x y"):
        with pytest.raises(ValidationError):
            AuditEvent(household=H, run_id=RunId("r"), actor=actor, event="e")
    with pytest.raises(ValidationError):  # a free-text detail value could carry personal text
        AuditEvent(household=H, run_id=RunId("r"), actor="elder", event="e", detail={"k": "has space"})


def test_received_at_is_required() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InboundMessage(source=Source.WHATSAPP, provider_message_id="p", sender_ref="s", kind=MediaKind.TEXT)


def test_new_id_helpers_reexported_from_domain() -> None:
    assert domain.new_id is not None and domain.idem_key is idem_key and domain.derive_id is derive_id
    assert len(domain.new_id()) == 32


# --------------------------------------------------------------------------- #
# the fixes of the WU-03 review: fakes the LLD names, scripted tags, failable tx and sealer, ERRORS_OF, legacy
# --------------------------------------------------------------------------- #
async def test_fake_asr_script_and_fake_codec_name() -> None:
    asr = FakeASR(["first", errors.AsrUnavailable, Transcript(text="third")], text="after")
    audio = AudioBlob(data=b"a")
    assert (await asr.transcribe(audio)).text == "first"
    with pytest.raises(errors.AsrUnavailable):
        await asr.transcribe(audio)
    assert (await asr.transcribe(audio)).text == "third"
    assert (await asr.transcribe(audio)).text == "after"
    with pytest.raises(TypeError):
        FakeASR("one string")  # type: ignore[arg-type]
    codec = FakeCodec()
    assert isinstance(codec, ports.AudioCodec) and FakeCodec is FakeAudioCodec
    codec.fail("to_wav16k", errors.DecodeTimeout)
    with pytest.raises(errors.DecodeTimeout):
        await codec.to_wav16k(audio, max_seconds=1)


async def test_unreviewed_clip_raises_clip_unreviewed() -> None:
    assert issubclass(errors.ClipUnreviewed, errors.ClipMissing)  # a caller that handles a missing clip handles it
    vs = FakeVoiceStore()
    vs.add(ReplyKey("ack"), b"bytes", reviewed=False)
    with pytest.raises(errors.ClipUnreviewed):
        await vs.fixed(ReplyKey("ack"))
    vs.add(ReplyKey("ack"), b"bytes")  # the review is recorded
    assert (await vs.fixed(ReplyKey("ack"))).data == b"bytes"


async def test_tx_execute_returns_scripted_tags_and_can_fail() -> None:
    store = FakeStore(FakeClock(T0))
    store.tags.extend(["INSERT 0 0", "UPDATE 1"])
    async with store.tx(household=H) as tx:
        assert await tx.execute("insert") == "INSERT 0 0"
        assert await tx.execute("update") == "UPDATE 1"
        assert await tx.execute("insert") == "INSERT 0 1"  # the default tag
        tx.fail("execute", errors.ConflictLost)  # type: ignore[attr-defined]
        with pytest.raises(errors.ConflictLost):
            await tx.execute("update")
        tx.fail("fetch", errors.StoreUnavailable)  # type: ignore[attr-defined]
        with pytest.raises(errors.StoreUnavailable):
            await tx.fetch("select")
    assert FakeStore(default_tag="DELETE 0")  # a store may change the default
    direct = FakeTx(H, T0, tags=["UPDATE 2"], default_tag="DELETE 0")
    assert [await direct.execute("a"), await direct.execute("b")] == ["UPDATE 2", "DELETE 0"]


def test_sealer_can_fail_on_demand() -> None:
    sealer = FakeSealer()
    sealer.fail("seal", errors.KeyMissing)
    with pytest.raises(errors.KeyMissing):
        sealer.seal(b"pk", b"x", aad=b"a")  # type: ignore[arg-type]
    assert sealer.seal(b"pk", b"x", aad=b"a")  # type: ignore[arg-type]


def test_errors_of_is_keyed_by_protocol_class() -> None:
    assert set(ERRORS_OF) == set(_protocols())
    assert ERRORS_OF[ports.ElderChannel] == PORT_ERRORS["ElderChannel"]
    assert ERRORS_OF[ports.ASR] == ERRORS_OF[ports.AudioCodec] == PORT_ERRORS["ASR, AudioCodec"]
    assert ERRORS_OF[ports.VoiceStore] == PORT_ERRORS["TTS, VoiceStore"]
    assert errors.KeyMissing in ERRORS_OF[ports.Opener] and errors.TenantNotSet in ERRORS_OF[ports.Store]
    assert ERRORS_OF[ports.Clock] == ()
    listed = {e for group in PORT_ERRORS.values() for e in group}
    assert {e for group in ERRORS_OF.values() for e in group} <= listed


# modules that import saathi.legacy today. The set may only shrink: see the removal plan in legacy.py.
LEGACY_IMPORTERS = {
    "src/saathi/channels/telegram.py",  # WU-18
}


LEGACY_IMPORT = re.compile(
    r"^\s*(from saathi\.legacy\b|from saathi import[^\n]*\blegacy\b|import saathi\.legacy\b)", re.M
)


def _legacy_importers() -> set[str]:
    found = set()
    for base in ("src", "tests", "scripts", "evals"):
        for path in (ROOT / base).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel == "src/saathi/legacy.py" or rel == "tests/test_foundation.py":
                continue
            if LEGACY_IMPORT.search(path.read_text()):
                found.add(rel)
    return found


def test_legacy_importers_only_shrink() -> None:
    now = _legacy_importers()
    assert now <= LEGACY_IMPORTERS, f"new importer of saathi.legacy: {sorted(now - LEGACY_IMPORTERS)}"
    plan = (ROOT / "src/saathi/legacy.py").read_text()
    for unit in ("WU-08", "WU-14", "WU-15", "WU-18", "WU-20"):
        assert unit in plan, f"legacy.py removal plan lacks {unit}"
