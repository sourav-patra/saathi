"""Reply text and the reply sender (M16; RQ-16, 17, 74, 78, 116; D-69, D-71).

Reply text is constants in Hindi and English. None holds a name, an address or a number except 108 and 112.
Every text that gives those numbers also says that Saathi is not an emergency service (RQ-116).
`send_reply` sends the signed fixed clip and the text; when the clip cannot be used it sends the text alone.

The functions at the end (`emergency_to_elder` and the rest) interpolate a caregiver name. They are kept only until
WU-15 rewrites `pipeline.py`, their one caller; nothing new may call them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from saathi.audit.events import AuditName
from saathi.domain import AuditEvent, SendReceipt, WavBlob
from saathi.errors import ClipHashMismatch, ClipMissing, ClipUnreviewed
from saathi.ids import HouseholdId, IdemKey, PersonRef, RunId, VoiceId
from saathi.ports import TTS, AudioCodec, AuditSink, ElderChannel, VoiceStore
from saathi.voice.store import ReplyKey

DEFAULT_LANGUAGE: Final = "hi"
EMERGENCY_NUMBERS: Final = ("112", "108")

# "Saathi is not an emergency service; it alerts your family" in each language (RQ-116).
NOT_EMERGENCY_MARKERS: Final[dict[str, str]] = {
    "en": "Saathi is not an emergency service; it alerts your family",
    "hi": "साथी आपातकालीन सेवा नहीं है; यह आपके परिवार को खबर करता है",
}
NOT_EMERGENCY_MARKER: Final = NOT_EMERGENCY_MARKERS["en"]

REPLY_TEXT: Final[dict[ReplyKey, dict[str, str]]] = {
    ReplyKey.EMERGENCY: {
        "hi": f"अभी 112 या एम्बुलेंस के लिए 108 पर फ़ोन करें। {NOT_EMERGENCY_MARKERS['hi']}। आस-पास किसी को आवाज़ दें।",
        "en": f"Call 112 now, or 108 for an ambulance. {NOT_EMERGENCY_MARKERS['en']}. Call out to someone nearby.",
    },
    ReplyKey.SCAM: {
        "hi": "सावधान: किसी को भी OTP, PIN या बैंक की जानकारी न बताएं, और अनजान लिंक पर क्लिक न करें। "
        f"{NOT_EMERGENCY_MARKERS['hi']}। ज़रूरत हो तो 112 या 108 पर फ़ोन करें।",
        "en": "Be careful: do not tell anyone your OTP, PIN or bank details, and do not click an unknown link. "
        f"{NOT_EMERGENCY_MARKERS['en']}. If you need help, call 112 or 108.",
    },
    ReplyKey.ACK: {
        "hi": "ठीक है, आपका संदेश मिल गया।",
        "en": "Okay, your message has arrived.",
    },
    ReplyKey.TOLD_CAREGIVER: {
        "hi": "मैंने आपके परिवार को बता दिया है।",
        "en": "I have told your family.",
    },
    ReplyKey.NOT_UNDERSTOOD: {
        "hi": "माफ़ कीजिए, मैं समझ नहीं पाया। कृपया फिर से बताइए।",
        "en": "Sorry, I did not understand. Please tell me again.",
    },
    ReplyKey.EXPIRED: {
        "hi": "यह अनुरोध पुराना हो गया है। ज़रूरत हो तो फिर से बताइए।",
        "en": "This request has expired. Please ask again if you still need it.",
    },
    ReplyKey.CHANNEL_DOWN: {
        "hi": "अभी संदेश भेजने में दिक्कत है। थोड़ी देर बाद फिर कोशिश करें। "
        f"{NOT_EMERGENCY_MARKERS['hi']}। ज़रूरी हो तो 112 या 108 पर फ़ोन करें।",
        "en": "There is a problem sending messages right now. Please try again in a little while. "
        f"{NOT_EMERGENCY_MARKERS['en']}. If it is urgent, call 112 or 108.",
    },
}

CONSENT_NOTICE_VERSION: Final = "n1"
CONSENT_NOTICE: Final[dict[str, str]] = {
    "hi": "साथी आपकी आवाज़ के संदेश सुनकर आपका अनुरोध आपके परिवार द्वारा चुने गए सहायक तक पहुँचाता है। "
    "आप जब चाहें, बिना कारण बताए, इसे बंद कर सकते हैं। "
    f"{NOT_EMERGENCY_MARKERS['hi']}। आपात स्थिति में 112 या 108 पर फ़ोन करें।",
    "en": "Saathi listens to your voice messages and passes your request to the helper your family chose. "
    "You can stop it at any time without giving a reason. "
    f"{NOT_EMERGENCY_MARKERS['en']}. In an emergency, call 112 or 108.",
}


def reply_text(key: ReplyKey, language: str = DEFAULT_LANGUAGE) -> str:
    """The text of a reply key. An unknown language falls back to Hindi: a reply is never skipped."""
    texts = REPLY_TEXT[ReplyKey(key)]
    return texts.get(language, texts[DEFAULT_LANGUAGE])


def consent_notice(language: str = DEFAULT_LANGUAGE) -> str:
    return CONSENT_NOTICE.get(language, CONSENT_NOTICE[DEFAULT_LANGUAGE])


async def _clip_failed(
    reason: str,
    key: ReplyKey,
    *,
    audit: AuditSink,
    household: HouseholdId,
    run: RunId,
    owner_alert: Callable[[str], Awaitable[None]],
) -> None:
    """Tell the owner and write the audit row. Neither may stop the reply, so their own errors are not raised."""
    try:
        await owner_alert("clip_error")
    except Exception:  # noqa: BLE001  (the reply goes out whatever the alert does)
        pass
    try:
        await audit.append(
            AuditEvent(
                household=household,
                run_id=run,
                actor="system:voice",
                event=AuditName.CLIP_ERROR,
                detail={"reply_key": str(key), "reason": reason},
            )
        )
    except Exception:  # noqa: BLE001
        pass


async def send_reply(
    ch: ElderChannel,
    to: PersonRef,
    key: ReplyKey,
    *,
    voice: VoiceStore,
    language: str = DEFAULT_LANGUAGE,
    idem: IdemKey,
    owner_alert: Callable[[str], Awaitable[None]],
    audit: AuditSink,
    household: HouseholdId,
    run: RunId,
) -> SendReceipt:
    """Send the fixed clip and the text. Any clip error sends the text alone, alerts the owner and audits; never skips.

    The two sends use the keys `<idem>:audio` and `<idem>:text`. The receipt returned is the text send.
    """
    text = reply_text(key, language)
    failure: str | None = None
    try:
        clip = await voice.fixed(key, language)
    except ClipUnreviewed:
        failure = "unreviewed"
    except ClipMissing:
        failure = "missing"
    except ClipHashMismatch:
        failure = "hash_mismatch"
    except Exception:  # noqa: BLE001  (the text must go out whatever the store does)
        failure = "clip_error_other"
    else:
        try:
            await ch.send_audio(to, clip, key=IdemKey(f"{idem}:audio"))
        except Exception:  # noqa: BLE001
            failure = "audio_send_failed"
    if failure is not None:
        await _clip_failed(failure, key, audit=audit, household=household, run=run, owner_alert=owner_alert)
    return await ch.send_text(to, text, key=IdemKey(f"{idem}:text"))


async def send_dynamic(
    ch: ElderChannel,
    to: PersonRef,
    text: str,
    *,
    tts: TTS,
    codec: AudioCodec,
    voice: VoiceId,
    idem: IdemKey,
    max_seconds: float = 30.0,
) -> SendReceipt:
    """A reply built by code from typed fields: local speech first, and the text always goes (hybrid decision, D-69).

    Any speech, encoding or audio send error leaves the text alone; the text send is the receipt returned.
    """
    try:
        spoken = await tts.synthesize(text, voice=voice, max_seconds=max_seconds)
        clip = await codec.encode_clip(WavBlob(data=spoken.data))
        await ch.send_audio(to, clip, key=IdemKey(f"{idem}:audio"))
    except Exception:  # noqa: BLE001  (the text always goes, whatever the speech or codec does)
        pass
    return await ch.send_text(to, text, key=IdemKey(f"{idem}:text"))


# ---- legacy, removed by WU-15 ----

def emergency_to_elder(caregiver: str) -> str:
    return (
        f"Aapka sandesh mila. {caregiver} ko turant bata diya hai.\n"
        "Agar haalat gambhir hai to abhi 112 (emergency) ya 108 (ambulance) par call karein.\n"
        "Paas mein kisi ko awaaz dein."
    )


def scam_to_elder(caregiver: str) -> str:
    return (
        "⚠️ Dhyaan dein: kisi ko bhi OTP, PIN, CVV ya bank details na batayein, "
        "aur kisi anjaan link par click na karein.\n"
        f"Maine {caregiver} ko bata diya hai. Woh dekh rahe hain."
    )


def ack_to_elder(caregiver: str) -> str:
    return f"Theek hai, aapka sandesh mil gaya 🙏 {caregiver} ko bata diya hai."


def asked_caregiver(caregiver: str) -> str:
    return f"{caregiver} se approve karwa raha hoon, thoda intezaar karein 🙏"
