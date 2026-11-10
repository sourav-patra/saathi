"""T0 emergency and scam radar: deterministic, runs before any model.

This is a *high-recall* keyword layer. In the pipeline it is OR-ed with a T1
model check, so either one flagging is enough. A false alarm costs the caregiver a
glance; a miss could cost far more.

Lexicons cover Hindi (Devanagari), romanised Hindi/Hinglish and English.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass

from saathi.domain import ClassifierFlag
from saathi.guards.pii import redact
from saathi.ids import RunId
from saathi.ports import Classifier

# --------------------------------------------------------------------------- #
# Lexicons. Romanised entries are matched on word boundaries after
# normalisation; Devanagari entries are matched as substrings.
# --------------------------------------------------------------------------- #
EMERGENCY_ROMAN: tuple[str, ...] = (
    # English
    "chest pain", "heart attack", "cant breathe", "cannot breathe", "can not breathe",
    "breathing problem", "short of breath", "unconscious", "fainted", "i fell", "fell down",
    "bleeding", "stroke", "ambulance", "emergency", "help me", "accident", "paralysis",
    "paralysed", "paralyzed", "dizzy", "collapsed",
    # Hinglish
    "seene mein dard", "seene me dard", "sine me dard", "seene main dard",
    "chhati mein dard", "chati me dard", "chhati me dard", "chaati me dard",
    "saans nahi", "sans nahi", "saans nhi", "saans lene", "sans lene", "saas nahi",
    "gir gaya", "gir gayi", "gir gai", "gir gaye", "gir gya", "gir padi", "gir pada",
    "gir pade", "behosh", "khoon nikal", "khoon beh", "bachao", "ambulance bulao",
    "hospital jaldi", "jaldi hospital", "lakwa", "aag lag", "chakkar aa", "chakkar aaya",
    "dil mein dard", "dil me dard", "haath sunn", "muh tedha", "munh tedha",
    # added in WU-09 (append only)
    "ghabrahat", "dil ghabra", "sugar low", "bp high", "bahut dard", "uth nahi pa",
    "call ambulance", "need a doctor", "not breathing",
    # added in the WU-09 fix pass (append only)
    "sine mein dard", "chhaati mein dard", "chest me pain", "gir gyi", "fisal gayi",
    "fisal ke gir", "saans nahin", "saans nai", "sans nhi", "saans phool", "breathe nahi",
    "cant breath", "difficulty breathing", "unable to breathe", "not able to breathe",
    "ambulence", "embulance", "doctor bulao", "hospital le chalo", "bp bahut high",
    "sugar bahut low", "haart attack", "attack aaya", "hosh nahi", "bol nahi pa",
    "haath pair sunn", "i have fallen", "fell in", "help", "bachaao",
    # added in WU-23 (append only)
    "seene mein bahut dard", "sine mein bahut dard",
)
EMERGENCY_DEVANAGARI: tuple[str, ...] = (
    "सीने में दर्द", "सीने मे दर्द", "छाती में दर्द", "छाती मे दर्द", "दिल में दर्द",
    "सांस नहीं", "साँस नहीं", "सांस लेने", "साँस लेने", "गिर गया", "गिर गई", "गिर गयी",
    "गिर गए", "गिर पड़ी", "गिर पड़ा", "बेहोश", "खून निकल", "खून बह", "एम्बुलेंस",
    "एंबुलेंस", "बचाओ", "लकवा", "आग लग", "हार्ट अटैक", "चक्कर आ", "अस्पताल जल्दी",
    "घबराहट", "दिल घबरा",
    # added in the WU-09 fix pass (append only)
    "सांस नही", "सांस फूल", "ऐम्बुलेंस", "एंबुलैंस", "दिल का दौरा", "मदद करो",
    "डॉक्टर बुलाओ", "अस्पताल ले चलो",
    # added in WU-23 after the synthetic radar fixture showed two misses (append only)
    "सीने में बहुत दर्द", "सुन्न हो",
)

SCAM_ROMAN: tuple[str, ...] = (
    "otp", "kyc", "upi pin", "atm pin", "pin batao", "pin bata", "cvv",
    "account block", "account blocked", "account band", "account suspend",
    "account will be", "khata band", "sim block", "sim band", "pan link", "aadhaar link",
    "aadhar link", "lottery", "lucky draw", "prize", "inaam", "jeeta", "you have won",
    "you won", "refund", "cashback", "bijli kat", "electricity disconnect",
    "power disconnect", "customs", "parcel held", "digital arrest", "cbi", "police case",
    "arrest warrant", "anydesk", "teamviewer", "quicksupport", "screen share",
    "click the link", "click this link", "link pe click", "link par click", "apk",
    "kbc", "free recharge", "work from home earn", "task earn", "investment double",
    "paisa double", "credit card points", "reward points", "collect request",
    # added in WU-09 (append only)
    "upi collect", "payment request", "kyc update", "kyc expired", "verify your account",
    "share your otp", "otp share", "remote access",
    # added in the WU-09 fix pass (append only)
    "o t p", "code batao", "verification code", "digit code", "k y c", "ekyc",
    "lotery", "lottary", "lakh jeete", "bank se call", "bank se phone", "bank manager",
    "bank se bol", "se bol raha hun", "pin daalo", "pin dalo", "paise milenge",
    "paise receive", "scan karo", "qr scan", "request accept", "video call pe raho",
    "ed officer", "narcotics", "money laundering", "aadhaar misuse", "aadhaar band",
    "remote app", "will be disconnected",
)
SCAM_DEVANAGARI: tuple[str, ...] = (
    "ओटीपी", "केवाईसी", "खाता बंद", "अकाउंट बंद", "सिम बंद", "लॉटरी", "इनाम", "बिजली कट",
    "डिजिटल अरेस्ट", "पिन बताओ", "पिन बताएं", "लिंक पर क्लिक", "रिफंड", "कैशबैक", "गिरफ्तार",
    "ओटीपी बताओ", "यूपीआई पिन",
    # added in the WU-09 fix pass (append only)
    "के वाई सी", "बैंक से कॉल", "बैंक से फोन", "पिन डालो", "सीबीआई", "पुलिस केस", "ओ टी पी",
)

# A classifier call may not stall the alert path (M10: never wait for a model). The LLD sets no value.
CLASSIFIER_TIMEOUT_S = 2.0
_NUKTA = "\u093c"
_ZERO_WIDTH_RE = re.compile("[\u200c\u200d]")

_URL_RE = re.compile(r"(https?://|www\.|bit\.ly|tinyurl|wa\.me/)", re.IGNORECASE)
_URGENCY_ROMAN = ("urgent", "immediately", "turant", "abhi", "24 hours", "today only", "jaldi")


def _normalise_roman(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("'", "").replace("’", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _roman_variants(text: str) -> list[str]:
    """The existing normalisation first, then the same with ZWNJ/ZWJ removed (additive)."""
    base = _normalise_roman(text)
    stripped = _normalise_roman(_ZERO_WIDTH_RE.sub("", text))
    return [base] if stripped == base else [base, stripped]


def _devanagari_variants(text: str) -> tuple[str, str, str]:
    """NFKC text, then ZW-stripped with whitespace collapsed, then that with nukta removed."""
    nfkc = unicodedata.normalize("NFKC", text)
    clean = re.sub(r"\s+", " ", _ZERO_WIDTH_RE.sub("", nfkc))
    return nfkc, clean, clean.replace(_NUKTA, "")


def _devanagari_hits(terms: tuple[str, ...], variants: tuple[str, str, str]) -> set[str]:
    nfkc, clean, bare = variants
    return {
        t for t in terms
        if t in nfkc or t in clean or t.replace(_NUKTA, "") in bare
    }


def _build_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")


_EMERGENCY_RE = _build_pattern(EMERGENCY_ROMAN)
_SCAM_RE = _build_pattern(SCAM_ROMAN)
_URGENCY_RE = _build_pattern(_URGENCY_ROMAN)


_LINK_URGENCY = "link+urgency"


def _ids(kind: str, terms: tuple[str, ...]) -> dict[str, str]:
    """Stable rule id per phrase: `RD-<kind>-<n>`, n is the 1-based position. Lists only grow."""
    return {t: f"RD-{kind}-{i}" for i, t in enumerate(terms, start=1)}


RULE_IDS: dict[str, str] = {
    **_ids("EMR", EMERGENCY_ROMAN),
    **_ids("EMD", EMERGENCY_DEVANAGARI),
    **_ids("SCR", SCAM_ROMAN),
    **_ids("SCD", SCAM_DEVANAGARI),
    _LINK_URGENCY: "RD-LNK-1",
}


@dataclass(frozen=True)
class RadarResult:
    emergency: bool = False
    scam: bool = False
    matches: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()

    @property
    def triggered(self) -> bool:
        return self.emergency or self.scam


def scan(text: str | None) -> RadarResult:
    """Scan a transcript / caption / OCR text. Never raises."""
    if not text:
        return RadarResult()
    try:
        romans = _roman_variants(text)
        dev = _devanagari_variants(text)

        em: set[str] = set()
        sc: set[str] = set()
        for roman in romans:
            em |= {m.group(0) for m in _EMERGENCY_RE.finditer(roman)}
            sc |= {m.group(0) for m in _SCAM_RE.finditer(roman)}
        em |= _devanagari_hits(EMERGENCY_DEVANAGARI, dev)
        sc |= _devanagari_hits(SCAM_DEVANAGARI, dev)

        # A link plus urgency language is a classic phishing shape.
        if _URL_RE.search(text) and any(_URGENCY_RE.search(r) for r in romans):
            sc.add(_LINK_URGENCY)
    except Exception:  # noqa: BLE001  (never raises; fail towards the humans)
        return RadarResult(emergency=True, matches=("radar_error",), rule_ids=("RD-ERR-1",))

    hits = sorted(em) + sorted(sc)
    return RadarResult(
        emergency=bool(em),
        scam=bool(sc),
        matches=tuple(hits),
        rule_ids=tuple(RULE_IDS[h] for h in hits if h in RULE_IDS),
    )


async def combine(text: str | None, clf: Classifier | None, *, run: RunId) -> RadarResult:
    """Keyword OR classifier. A model can add a flag and can never clear one.

    A classifier error or absence returns the keyword result. The classifier is local (G1) and
    sees redacted text only.
    """
    kw = scan(text)
    if clf is None or not text:
        return kw
    try:
        async with asyncio.timeout(CLASSIFIER_TIMEOUT_S):
            flags = await clf.flags(redact(text).text, run=run)
            kinds = {
                f.kind.strip().lower()
                for f in flags
                if isinstance(f, ClassifierFlag) and isinstance(f.kind, str)
            }
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001  (a classifier fault or timeout never hides the keyword result)
        return kw
    em, sc = kw.emergency, kw.scam
    matches, ids = list(kw.matches), list(kw.rule_ids)
    for kind, rule_id in (("emergency", "RD-CLF-1"), ("scam", "RD-CLF-2")):
        if kind not in kinds:
            continue
        em = em or kind == "emergency"
        sc = sc or kind == "scam"
        matches.append(f"clf:{kind}")
        ids.append(rule_id)
    return RadarResult(emergency=em, scam=sc, matches=tuple(matches), rule_ids=tuple(ids))
