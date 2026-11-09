"""T0 emergency and scam radar: deterministic, runs before any model.

This is a *high-recall* keyword layer. In the pipeline it is OR-ed with a T1
model check, so either one flagging is enough. A false alarm costs the caregiver a
glance; a miss could cost far more.

Lexicons cover Hindi (Devanagari), romanised Hindi/Hinglish and English.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

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
)
EMERGENCY_DEVANAGARI: tuple[str, ...] = (
    "सीने में दर्द", "सीने मे दर्द", "छाती में दर्द", "छाती मे दर्द", "दिल में दर्द",
    "सांस नहीं", "साँस नहीं", "सांस लेने", "साँस लेने", "गिर गया", "गिर गई", "गिर गयी",
    "गिर गए", "गिर पड़ी", "गिर पड़ा", "बेहोश", "खून निकल", "खून बह", "एम्बुलेंस",
    "एंबुलेंस", "बचाओ", "लकवा", "आग लग", "हार्ट अटैक", "चक्कर आ", "अस्पताल जल्दी",
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
)
SCAM_DEVANAGARI: tuple[str, ...] = (
    "ओटीपी", "केवाईसी", "खाता बंद", "अकाउंट बंद", "सिम बंद", "लॉटरी", "इनाम", "बिजली कट",
    "डिजिटल अरेस्ट", "पिन बताओ", "पिन बताएं", "लिंक पर क्लिक", "रिफंड", "कैशबैक", "गिरफ्तार",
)

_URL_RE = re.compile(r"(https?://|www\.|bit\.ly|tinyurl|wa\.me/)", re.IGNORECASE)
_URGENCY_ROMAN = ("urgent", "immediately", "turant", "abhi", "24 hours", "today only", "jaldi")


def _normalise_roman(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("'", "").replace("’", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")


_EMERGENCY_RE = _build_pattern(EMERGENCY_ROMAN)
_SCAM_RE = _build_pattern(SCAM_ROMAN)
_URGENCY_RE = _build_pattern(_URGENCY_ROMAN)


@dataclass
class RadarResult:
    emergency: bool = False
    scam: bool = False
    matches: list[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return self.emergency or self.scam


def scan(text: str | None) -> RadarResult:
    """Scan a transcript / caption / OCR text. Never raises."""
    result = RadarResult()
    if not text:
        return result

    nfkc = unicodedata.normalize("NFKC", text)
    roman = _normalise_roman(text)

    em = {m.group(0) for m in _EMERGENCY_RE.finditer(roman)}
    em |= {t for t in EMERGENCY_DEVANAGARI if t in nfkc}
    sc = {m.group(0) for m in _SCAM_RE.finditer(roman)}
    sc |= {t for t in SCAM_DEVANAGARI if t in nfkc}

    # A link plus urgency language is a classic phishing shape.
    if _URL_RE.search(text) and _URGENCY_RE.search(roman):
        sc.add("link+urgency")

    result.emergency = bool(em)
    result.scam = bool(sc)
    result.matches = sorted(em) + sorted(sc)
    return result
