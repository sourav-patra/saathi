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
# A message is read clause by clause, so "no cab is coming, please book an auto" is still a request.
_CLAUSE_BREAK = re.compile(r"[,.;:!?।\n]+|\s(?:please|but|lekin|magar|kripya|कृपया|लेकिन|मगर)\s", re.IGNORECASE)
_NEGATIONS = tuple(
    _padded(f"{negation} {verb}")
    for negation, verbs in (
        ("do not", ("book", "call", "send")),
        ("don't", ("book", "call", "send")),
        ("dont", ("book", "call", "send")),
        ("never", ("book", "call")),
        ("mat", ("bulao", "bulana", "mangao", "book karo", "bhejo", "bhejna", "bhejiye")),
        ("nahi", ("chahiye", "bulao", "bulana", "jaana hai", "jana hai", "bhejo", "bhejna")),
        ("nahin", ("chahiye", "bulao", "bulana", "jaana hai", "jana hai", "bhejo", "bhejna")),
        ("nhi", ("chahiye", "bulao", "bulana", "jaana hai", "jana hai", "bhejo", "bhejna")),
        ("नहीं", ("चाहिए", "चाहिये", "बुलाओ", "जाना है", "भेजो")),
        ("नही", ("चाहिए", "चाहिये", "बुलाओ", "जाना है", "भेजो")),
        ("मत", ("बुलाओ", "बुलाना", "मंगाओ", "बुक करो", "भेजो", "भेजना")),
    )
    for verb in verbs
)
_RIDE_NOUNS = "|".join(re.escape(normalise(w)) for w in RIDE_WORDS if " " not in normalise(w))
_NOT_WORDS = "nahi|nahin|nhi|नहीं|नही"
_NO_WANT = re.compile(rf" (?:do not|don t|dont) (?:need|want) (?:(?:a|an|the|any) )?(?:{_RIDE_NOUNS}) ")
_NO_RIDE_END = re.compile(rf" (?:{_NOT_WORDS}) $")  # "cab to Clinic A nahi"
_NO_RIDE_PREFIX = re.compile(rf" (?:no|नहीं|नही) (?:{_RIDE_NOUNS}) ")  # "no cab is coming"
_CANCEL = re.compile(r" (?:cancel|कैंसल|रद्द) ")
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


def _negated(clause: str) -> bool:
    """A clause whose ride word is negated or cancelled ("cab mat bhejo", "no cab is coming", "cancel the cab")."""
    return (
        any(negation in clause for negation in _NEGATIONS)
        or bool(_NO_WANT.search(clause) or _NO_RIDE_END.search(clause))
        or bool(_NO_RIDE_PREFIX.search(clause) or _CANCEL.search(clause))
    )


def recognise(text: str, places: Sequence[Place]) -> RideResult:
    """`RideResult(ride_word, place_id)`. No ride word in a clause that is not negated: `RideResult(False)`.

    Places are looked for in every clause that is not negated, so the place may sit in a clause of its own."""
    clauses = [c for c in (_padded(part) for part in _CLAUSE_BREAK.split(text)) if c.strip()]
    kept = [c for c in clauses if not (_has_ride_word(c) and _negated(c))]
    if not any(_has_ride_word(c) for c in kept):
        return RideResult(False)
    candidates: list[tuple[int, str, str]] = []  # (length, phrase, place id)
    for place in places:
        for name in (place.label, *place.aliases):
            phrase = normalise(name)
            if usable_name(name):
                candidates.append((len(phrase), phrase, place.id))
    for _length, phrase, _pid in sorted(candidates, key=lambda c: -c[0]):
        if any(f" {phrase} " in clause for clause in kept):
            owners = {pid for _l, ph, pid in candidates if ph == phrase}
            return RideResult(True, next(iter(owners)) if len(owners) == 1 else None)
    return RideResult(True)
