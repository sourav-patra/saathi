import pytest

from saathi.guards import pii, radar


# ------------------------------------------------------------------ radar
@pytest.mark.parametrize(
    "text",
    [
        "Beta seene mein dard ho raha hai",
        "मुझे सीने में दर्द है",
        "Main bathroom mein gir gayi",
        "saans nahi aa rahi",
        "Please call ambulance",
        "chakkar aa raha hai bahut",
        "HELP ME!!",
        "सांस नहीं आ रही",
    ],
)
def test_emergency_detected(text):
    assert radar.scan(text).emergency


@pytest.mark.parametrize(
    "text",
    [
        "Bank se call aaya, OTP maang rahe hain",
        "Your KYC is pending, account will be blocked",
        "Aapne lottery jeeta hai 25 lakh",
        "बिजली कट जाएगी आज रात",
        "Urgent: verify now https://bit.ly/xyz",
        "Install AnyDesk for refund",
        "digital arrest ho jayega",
    ],
)
def test_scam_detected(text):
    assert radar.scan(text).scam


@pytest.mark.parametrize(
    "text",
    [
        "Mujhe kal temple jaana hai auto se",
        "Doodh aur atta mangwa do",
        "Aaj khana bahut accha bana",
        "Ye video sach hai kya?",
        "",
        None,
    ],
)
def test_benign_not_flagged(text):
    assert not radar.scan(text).triggered


def test_word_boundaries_avoid_substring_false_positives():
    # "cbi" must not fire inside an unrelated word, nor "otp" inside "hotpot"
    assert not radar.scan("hotpot recipe").scam


# ------------------------------------------------------------------ PII
def test_valid_aadhaar_redacted():
    # 2341 2341 2346 is Verhoeff-valid test data, not a real person's number
    r = pii.redact("mera aadhaar 2341 2341 2346 hai")
    assert "<AADHAAR>" in r.text and "IN_AADHAAR" in r.entities


def test_checksum_invalid_12_digits_not_labelled_aadhaar():
    r = pii.redact("order id 234123412347")
    assert "IN_AADHAAR" not in r.entities


def test_pan_phone_email_upi():
    r = pii.redact("PAN ABCDE1234F, call +91 98765 43210, mail a.b@example.com, upi ram@okaxis")
    assert set(r.entities) >= {"IN_PAN", "PHONE", "EMAIL", "UPI_ID"}
    for raw in ("ABCDE1234F", "98765", "a.b@example.com", "ram@okaxis"):
        assert raw not in r.text


def test_otp_redacted():
    r = pii.redact("Your OTP is 482913. Do not share")
    assert "482913" not in r.text and "OTP" in r.entities


def test_card_luhn():
    r = pii.redact("card 4111 1111 1111 1111")
    assert "<CARD>" in r.text


def test_plain_text_untouched():
    r = pii.redact("Kal subah 10 baje doctor ke paas jaana hai")
    assert not r.had_pii and r.text.startswith("Kal subah")
