import hashlib
import hmac
import json

import httpx
import pytest

from saathi.channels.whatsapp import parse_webhook, verify_signature
from saathi.domain import Intent, Understanding
from saathi.legacy import Channel, InboundMessage, MediaKind
from saathi.llm.gateway import (
    BudgetExceeded,
    BudgetLedger,
    Gateway,
    PrivacyViolation,
    Tier,
    TierSpec,
)
from saathi.perception.asr import Transcript
from saathi.pipeline import Deps, build_graph


# ----------------------------------------------------------------- WhatsApp
def _wa_payload(msg: dict) -> dict:
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def test_signature_roundtrip():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, sig)
    assert not verify_signature("secret", body + b" ", sig)
    assert not verify_signature("", body, sig)
    assert not verify_signature("secret", body, None)


def test_parse_text_and_voice():
    t = parse_webhook(_wa_payload({"from": "910000000000", "id": "w1", "type": "text", "text": {"body": "hi"}}))
    assert t[0].kind == MediaKind.TEXT and t[0].text == "hi" and t[0].sender_ref == "+910000000000"
    v = parse_webhook(
        _wa_payload({"from": "910000000000", "id": "w2", "type": "audio",
                     "audio": {"id": "m1", "mime_type": "audio/ogg; codecs=opus"}})
    )
    assert v[0].kind == MediaKind.AUDIO and v[0].media is not None and v[0].media.handle == "m1"


def test_status_updates_ignored():
    assert parse_webhook({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []


# ----------------------------------------------------------------- Gateway
def _chat_response(content: str, cost: float | None = None) -> httpx.Response:
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    if cost is not None:
        usage["cost"] = cost
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": usage})


def _gateway(handler, cap=2000.0) -> Gateway:
    tiers = {
        Tier.T1: TierSpec(Tier.T1, "http://t1/v1", "m1"),
        Tier.T2: TierSpec(Tier.T2, "http://t2/v1", "m2", "k"),
        Tier.T2_FREE: TierSpec(Tier.T2_FREE, "http://free/v1", "mf", "k", allows_personal=False),
        Tier.T3: TierSpec(Tier.T3, "", ""),  # not configured
        Tier.T4: TierSpec(Tier.T4, "http://t4/v1", "m4", "k", paid=True),
    }
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Gateway(tiers, BudgetLedger(cap), usd_to_inr=100.0, client=client)


async def test_privacy_gate_blocks_free_tier_for_personal_data():
    gw = _gateway(lambda r: _chat_response("x"))
    with pytest.raises(PrivacyViolation):
        await gw.chat([{"role": "user", "content": "x"}], tier=Tier.T2_FREE, personal=True)
    res = await gw.chat([{"role": "user", "content": "x"}], tier=Tier.T2_FREE, personal=False)
    assert res.content == "x"


async def test_budget_cap_blocks_paid_tier():
    gw = _gateway(lambda r: _chat_response("ok", cost=0.2), cap=15.0)  # 0.2 USD = ₹20
    await gw.chat([{"role": "user", "content": "a"}], tier=Tier.T4, personal=True, temperature=0.5)
    with pytest.raises(BudgetExceeded):
        await gw.chat([{"role": "user", "content": "b"}], tier=Tier.T4, personal=True)


async def test_cascade_escalates_and_skips_disallowed_tiers():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        conf = 0.3 if req.url.host == "t1" else 0.9
        return _chat_response(json.dumps({"intent": "ride", "confidence": conf}))

    gw = _gateway(handler)
    res = await gw.cascade(
        [{"role": "user", "content": "auto chahiye"}],
        tiers=[Tier.T1, Tier.T2_FREE, Tier.T3, Tier.T2],
        personal=True,
        accept=lambda r: r.json()["confidence"] >= 0.7,
    )
    assert res.tier == Tier.T2
    assert seen == ["t1", "t2"]  # free tier (privacy) and T3 (unconfigured) skipped


async def test_exact_cache_hit_is_free():
    calls = []

    def handler(req):
        calls.append(1)
        return _chat_response("same")

    gw = _gateway(handler)
    msgs = [{"role": "user", "content": "q"}]
    await gw.chat(msgs, tier=Tier.T1, personal=True)
    res = await gw.chat(msgs, tier=Tier.T1, personal=True)
    assert res.cached and len(calls) == 1


# ----------------------------------------------------------------- Pipeline
class FakeASR:
    name = "fake"

    def __init__(self, text):
        self.text = text

    async def transcribe(self, audio, mime):
        return Transcript(text=self.text, provider="fake")


class FakeMedia:
    async def fetch(self, msg):
        return b"audio", "audio/ogg"


class FakeElder:
    def __init__(self):
        self.sent = []

    async def reply(self, msg, text):
        self.sent.append(text)


class FakeCaregiver:
    def __init__(self):
        self.summaries: list[Understanding] = []
        self.alerts: list[str] = []

    async def summary(self, msg, transcript, u):
        self.summaries.append(u)

    async def alert(self, msg, transcript, kind, matches):
        self.alerts.append(kind)


def _deps(asr_text: str, llm_json: dict):
    gw = _gateway(lambda r: _chat_response(json.dumps(llm_json)))
    return Deps(gateway=gw, asr=FakeASR(asr_text), media=FakeMedia(), elder=FakeElder(),
                caregiver=FakeCaregiver(), caregiver_name="Beta")


def _voice() -> InboundMessage:
    return InboundMessage(channel=Channel.WHATSAPP, provider_message_id="w", sender_phone_e164="+91",
                          kind=MediaKind.AUDIO, media_id="m")


async def test_pipeline_summarises_normal_request():
    deps = _deps("kal subah 10 baje mandir ke liye auto chahiye",
                 {"intent": "ride", "confidence": 0.92, "summary_en": "Auto to temple 10am",
                  "slots": {"destination": "temple"}})
    out = await build_graph(deps).ainvoke({"msg": _voice()})
    assert out["understanding"].intent == Intent.RIDE
    assert deps.caregiver.summaries and not deps.caregiver.alerts
    assert "Beta" in deps.elder.sent[0]


async def test_pipeline_emergency_short_circuits_without_llm():
    deps = _deps("seene mein dard ho raha hai", {"intent": "chit_chat", "confidence": 0.99})
    out = await build_graph(deps).ainvoke({"msg": _voice()})
    assert deps.caregiver.alerts == ["EMERGENCY"]
    assert "understanding" not in out and "112" in deps.elder.sent[0]


async def test_pipeline_scam_alert():
    deps = _deps("bank wale OTP maang rahe hain", {"intent": "chit_chat", "confidence": 0.99})
    await build_graph(deps).ainvoke({"msg": _voice()})
    assert deps.caregiver.alerts == ["SCAM"] and "OTP" in deps.elder.sent[0]


async def test_pipeline_redacts_before_llm():
    bodies = []

    def handler(req):
        bodies.append(req.content.decode())
        return _chat_response(json.dumps({"intent": "general_qa", "confidence": 0.9, "summary_en": "q"}))

    deps = _deps("mera number 9876543210 hai, ek sawaal hai", {})
    deps.gateway = _gateway(handler)
    await build_graph(deps).ainvoke({"msg": _voice()})
    assert bodies and "9876543210" not in bodies[0] and "<PHONE>" in bodies[0]
