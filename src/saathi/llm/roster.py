"""The model roster (M19, RQ-45, RQ-50, RQ-96, PL-20).

Tiers are configured by capability grade. The roster names tiers (T1 to T4, T2_FREE) and grades only. Concrete
model ids, endpoints and key variable names come from a private TOML file at run time; the file in the repository
(`infra/roster.example.toml`) is a schema with synthetic ids.

An entry carries a receipt: licence, training and retention terms, the media classes it may receive, the date and
the reader's role. An entry without a receipt receives no personal data. Tier F (T2_FREE) never receives personal
data and never has a receipt.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from saathi.domain import Grade, Sensitivity
from saathi.errors import PrivacyViolation, ReceiptMissing


class Tier(StrEnum):
    T1 = "T1"  # local model: private
    T2 = "T2"  # hosted open-weight, receipt needed for personal data
    T2_FREE = "T2_FREE"  # tier F: public payloads only
    T3 = "T3"  # paid multimodal
    T4 = "T4"  # paid frontier reasoning


class MediaClass(StrEnum):
    """RQ-96: what a request carries. Redaction cleans text only; for the others the receipt is the control."""

    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    SCREENSHOT = "screenshot"
    VIDEO = "video"


GRADE_TIER: dict[Grade, Tier] = {Grade.G1: Tier.T1, Grade.G2: Tier.T2, Grade.G3: Tier.T3, Grade.G4: Tier.T4}
PAID_TIERS: frozenset[Tier] = frozenset({Tier.T3, Tier.T4})
HOSTED_TIERS: frozenset[Tier] = frozenset({Tier.T2, Tier.T2_FREE, Tier.T3, Tier.T4})


class Receipt(BaseModel):
    """PL-20: what the provider's terms say, which classes they cover, when and who read them (role only)."""

    model_config = ConfigDict(frozen=True)
    licence: str = Field(min_length=1)
    training: str = Field(min_length=1)
    retention: str = Field(min_length=1)
    classes: frozenset[MediaClass]
    read_on: date
    read_by: str = Field(min_length=1)


class TierSpec(BaseModel):
    """One endpoint. `grade` is None for tier F. The key is named by `key_env` and never held in the roster."""

    model_config = ConfigDict(frozen=True)
    entry_id: str
    tier: Tier
    grade: Grade | None
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    key_env: str = ""
    usd_per_m_input: Decimal | None = None
    usd_per_m_output: Decimal | None = None

    @model_validator(mode="after")
    def _grade_matches_tier(self) -> TierSpec:
        """The grade decides the cap and the checks, so it must be the grade of the tier (None only for tier F)."""
        if self.tier is Tier.T2_FREE:
            if self.grade is not None:
                raise ValueError("tier F has no grade")
        elif self.grade is None or GRADE_TIER.get(self.grade) is not self.tier:
            raise ValueError(f"grade {self.grade!r} does not belong to tier {self.tier.value}")
        return self

    @property
    def paid(self) -> bool:
        return self.tier in PAID_TIERS

    @property
    def hosted(self) -> bool:
        return self.tier in HOSTED_TIERS

    @property
    def priced(self) -> bool:
        return self.usd_per_m_input is not None and self.usd_per_m_output is not None


class RosterEntry(BaseModel):
    """`wants` is what the entry is meant to receive; the receipt must cover all of it."""

    model_config = ConfigDict(frozen=True)
    spec: TierSpec
    wants: frozenset[MediaClass] = frozenset()
    receipt: Receipt | None = None


def _tier_for(grade_label: str) -> tuple[Tier, Grade | None]:
    label = grade_label.strip().upper()
    if label == "F":
        return Tier.T2_FREE, None
    try:
        grade = Grade[label]
    except KeyError as exc:
        raise ValueError(f"unknown grade {grade_label!r}") from exc
    if grade not in GRADE_TIER:
        raise ValueError("grade G0 is code only and has no model endpoint")
    return GRADE_TIER[grade], grade


class Roster:
    def __init__(self, entries: Sequence[RosterEntry]) -> None:
        self._entries = tuple(entries)

    @classmethod
    def load(cls, entries: Sequence[RosterEntry]) -> Roster:
        """Raises ReceiptMissing for an entry that wants a class its receipt does not cover (RQ-50)."""
        seen: set[str] = set()
        for e in entries:
            if e.spec.entry_id in seen:
                raise ValueError(f"duplicate roster entry {e.spec.entry_id!r}")
            seen.add(e.spec.entry_id)
            if e.spec.tier is Tier.T2_FREE and (e.wants or e.receipt is not None):
                raise PrivacyViolation("tier F takes public payloads only and has no receipt")
            covered = e.receipt.classes if e.receipt else frozenset()
            if e.wants - covered:
                raise ReceiptMissing(f"{e.spec.entry_id}: no receipt for {sorted(c.value for c in e.wants - covered)}")
        return cls(entries)

    @property
    def entries(self) -> tuple[RosterEntry, ...]:
        return self._entries

    def tiers(self, grade: Grade, sensitivity: Sensitivity, media: frozenset[MediaClass]) -> list[TierSpec]:
        """Tiers of `grade` that may take a request. Never tier F. Personal data needs a receipt for every class."""
        need = media or frozenset({MediaClass.TEXT})
        out: list[TierSpec] = []
        for e in self._entries:
            if e.spec.grade != grade or e.spec.tier is Tier.T2_FREE:
                continue
            # Anything that is not exactly PUBLIC (a str, None, a model_construct value) counts as personal.
            if sensitivity is not Sensitivity.PUBLIC and not (e.receipt and need <= e.receipt.classes):
                continue
            out.append(e.spec)
        return out

    def free_tiers(self) -> list[TierSpec]:
        """Tier F endpoints. Only the public-payload path of the gateway may use them."""
        return [e.spec for e in self._entries if e.spec.tier is Tier.T2_FREE]

    def handoff_needed(self, grade_floor: Grade, grade_ceiling: Grade, media: frozenset[MediaClass]) -> bool:
        """True when no tier in the grade range has a receipt for the class: the caregiver gets the original (RQ-96)."""
        return not any(
            self.tiers(Grade(g), Sensitivity.PERSONAL, media) for g in range(int(grade_floor), int(grade_ceiling) + 1)
        )

    def has_unreceipted(self, grade_floor: Grade, grade_ceiling: Grade, media: frozenset[MediaClass]) -> bool:
        """True when a tier exists in range that lacks a receipt for `media` (the reason for a ReceiptMissing)."""
        return any(
            self.tiers(Grade(g), Sensitivity.PUBLIC, media) for g in range(int(grade_floor), int(grade_ceiling) + 1)
        )


def entries_from_mapping(data: dict) -> list[RosterEntry]:
    out: list[RosterEntry] = []
    for i, raw in enumerate(data.get("entry", [])):
        tier, grade = _tier_for(str(raw["grade"]))
        spec = TierSpec(
            entry_id=str(raw.get("id") or f"entry-{i}"),
            tier=tier,
            grade=grade,
            model=raw["model"],
            base_url=raw["base_url"],
            key_env=raw.get("key_env", ""),
            usd_per_m_input=_dec(raw.get("usd_per_m_input")),
            usd_per_m_output=_dec(raw.get("usd_per_m_output")),
        )
        rc = raw.get("receipt")
        receipt = (
            Receipt(
                licence=rc["licence"],
                training=rc["training"],
                retention=rc["retention"],
                classes=frozenset(MediaClass(c) for c in rc["classes"]),
                read_on=rc["read_on"] if isinstance(rc["read_on"], date) else date.fromisoformat(rc["read_on"]),
                read_by=rc["read_by"],
            )
            if rc
            else None
        )
        wants = frozenset(MediaClass(c) for c in raw.get("wants", []))
        out.append(RosterEntry(spec=spec, wants=wants, receipt=receipt))
    return out


def _dec(v: object) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def load_roster_file(path: Path | str) -> Roster:
    """Read the private roster file (TOML). The schema and synthetic ids are in infra/roster.example.toml."""
    with open(path, "rb") as fh:
        return Roster.load(entries_from_mapping(tomllib.load(fh)))


def tier_names(entries: Iterable[RosterEntry]) -> set[str]:
    return {e.spec.tier.value for e in entries}
