"""Spot a ride request in an elder's words and the place it names. Pure: no I/O, no model.

The words are first normalised (Unicode composed, case folded, punctuation dropped; letters, marks and digits are kept,
so Devanagari and Latin both survive). A ride word from `RIDE_WORDS` must be present, else the text is not a ride.
Then every place label and alias is tried as a whole word or phrase, longest first. A tie between two places on the
same longest phrase names no place, so the caregiver chooses. The pickup place is never a drop.
"""

from __future__ import annotations

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


def _has_ride_word(padded: str) -> bool:
    return any(p in padded for p in _RIDE_PHRASES)


def recognise(text: str, places: Sequence[Place]) -> RideResult:
    """`RideResult(ride_word, place_id)`. No ride word: `RideResult(False)`, whatever the places."""
    padded = _padded(text)
    if not _has_ride_word(padded):
        return RideResult(False)
    candidates: list[tuple[int, str, str]] = []  # (length, phrase, place id)
    for place in places:
        if place.is_pickup:
            continue
        for name in (place.label, *place.aliases):
            phrase = normalise(name)
            if phrase:
                candidates.append((len(phrase), phrase, place.id))
    for _length, phrase, _pid in sorted(candidates, key=lambda c: -c[0]):
        if f" {phrase} " in padded:
            owners = {pid for _l, ph, pid in candidates if ph == phrase}
            return RideResult(True, next(iter(owners)) if len(owners) == 1 else None)
    return RideResult(True)
