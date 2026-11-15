"""Spot a ride request in an elder's words and the place it names. Pure: no I/O, no model.

The words are first normalised (Unicode composed, case folded, punctuation dropped; letters, marks and digits are kept,
so Devanagari and Latin both survive). A ride word from `RIDE_WORDS` must be present, else the text is not a ride.
Then every place label and alias is tried as a whole word or phrase, longest first. A tie between two places on the
same longest phrase names no place, so the caregiver chooses. An explicitly named pickup can be the destination.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from saathi.rides.place import Place

# English, Hinglish (Latin spelling) and Hindi (Devanagari). Whole words or phrases, matched after `normalise`.
RIDE_WORDS: tuple[str, ...] = (
    "cab", "taxi", "auto", "rickshaw", "ride", "uber", "ola", "rapido", "namma yatri",
    "gaadi", "gadi", "gaari", "jaana hai", "jana hai", "jaana he", "le chalo", "le chaliye", "chhod do", "chod do",
    "गाड़ी", "गाडी", "ऑटो", "कैब", "टैक्सी", "रिक्शा", "ऑटोरिक्शा", "जाना है", "ले चलो", "ले चलिए", "छोड़ दो",
)  # fmt: skip


@dataclass(frozen=True)
class RideResult:
    ride_word: bool  # a ride word was found
    place_id: str | None = None  # the one place named, or None


def normalise(text: str) -> str:
    """Composed, case folded; letters, marks and digits kept, everything else a space; one space between words."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", text).casefold():
        cat = unicodedata.category(ch)
        if cat[0] in "LMN":
            out.append(ch)
        elif cat == "Cf":  # a joiner inside a word must not split it
            continue
        else:
            out.append(" ")
    return " ".join(unicodedata.normalize("NFC", "".join(out)).split())


def _padded(text: str) -> str:
    return f" {normalise(text)} "


_RIDE_PHRASES = tuple(_padded(w) for w in RIDE_WORDS)
# Refusals fail closed: a ride word plus any refusal marker anywhere gives no card (the caregiver still reads the
# summary). Only three narrow exceptions remove their own words before the markers are looked for: unavailability
# ("there is no auto", "cab nahi mil rahi"), a rejected place when another place is named, and a few fixed phrases.
_RIDE_NOUNS = "|".join(re.escape(normalise(w)) for w in RIDE_WORDS if " " not in normalise(w))
_MARKERS = re.compile(
    r" (?:do not|don t|dont|does not|doesn t|doesnt|did not|didn t|didnt|never|won t|wont|will not|cannot|can t|cant"
    r"|shouldn t|shouldnt|should not|wouldn t|wouldnt|would not|isn t|isnt|cancel|cancelled|canceled|no need"
    r"|no longer|no more|not|mat|मत|nahi|nahin|nahii|nhi|nehi|na|न|नहीं|नही|नहि|mana|मना|rehne do|रहने दो"
    r"|कैंसल|कैंसिल|रद्द|radd|nai|nahee|skip|skipped|stop|stopped|hold off|forget|call off|called off|against"
    r"|unnecessary|no thanks|no thank you|pass on|refuse|refused|rehne de|rehne dena|rehne dijiye"
    r"|रहने दे|रहने देना|रहने दीजिए)(?= )"
    rf"| no (?:{_RIDE_NOUNS})(?= )"
)
_NOT = "nahi|nahin|nhi|नहीं|नही"
# Fixed phrases that are not about the ride, forgiven only when punctuation ends them in the raw text
_ALLOWED_RAW = re.compile(r"(?:\b(?:der|deri) mat karo|देर मत करो|\bnot (?:feeling )?well)\s*[,.;!?।]", re.IGNORECASE)
_UNAVAILABLE = re.compile(
    rf" there (?:is|are) no (?:{_RIDE_NOUNS})(?= )"
    rf"| no (?:{_RIDE_NOUNS}) (?:is|are|has) (?:coming|available|here|arrived|showing)(?= )"
    rf"| (?:{_RIDE_NOUNS}) (?:nahi|नहीं) (?:mil rahi|mil raha|mil rhi|mila|mili|aa rahi|aa raha|aayi|aai|aaya"
    r"|मिल रही|मिल रहा|मिली|मिला|आ रही|आ रहा|आई|आया)(?= )"
)
_COMMON_NAMES = frozenset(
    {
        "the",
        "and",
        "for",
        "get",
        "book",
        "cab",
        "taxi",
        "auto",
        "ride",
        "hai",
        "chahiye",
        "और",
        "है",
        "पर",
        "कब",
        "सब",
        "अब",
        "तक",
        "जब",
        "हम",
    }
)


def usable_name(name: str) -> bool:
    """Reject short or common-word place names both at entry and when reading older saved aliases."""
    phrase = normalise(name)
    letters: dict[str, int] = {}
    for ch in phrase:
        if unicodedata.category(ch).startswith("L"):
            script = unicodedata.name(ch, "").split(" ")[0]
            letters[script] = letters.get(script, 0) + 1
    return phrase not in _COMMON_NAMES and any(
        count >= (2 if script == "DEVANAGARI" else 3) for script, count in letters.items()
    )


def _has_ride_word(padded: str) -> bool:
    return any(p in padded for p in _RIDE_PHRASES)


def _rejected(padded: str, phrase: str) -> bool:
    return bool(re.search(_rejection(phrase), padded))


def _rejection(phrase: str) -> str:
    # "Clinic A nahi chahiye" refuses the ride; only a bare "Clinic A nahi" rejects the place
    refusal = "chahiye|chahie|चाहिए|चाहिये|bulao|bhejo|बुलाओ|भेजो"
    return rf" {re.escape(phrase)} (?:{_NOT})(?! (?:{refusal}) )(?= )| not {re.escape(phrase)}(?= )"


def recognise(text: str, places: Sequence[Place]) -> RideResult:
    """`RideResult(ride_word, place_id)`. No ride word, or any refusal marker left after the exceptions:
    `RideResult(False)`, whatever the places. A rejected place ("Clinic A nahi", "not Clinic A") is never the
    destination, and its rejection is forgiven only when the message also names a different place it does not reject."""
    padded = _padded(text)
    if not _has_ride_word(padded):
        return RideResult(False)
    names = [(place.id, normalise(n)) for place in places for n in (place.label, *place.aliases) if usable_name(n)]
    rejected_ids = {pid for pid, phrase in names if _rejected(padded, phrase)}
    candidates = [(len(phrase), phrase, pid) for pid, phrase in names if pid not in rejected_ids]
    named = [c for c in candidates if f" {c[1]} " in padded]
    rest = _UNAVAILABLE.sub(" ", _padded(_ALLOWED_RAW.sub(" , ", text)))
    if named:
        for pid, phrase in names:
            if pid in rejected_ids:
                rest = re.sub(_rejection(phrase), " ", rest)
    if _MARKERS.search(rest):
        return RideResult(False)
    for _length, phrase, _pid in sorted(named, key=lambda c: -c[0]):
        owners = {pid for _l, ph, pid in candidates if ph == phrase}
        return RideResult(True, next(iter(owners)) if len(owners) == 1 else None)
    return RideResult(True)
