"""Reply text (LT-23; RQ-116) and the reply sender with fakes (RQ-16, 17): a reply is never skipped."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import pytest
from fakes.channels import FakeElderChannel
from fakes.services import FakeAudit
from fakes.voice import FakeAudioCodec, FakeTTS, FakeVoiceStore

from saathi import replies
from saathi.errors import ChannelDown, TtsUnavailable
from saathi.ids import HouseholdId, IdemKey, PersonRef, VoiceId
from saathi.replies import (
    CONSENT_NOTICE,
    NOT_EMERGENCY_MARKER,
    NOT_EMERGENCY_MARKERS,
    REPLY_TEXT,
    consent_notice,
    reply_text,
    send_dynamic,
    send_reply,
)
from saathi.voice.store import ReplyKey

TO = PersonRef("ref-1")
H = HouseholdId("h1")
LANGS = ("hi", "en")
SYNTHETIC_NAMES = ("Asha", "Ravi", "Meera", "Example Lane")


def test_emergency_and_scam_hold_marker_112_108():
    for key in (ReplyKey.EMERGENCY, ReplyKey.SCAM):
        for lang in LANGS:
            text = reply_text(key, lang)
            assert NOT_EMERGENCY_MARKERS[lang] in text, (key, lang)
            assert "112" in text and "108" in text, (key, lang)
    assert NOT_EMERGENCY_MARKER == "Saathi is not an emergency service; it alerts your family"


def test_any_text_that_gives_the_numbers_holds_the_marker():
    for key, texts in REPLY_TEXT.items():
        for lang, text in texts.items():
            if "112" in text or "108" in text:
                assert NOT_EMERGENCY_MARKERS[lang] in text, (key, lang)


def test_the_marker_check_fails_on_a_text_with_the_sentence_removed():
    text = reply_text(ReplyKey.EMERGENCY, "en").replace(NOT_EMERGENCY_MARKERS["en"], "")
    assert NOT_EMERGENCY_MARKERS["en"] not in text  # the check above would fail on this fixture


def test_emergency_text_gives_the_call_instruction_before_the_disclaimer():
    for lang in LANGS:
        text = reply_text(ReplyKey.EMERGENCY, lang)
        assert text.index("112") < text.index(NOT_EMERGENCY_MARKERS[lang]), lang
        assert text.index("108") < text.index(NOT_EMERGENCY_MARKERS[lang]), lang


def test_consent_notice_says_the_same_in_both_languages():
    assert "आपके परिवार द्वारा चुने गए सहायक" in CONSENT_NOTICE["hi"]
    assert "the helper your family chose" in CONSENT_NOTICE["en"]


def test_consent_notice_holds_marker():
    for lang in LANGS:
        text = consent_notice(lang)
        assert NOT_EMERGENCY_MARKERS[lang] in text
        assert "112" in text and "108" in text
    assert set(CONSENT_NOTICE) == set(LANGS)
    assert consent_notice("zz") == consent_notice("hi")


def test_reply_text_holds_no_name():
    for key, texts in REPLY_TEXT.items():
        assert set(texts) == set(LANGS), key
        for text in texts.values():
            assert "{" not in text and "}" not in text  # no template slot a name could fill
            assert not any(n in text for n in SYNTHETIC_NAMES)
            assert set(re.findall(r"\d+", text)) <= {"112", "108"}, text
            assert "@" not in text and "http" not in text


def test_every_key_has_hindi_and_unknown_language_falls_back_to_hindi():
    for key in ReplyKey:
        assert reply_text(key, "hi") == REPLY_TEXT[key]["hi"]
        assert reply_text(key, "zz") == reply_text(key, "hi")


def _alerts() -> tuple[list[str], Callable[[str], Awaitable[None]]]:
    seen: list[str] = []

    async def alert(what: str) -> None:
        seen.append(what)

    return seen, alert


async def _send(ch, voice, audit, alert, key=ReplyKey.EMERGENCY, idem="k1"):
    return await send_reply(
        ch, TO, key, voice=voice, language="hi", idem=IdemKey(idem), owner_alert=alert, audit=audit,
        household=H, run="run-1",
    )  # fmt: skip


async def test_send_reply_sends_clip_then_text():
    ch, voice, audit = FakeElderChannel(), FakeVoiceStore(), FakeAudit()
    voice.add(ReplyKey.EMERGENCY, b"clip-bytes")
    seen, alert = _alerts()
    receipt = await _send(ch, voice, audit, alert)
    assert [m for m, *_ in ch.sent] == ["send_audio", "send_text"]
    assert ch.sent[1][2] == reply_text(ReplyKey.EMERGENCY, "hi")
    assert receipt.key == IdemKey("k1:text")
    assert seen == [] and audit.events == []


@pytest.mark.parametrize("problem", ["missing", "unreviewed", "corrupt"])
async def test_a_clip_problem_sends_text_alone_alerts_and_audits(problem):
    ch, voice, audit = FakeElderChannel(), FakeVoiceStore(), FakeAudit()
    if problem != "missing":
        voice.add(ReplyKey.SCAM, b"clip", reviewed=problem != "unreviewed")
    if problem == "corrupt":
        voice.corrupt(ReplyKey.SCAM)
    seen, alert = _alerts()
    await _send(ch, voice, audit, alert, key=ReplyKey.SCAM)
    assert [m for m, *_ in ch.sent] == ["send_text"]
    assert seen == ["clip_error"]
    [ev] = audit.events
    assert ev.event == "clip_error" and ev.actor == "system:voice" and ev.household == H
    assert ev.detail["reply_key"] == "scam"


async def test_an_alert_or_audit_failure_never_skips_the_reply():
    ch, voice, audit = FakeElderChannel(), FakeVoiceStore(), FakeAudit()
    audit.fail("append", RuntimeError)

    async def bad_alert(_: str) -> None:
        raise RuntimeError("owner chat down")

    await _send(ch, voice, audit, bad_alert)
    assert [m for m, *_ in ch.sent] == ["send_text"]


async def test_an_audio_send_error_still_sends_the_text():
    ch, voice, audit = FakeElderChannel(), FakeVoiceStore(), FakeAudit()
    voice.add(ReplyKey.ACK, b"clip")
    ch.fail("send_audio", ChannelDown)
    seen, alert = _alerts()
    await _send(ch, voice, audit, alert, key=ReplyKey.ACK)
    assert [m for m, *_ in ch.sent] == ["send_text"]
    assert seen == ["clip_error"] and audit.events[0].detail["reason"] == "audio_send_failed"


async def test_dynamic_reply_sends_audio_and_text_and_falls_back_to_text():
    ch = FakeElderChannel()
    kw = {"codec": FakeAudioCodec(), "voice": VoiceId("v")}
    await send_dynamic(ch, TO, "typed text", tts=FakeTTS(), idem=IdemKey("d1"), **kw)
    assert [m for m, *_ in ch.sent] == ["send_audio", "send_text"]
    ch2, tts = FakeElderChannel(), FakeTTS()
    tts.fail("synthesize", TtsUnavailable)
    r = await send_dynamic(ch2, TO, "typed text", tts=tts, idem=IdemKey("d2"), **kw)
    assert [m for m, *_ in ch2.sent] == ["send_text"] and r.key == IdemKey("d2:text")


def test_legacy_names_are_still_there_for_the_old_caller():
    assert callable(replies.emergency_to_elder)


class _RaisingStore:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def fixed(self, key, language):
        raise self.exc


@pytest.mark.parametrize("exc", [RuntimeError("db down"), OSError("disk")])
async def test_any_store_error_still_sends_the_emergency_text(exc):
    ch, audit = FakeElderChannel(), FakeAudit()
    seen, alert = _alerts()
    await _send(ch, _RaisingStore(exc), audit, alert)
    assert [m for m, *_ in ch.sent] == ["send_text"]
    assert ch.sent[0][2] == reply_text(ReplyKey.EMERGENCY, "hi")
    assert seen == ["clip_error"] and audit.events[0].detail["reason"] == "clip_error_other"


async def test_a_plain_error_from_the_audio_send_still_sends_the_emergency_text():
    ch, voice, audit = FakeElderChannel(), FakeVoiceStore(), FakeAudit()
    voice.add(ReplyKey.EMERGENCY, b"clip")
    ch.fail("send_audio", RuntimeError)
    seen, alert = _alerts()
    await _send(ch, voice, audit, alert)
    assert [m for m, *_ in ch.sent] == ["send_text"]
    assert ch.sent[0][2] == reply_text(ReplyKey.EMERGENCY, "hi")
    assert seen == ["clip_error"] and audit.events[0].detail["reason"] == "audio_send_failed"


async def test_dynamic_reply_sends_text_when_speech_raises_a_plain_error():
    ch, tts = FakeElderChannel(), FakeTTS()
    tts.fail("synthesize", OSError)
    kw = {"codec": FakeAudioCodec(), "voice": VoiceId("v"), "idem": IdemKey("d3")}
    r = await send_dynamic(ch, TO, "typed text", tts=tts, **kw)
    assert [m for m, *_ in ch.sent] == ["send_text"] and r.key == IdemKey("d3:text")
