"""LT-28: the roster (M19; RQ-45, RQ-50, RQ-96, PL-20). Synthetic ids only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from saathi.domain import Grade, Sensitivity
from saathi.errors import PrivacyViolation, ReceiptMissing
from saathi.llm.roster import (
    MediaClass,
    Receipt,
    Roster,
    RosterEntry,
    Tier,
    TierSpec,
    entries_from_mapping,
    load_roster_file,
)

ROOT = Path(__file__).resolve().parent.parent
TEXT = frozenset({MediaClass.TEXT})


def _rc(*classes: MediaClass) -> Receipt:
    return Receipt(
        licence="example", training="example", retention="example", classes=frozenset(classes),
        read_on=date(2000, 1, 1), read_by="owner",
    )  # fmt: skip


def _spec(eid: str, tier: Tier, grade: Grade | None) -> TierSpec:
    return TierSpec(entry_id=eid, tier=tier, grade=grade, model="example-grade-x", base_url="http://x.invalid/v1")


def test_entry_without_receipt_gets_no_personal_data():
    e = RosterEntry(spec=_spec("a", Tier.T2, Grade.G2))  # no receipt, wants nothing: allowed in the file
    r = Roster.load([e])
    assert r.tiers(Grade.G2, Sensitivity.PERSONAL, TEXT) == []
    assert [s.entry_id for s in r.tiers(Grade.G2, Sensitivity.PUBLIC, TEXT)] == ["a"]
    # a receipt for another class does not help
    e2 = RosterEntry(spec=_spec("b", Tier.T2, Grade.G2), receipt=_rc(MediaClass.IMAGE))
    assert Roster.load([e2]).tiers(Grade.G2, Sensitivity.PERSONAL, TEXT) == []


@pytest.mark.parametrize("missing", list(MediaClass))
def test_no_route_for_unreceipted_class(missing):
    covered = [c for c in MediaClass if c is not missing]
    e = RosterEntry(spec=_spec("a", Tier.T3, Grade.G3), wants=frozenset(covered), receipt=_rc(*covered))
    r = Roster.load([e])
    assert r.tiers(Grade.G3, Sensitivity.PERSONAL, frozenset({missing})) == []
    assert r.tiers(Grade.G3, Sensitivity.PERSONAL, frozenset(covered)) != []
    # one class without a receipt in the request removes the tier for the whole request
    assert r.tiers(Grade.G3, Sensitivity.PERSONAL, frozenset({covered[0], missing})) == []
    # an entry that wants the class without a receipt for it does not load
    with pytest.raises(ReceiptMissing):
        Roster.load([RosterEntry(spec=_spec("a", Tier.T3, Grade.G3), wants=frozenset({missing}), receipt=None)])
    with pytest.raises(ReceiptMissing):
        everything = frozenset(MediaClass)
        Roster.load([RosterEntry(spec=_spec("a", Tier.T3, Grade.G3), wants=everything, receipt=_rc(*covered))])


@pytest.mark.parametrize("cls", list(MediaClass))
def test_handoff_per_class(cls):
    e = RosterEntry(spec=_spec("a", Tier.T2, Grade.G2), receipt=_rc(*(c for c in MediaClass if c is not cls)))
    r = Roster.load([e])
    assert r.handoff_needed(Grade.G1, Grade.G4, frozenset({cls}))
    assert r.has_unreceipted(Grade.G1, Grade.G4, frozenset({cls}))
    other = next(c for c in MediaClass if c is not cls)
    assert not r.handoff_needed(Grade.G1, Grade.G4, frozenset({other}))
    # the grade range matters: the entry is G2, so a G3-only range has no route for any class
    assert r.handoff_needed(Grade.G3, Grade.G4, frozenset({other}))


def test_tier_f_never_personal():
    f = RosterEntry(spec=_spec("f", Tier.T2_FREE, None))
    r = Roster.load([f])
    assert r.free_tiers()[0].entry_id == "f"
    for g in Grade:
        assert r.tiers(g, Sensitivity.PERSONAL, TEXT) == [] and r.tiers(g, Sensitivity.PUBLIC, TEXT) == []
    with pytest.raises(PrivacyViolation):
        Roster.load([RosterEntry(spec=_spec("f", Tier.T2_FREE, None), receipt=_rc(MediaClass.TEXT))])
    with pytest.raises(PrivacyViolation):
        Roster.load([RosterEntry(spec=_spec("f", Tier.T2_FREE, None), wants=TEXT)])


def test_tier_loader():
    r = load_roster_file(ROOT / "infra" / "roster.example.toml")
    by = {e.spec.entry_id: e.spec for e in r.entries}
    assert by["example-local"].tier is Tier.T1 and by["example-local"].grade == Grade.G1
    assert by["example-hosted"].tier is Tier.T2
    assert by["example-paid"].tier is Tier.T4 and by["example-paid"].priced and by["example-paid"].paid
    assert by["example-free"].tier is Tier.T2_FREE and by["example-free"].grade is None
    assert by["example-hosted"].key_env == "EXAMPLE_KEY_B"
    assert [s.entry_id for s in r.tiers(Grade.G2, Sensitivity.PERSONAL, frozenset({MediaClass.IMAGE}))] == [
        "example-hosted"
    ]
    assert r.tiers(Grade.G2, Sensitivity.PERSONAL, frozenset({MediaClass.AUDIO})) == []


def _raw(**kw):
    base = {"id": "x", "grade": "G2", "model": "example-grade-b", "base_url": "http://x.invalid/v1"}
    return {"entry": [{**base, **kw}]}


def test_tier_loader_rejects_bad_files():
    with pytest.raises(ValueError):
        entries_from_mapping(_raw(grade="G0"))
    with pytest.raises(ValueError):
        entries_from_mapping(_raw(grade="G9"))
    with pytest.raises(KeyError):
        entries_from_mapping({"entry": [{"grade": "G2"}]})
    with pytest.raises(ReceiptMissing):
        Roster.load(entries_from_mapping(_raw(wants=["text"])))
    dup = {"entry": _raw()["entry"] * 2}
    with pytest.raises(ValueError):
        Roster.load(entries_from_mapping(dup))
    ok = entries_from_mapping(_raw(wants=["text"], receipt={
        "licence": "l", "training": "t", "retention": "r", "classes": ["text"],
        "read_on": "2000-01-01", "read_by": "owner",
    }))  # fmt: skip
    assert Roster.load(ok).tiers(Grade.G2, Sensitivity.PERSONAL, TEXT)
