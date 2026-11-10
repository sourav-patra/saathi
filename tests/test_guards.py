import pytest
from fakes import FakeClassifier

from saathi.domain import ClassifierFlag
from saathi.guards import pii, radar
from saathi.ids import RunId


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


# ------------------------------------------------------------------ LT-12: radar combine, phrases only added
RUN = RunId("run-test")

# Frozen at WU-09. Phrases are only added (RQ-16). A phrase may leave the module only with a
# test that names it, by moving it into REMOVED_WITH_TEST below.
PHRASES_V1 = {
    "emergency_roman": {
        "chest pain", "heart attack", "cant breathe", "cannot breathe", "can not breathe",
        "breathing problem", "short of breath", "unconscious", "fainted", "i fell", "fell down",
        "bleeding", "stroke", "ambulance", "emergency", "help me", "accident", "paralysis",
        "paralysed", "paralyzed", "dizzy", "collapsed",
        "seene mein dard", "seene me dard", "sine me dard", "seene main dard",
        "chhati mein dard", "chati me dard", "chhati me dard", "chaati me dard",
        "saans nahi", "sans nahi", "saans nhi", "saans lene", "sans lene", "saas nahi",
        "gir gaya", "gir gayi", "gir gai", "gir gaye", "gir gya", "gir padi", "gir pada",
        "gir pade", "behosh", "khoon nikal", "khoon beh", "bachao", "ambulance bulao",
        "hospital jaldi", "jaldi hospital", "lakwa", "aag lag", "chakkar aa", "chakkar aaya",
        "dil mein dard", "dil me dard", "haath sunn", "muh tedha", "munh tedha",
    },
    "emergency_devanagari": {
        "सीने में दर्द", "सीने मे दर्द", "छाती में दर्द", "छाती मे दर्द", "दिल में दर्द",
        "सांस नहीं", "साँस नहीं", "सांस लेने", "साँस लेने", "गिर गया", "गिर गई", "गिर गयी",
        "गिर गए", "गिर पड़ी", "गिर पड़ा", "बेहोश", "खून निकल", "खून बह", "एम्बुलेंस",
        "एंबुलेंस", "बचाओ", "लकवा", "आग लग", "हार्ट अटैक", "चक्कर आ", "अस्पताल जल्दी",
    },
    "scam_roman": {
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
    },
    "scam_devanagari": {
        "ओटीपी", "केवाईसी", "खाता बंद", "अकाउंट बंद", "सिम बंद", "लॉटरी", "इनाम", "बिजली कट",
        "डिजिटल अरेस्ट", "पिन बताओ", "पिन बताएं", "लिंक पर क्लिक", "रिफंड", "कैशबैक", "गिरफ्तार",
    },
}
# Phrases removed on purpose, each named by a test. Empty: nothing has been removed.
REMOVED_WITH_TEST: set[str] = set()

_MODULE_LISTS = {
    "emergency_roman": "EMERGENCY_ROMAN",
    "emergency_devanagari": "EMERGENCY_DEVANAGARI",
    "scam_roman": "SCAM_ROMAN",
    "scam_devanagari": "SCAM_DEVANAGARI",
}


def _missing(lists: dict[str, tuple[str, ...]]) -> list[str]:
    out = []
    for key, attr in _MODULE_LISTS.items():
        have = set(lists[attr])
        out += [p for p in PHRASES_V1[key] if p not in have and p not in REMOVED_WITH_TEST]
    return sorted(out)


def _module_lists() -> dict[str, tuple[str, ...]]:
    return {attr: getattr(radar, attr) for attr in _MODULE_LISTS.values()}


def test_phrases_only_added():
    assert _missing(_module_lists()) == []


def test_removed_phrase_fails():
    lists = _module_lists()
    lists["EMERGENCY_ROMAN"] = tuple(p for p in lists["EMERGENCY_ROMAN"] if p != "chest pain")
    lists["SCAM_DEVANAGARI"] = tuple(p for p in lists["SCAM_DEVANAGARI"] if p != "ओटीपी")
    assert _missing(lists) == sorted(["chest pain", "ओटीपी"])


def test_rule_ids_stable_and_unique():
    ids = [radar.RULE_IDS[p] for p in radar.RULE_IDS]
    assert len(ids) == len(set(ids))
    assert radar.RULE_IDS["chest pain"] == "RD-EMR-1"
    assert radar.RULE_IDS["otp"] == "RD-SCR-1"
    r = radar.scan("seene mein dard and otp")
    assert r.rule_ids == (radar.RULE_IDS["seene mein dard"], radar.RULE_IDS["otp"])
    assert all(i.startswith("RD-") for i in r.rule_ids)


@pytest.mark.parametrize("text", ["upi collect request aaya", "mujhe ghabrahat ho rahi hai"])
def test_added_phrases_fire(text):
    assert radar.scan(text).triggered


async def test_fake_classifier_adds_flag():
    clf = FakeClassifier()
    clf.add_rule("dhundhla", ClassifierFlag(kind="emergency", score=0.9))
    assert not radar.scan("sab dhundhla dikh raha hai").triggered
    r = await radar.combine("sab dhundhla dikh raha hai", clf, run=RUN)
    assert r.emergency and not r.scam and "clf:emergency" in r.matches


async def test_classifier_cannot_clear_keyword_hit():
    clf = FakeClassifier()  # returns no flags at all
    r = await radar.combine("seene mein dard hai", clf, run=RUN)
    assert r.emergency and r.matches == radar.scan("seene mein dard hai").matches
    clf.add_rule("dard", ClassifierFlag(kind="benign", score=1.0))
    r = await radar.combine("seene mein dard hai", clf, run=RUN)
    assert r.emergency


async def test_classifier_error_keeps_keyword_result():
    clf = FakeClassifier()
    clf.fail("flags", RuntimeError("down"))
    r = await radar.combine("Bank se OTP maang rahe hain", clf, run=RUN)
    assert r == radar.scan("Bank se OTP maang rahe hain")
    assert (await radar.combine("Bank se OTP maang rahe hain", None, run=RUN)) == r


async def test_combine_sees_redacted_text_only():
    clf = FakeClassifier()
    await radar.combine("my pan ABCDE1234F please check", clf, run=RUN)
    assert all("ABCDE1234F" not in str(c) for c in clf.calls_of("flags"))
