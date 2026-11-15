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
    r" (?:do not|don t|dont|does not|doesn t|did not|didn t|never|won t|wont|will not|cancel|cancelled|canceled"
    r"|no need|no longer|no more|not|mat|मत|nahi|nahin|nhi|नहीं|नही|rehne do|रहने दो|कैंसल|रद्द)(?= )"
    rf"| no (?:{_RIDE_NOUNS})(?= )"
)
_NOT = "nahi|nahin|nhi|नहीं|नही"
_ALLOWED = re.compile(r" (?:der mat karo|deri mat karo|देर मत करो|not feeling|not well)(?= )")
_UNAVAILABLE = re.compile(
    rf" there (?:is|are) no (?:{_RIDE_NOUNS})(?= )"
    rf"| no (?:{_RIDE_NOUNS}) (?:is|are|has) (?:coming|available|here|arrived|showing)(?= )"
    rf"| (?:{_RIDE_NOUNS}) (?:nahi|नहीं) (?=(?:mil|aa|मिल|आ)\S* )"
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
    return rf" {re.escape(phrase)} (?:{_NOT})(?= )| not {re.escape(phrase)}(?= )"


def recognise(text: str, places: Sequence[Place]) -> RideResult:
    """`RideResult(ride_word, place_id)`. No ride word, or any refusal marker left after the exceptions:
    `RideResult(False)`, whatever the places. A rejected place ("Clinic A nahi", "not Clinic A") is never the
    destination, and its rejection is forgiven only when the message also names a place it does not reject."""
    padded = _padded(text)
    if not _has_ride_word(padded):
        return RideResult(False)
    candidates: list[tuple[int, str, str]] = []  # (length, phrase, place id)
    rejected: list[str] = []
    for place in places:
        for name in (place.label, *place.aliases):
            phrase = normalise(name)
            if not usable_name(name):
                continue
            if _rejected(padded, phrase):
                rejected.append(phrase)
            else:
                candidates.append((len(phrase), phrase, place.id))
    named = [c for c in candidates if f" {c[1]} " in padded]
    rest = _UNAVAILABLE.sub(" ", _ALLOWED.sub(" ", padded))
    if named:
        for phrase in rejected:
            rest = re.sub(_rejection(phrase), " ", rest)
    if _MARKERS.search(rest):
        return RideResult(False)
    for _length, phrase, _pid in sorted(named, key=lambda c: -c[0]):
        owners = {pid for _l, ph, pid in candidates if ph == phrase}
        return RideResult(True, next(iter(owners)) if len(owners) == 1 else None)
    return RideResult(True)
