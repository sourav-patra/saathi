"""LT-13: deterministic redaction (M11; RQ-41, PL-03). Synthetic canaries only."""

from __future__ import annotations

import random
import string

import pytest

from saathi.guards import pii
from saathi.guards.pii import RestrictedDataLeft, assert_clean, luhn_valid, redact, verhoeff_valid

_D_DIGITS = "0123456789"


def _verhoeff_check_digit(payload: str) -> str:
    for d in _D_DIGITS:
        if verhoeff_valid(payload + d):
            return d
    raise AssertionError("unreachable")


def _luhn_check_digit(payload: str) -> str:
    for d in _D_DIGITS:
        if luhn_valid(payload + d):
            return d
    raise AssertionError("unreachable")


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_D_DIGITS) for _ in range(n))


def _letters(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(n))


def _aadhaar(rng: random.Random) -> str:
    payload = rng.choice("23456789") + _digits(rng, 10)
    return payload + _verhoeff_check_digit(payload)


def _pan(rng: random.Random) -> str:
    return _letters(rng, 5) + _digits(rng, 4) + _letters(rng, 1)


def _voter(rng: random.Random) -> str:
    return _letters(rng, 3) + _digits(rng, 7)


def _passport(rng: random.Random) -> str:
    return _letters(rng, 1) + _digits(rng, 7)


def _phone(rng: random.Random) -> str:
    return rng.choice("6789") + _digits(rng, 9)


def _card(rng: random.Random) -> str:
    payload = "4" + _digits(rng, 14)
    return payload + _luhn_check_digit(payload)


def _otp(rng: random.Random) -> str:
    return _digits(rng, rng.choice((4, 6)))


# type -> (generator, sentence template, entity label, raw-value extractor)
CASES = {
    "aadhaar": (_aadhaar, "mera aadhaar {v} hai", "IN_AADHAAR"),
    "pan": (_pan, "PAN {v} bhej diya", "IN_PAN"),
    "voter": (_voter, "voter id {v} dekho", "IN_VOTER"),
    "passport": (_passport, "passport number {v} hai", "IN_PASSPORT"),
    "phone": (_phone, "call me on {v} later", "PHONE"),
    "card": (_card, "card number {v} hai", "CARD_NUMBER"),
    "otp": (_otp, "Your OTP is {v}. Do not share", "OTP"),
}


@pytest.mark.parametrize("kind", list(CASES))
def test_each_id_type_redacted(kind):
    gen, template, label = CASES[kind]
    value = gen(random.Random(f"each-{kind}"))
    r = redact(template.format(v=value))
    assert label in r.entities
    assert value not in r.text


@pytest.mark.parametrize("kind", list(CASES))
def test_30_canaries_per_type(kind):
    gen, template, label = CASES[kind]
    rng = random.Random(f"canary-{kind}")
    values = {gen(rng) for _ in range(60)}
    assert len(values) >= 30
    for value in sorted(values)[:30]:
        r = redact(template.format(v=value))
        assert value not in r.text, (kind, value)
        assert label in r.entities, (kind, value)
        assert_clean(r.text)  # nothing restricted survives redaction


def test_voter_and_passport_are_word_bounded():
    assert "IN_VOTER" not in redact("order ABCD1234567 shipped").entities
    assert "IN_PASSPORT" not in redact("ref AB12345678 ok").entities
    assert "IN_VOTER" in redact("EPIC: XYZ1234567.").entities
    assert "IN_PASSPORT" in redact("passport (K1234567)").entities


def test_pan_not_taken_for_voter_or_passport():
    r = redact("ABCDE1234F")
    assert r.entities == ["IN_PAN"]


def test_redaction_is_deterministic_and_idempotent():
    text = "aadhaar 2341 2341 2346, PAN ABCDE1234F, voter XYZ1234567, call +91 98765 43210"
    first = redact(text)
    assert redact(text) == first
    assert redact(first.text).text == first.text
    assert not redact(first.text).had_pii


def test_assert_clean_raises():
    with pytest.raises(RestrictedDataLeft):
        assert_clean("mera pan ABCDE1234F hai")
    with pytest.raises(RestrictedDataLeft):
        assert_clean("passport K1234567")
    assert_clean("Kal subah 10 baje doctor ke paas jaana hai")
    assert_clean("")
    assert_clean(None)
    assert_clean(redact("pan ABCDE1234F otp 123456").text)


def test_media_not_claimed_clean():
    # Text only: media is never claimed clean (RQ-41, RQ-96).
    for media in (b"\x89PNG\r\n", bytearray(b"ogg"), memoryview(b"x")):
        with pytest.raises(RestrictedDataLeft):
            assert_clean(media)  # type: ignore[arg-type]
    assert "media" in pii.assert_clean.__doc__
