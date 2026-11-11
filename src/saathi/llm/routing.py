"""Routing floors (RQ-46) and the rules that follow from them. Model labels never change routing."""

from __future__ import annotations

from collections.abc import Mapping

from saathi.domain import Grade, MediaRef
from saathi.llm.roster import MediaClass

# RQ-46: the floor per step. G0 steps are code; a model is only ever a G1 helper there.
ROUTING: Mapping[str, Grade] = {
    "keywords": Grade.G0,
    "asr": Grade.G1,
    "intent": Grade.G1,
    "quote": Grade.G0,
    "macro": Grade.G0,
    "vision": Grade.G2,
    "qa": Grade.G2,
    "debunk_extract": Grade.G2,
    "debunk_verdict": Grade.G4,
}

# How far a step may climb (assurance section 1): intent retries once at G2 and the paid grades are not part of the
# router cascade; ASR climbs to G3 with an audio receipt; vision to G4 with an image receipt; extraction to G3 for
# video.
CEILING: Mapping[str, Grade] = {
    "keywords": Grade.G1,
    "asr": Grade.G3,
    "intent": Grade.G2,
    "quote": Grade.G1,
    "macro": Grade.G0,
    "vision": Grade.G4,
    "qa": Grade.G2,
    "debunk_extract": Grade.G3,
    "debunk_verdict": Grade.G4,
}

CONFIDENCE_FLOOR = 0.7
SCHEMA_ATTEMPTS = 2  # the first try and one retry at the same grade (RQ-49)


def start_grade(step: str) -> Grade:
    """The first grade a model call may use: the floor, but never below G1 (G0 is code)."""
    return max(ROUTING[step], Grade.G1)


def model_allowed(step: str) -> bool:
    """False for a step whose ceiling is G0: it is code only and no model may be called (RQ-53)."""
    return CEILING[step] >= Grade.G1


def ceiling_grade(step: str) -> Grade:
    return max(CEILING[step], start_grade(step))


def media_class_of(ref: MediaRef) -> MediaClass | None:
    """None when the class is unknown: such media has no route and goes to handoff.

    An image maps to IMAGE here; `classes_of` adds SCREENSHOT, because a `MediaRef` has no kind yet."""
    mime = (ref.mime or "").lower()
    if mime.startswith("audio/"):
        return MediaClass.AUDIO
    if mime.startswith("image/"):
        return MediaClass.IMAGE
    if mime.startswith("video/"):
        return MediaClass.VIDEO
    return None


def classes_of(ref: MediaRef) -> frozenset[MediaClass] | None:
    """The receipt classes a media ref needs. Every image may be a screenshot, so it needs both (fail closed)."""
    mc = media_class_of(ref)
    if mc is None:
        return None
    return frozenset({MediaClass.IMAGE, MediaClass.SCREENSHOT}) if mc is MediaClass.IMAGE else frozenset({mc})
