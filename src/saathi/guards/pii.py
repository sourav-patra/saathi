"""PII redaction before anything reaches a hosted model or a trace.

A deterministic regex core always runs. It needs no models and is fully
tested. Presidio (MIT) can be layered on top with the ``pii`` extra, with its
Indian recognisers (IN_AADHAAR, IN_PAN, IN_VOTER, IN_PASSPORT) explicitly
enabled, because Presidio ships them disabled.

The elder's real address and phone number for orders come from the profile
store, never from redacted text. Redacting here therefore costs no
functionality.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from saathi.errors import RestrictedDataLeft

__all__ = ["RedactionResult", "RestrictedDataLeft", "assert_clean", "redact"]

# Verhoeff tables, used to validate Aadhaar numbers.
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6), (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4), (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2), (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def luhn_valid(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_AADHAAR_RE = re.compile(r"(?<!\d)([2-9]\d{3})[\s-]?(\d{4})[\s-]?(\d{4})(?!\d)")
_PAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[\s-]?){12,18}\d(?!\d)")
_PHONE_RE = re.compile(r"(?<![\d+])(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
# Checksum-free, word-bounded (LLD M11, change 8). Voter: 3 letters and 7 digits; passport: 1 letter and 7 digits.
VOTER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{3}\d{7}(?![A-Za-z0-9])")
PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\d{7}(?![A-Za-z0-9])")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UPI_RE = re.compile(r"(?<![\w.])[\w.-]{2,}@[A-Za-z]{2,}(?![\w.])")
_OTP_RE = re.compile(
    r"(?i)(?:otp|one[\s-]time[\s-]password|(?<![a-z])code|ओटीपी)\D{0,15}(\d{4,8})(?!\d)"
)


@dataclass
class RedactionResult:
    text: str
    entities: list[str] = field(default_factory=list)

    @property
    def had_pii(self) -> bool:
        return bool(self.entities)


def _fold_digits(text: str) -> str:
    """Map every Unicode decimal digit (Devanagari, fullwidth, ...) to its ASCII digit."""
    if text.isascii():
        return text
    return "".join(
        str(d) if (d := unicodedata.decimal(c, None)) is not None else c for c in text
    )


def redact(text: str | None) -> RedactionResult:
    if not text:
        return RedactionResult(text or "")
    text = _fold_digits(text)
    found: list[str] = []
    out = text

    def _sub_otp(m: re.Match[str]) -> str:
        found.append("OTP")
        return m.group(0).replace(m.group(1), "<OTP>")

    out = _OTP_RE.sub(_sub_otp, out)

    def _sub_aadhaar(m: re.Match[str]) -> str:
        digits = "".join(m.groups())
        if verhoeff_valid(digits):
            found.append("IN_AADHAAR")
            return "<AADHAAR>"
        return m.group(0)

    out = _AADHAAR_RE.sub(_sub_aadhaar, out)

    def _sub_card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            found.append("CARD_NUMBER")
            return "<CARD>"
        return m.group(0)

    out = _CARD_RE.sub(_sub_card, out)

    for pattern, label, token in (
        (_PAN_RE, "IN_PAN", "<PAN>"),
        (VOTER_RE, "IN_VOTER", "<VOTER_ID>"),
        (PASSPORT_RE, "IN_PASSPORT", "<PASSPORT>"),
        (_EMAIL_RE, "EMAIL", "<EMAIL>"),
        (_UPI_RE, "UPI_ID", "<UPI_ID>"),
        (_PHONE_RE, "PHONE", "<PHONE>"),
    ):
        out, n = pattern.subn(token, out)
        found.extend([label] * n)

    return RedactionResult(out, found)


def assert_clean(text: str | None) -> None:
    """Raise `RestrictedDataLeft` when a restricted pattern remains in `text`.

    Called by the gateway and the trace exporter. Text only: anything that is not text
    (bytes, a media blob) is never claimed clean and raises.
    """
    if text is None:
        return
    if not isinstance(text, str):
        raise RestrictedDataLeft("media is not checked and is never claimed clean")
    entities = redact(text).entities
    if entities:
        raise RestrictedDataLeft(f"restricted pattern left: {sorted(set(entities))}")
