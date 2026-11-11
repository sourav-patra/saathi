"""LT-45: manual pilot counts and receipt validator (RQ-117, 118, 119; TQ-79). Every log here is synthetic."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pilot_counts", ROOT / "scripts" / "pilot_counts.py")
assert _spec and _spec.loader
pc = importlib.util.module_from_spec(_spec)
sys.modules["pilot_counts"] = pc
_spec.loader.exec_module(pc)

HEADER = ",".join(pc.FIELDS)
ROWS = [
    "1,grocery,task,yes,12,0,no",
    "1,ride,task,yes,30,1,no",
    "2,grocery,task,no,5,0,yes",
    "2,general_qa,social,yes,60,2,no",
    "3,grocery,task,yes,8,0,no",
    "3,ride,task,yes,20,0,no",
    "4,ride,task,yes,45,1,yes",
    "4,other,social,no,90,0,no",
]


def log(tmp_path: Path, rows: list[str], header: str = HEADER) -> Path:
    p = tmp_path / "pilot.csv"
    p.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return p


def test_counts_of_a_coded_log(tmp_path):
    c = pc.count_pilot(log(tmp_path, ROWS))
    assert c["n"] == 8 and (c["week_first"], c["week_last"]) == (1, 4)
    assert c["type_grocery"] == 3 and c["type_ride"] == 3 and c["type_other"] == 1
    assert (c["task"], c["social"], c["elder_initiated"], c["call_followed"]) == (6, 2, 6, 2)
    assert c["minutes_median"] == 20 and c["minutes_p90"] == 90


@pytest.mark.parametrize(
    "bad",
    [
        "1,grocery,task,yes,12,0,asked for milk",  # free text in a coded field
        "1,please book a cab,task,yes,12,0,no",  # free text as the request type
        "2026-11-02,grocery,task,yes,12,0,no",  # a date where a week number belongs
        "1,grocery,task,maybe,12,0,no",
        "1,grocery,task,yes,about ten,0,no",
        "1,grocery,task,yes,12,0,no,extra note",  # a column beyond the coded set
        "1,grocery,task",  # a short row
    ],
)
def test_free_text_field_fails(tmp_path, bad):
    with pytest.raises(pc.PilotLogError) as e:
        pc.count_pilot(log(tmp_path, [ROWS[0], bad]))
    assert "milk" not in str(e.value) and "cab" not in str(e.value)  # the value is never echoed


def test_free_text_column_fails(tmp_path):
    with pytest.raises(pc.PilotLogError):
        pc.count_pilot(log(tmp_path, [r + ",x" for r in ROWS], header=HEADER + ",message"))


def test_output_is_counts_only(tmp_path):
    c = pc.count_pilot(log(tmp_path, ROWS))
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in c.values())
    assert set(c) <= {
        "n", "week_first", "week_last", "elder_initiated", "task", "social", "minutes_median", "minutes_p90",
        "clarifications", "call_followed", *(f"type_{t}" for t in pc.REQUEST_TYPES),
    }  # fmt: skip


def test_cli_refuses_free_text_and_prints_no_row(tmp_path):
    p = log(tmp_path, [ROWS[0], "1,grocery,task,yes,12,0,secretword"])
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "pilot_counts.py"), str(p)], capture_output=True,
                          text=True, check=False)  # fmt: skip
    assert done.returncode == 1 and "secretword" not in done.stdout


def test_build_list_is_derived_from_intent_counts(tmp_path):
    keep, deferred = pc.build_list(pc.count_pilot(log(tmp_path, ROWS)))
    assert keep == ["ride", "grocery"]
    assert "general_qa" in deferred and "other" not in keep + deferred


AGGREGATE = """Aggregate counts of the manual pilot, no content.

id: RQ-118
revision: {rev}
date: 2026-12-07
signer: owner
command: python3 scripts/pilot_counts.py private/pilot.csv
result: verified
n: 8
weeks: 1-4
type_grocery: 3
type_ride: 3
elder_initiated: 6
task: 6
social: 2
minutes_median: 20
minutes_p90: 90
""".replace("{rev}", "0" * 40)


def test_receipt_validator(tmp_path):
    ok = tmp_path / "RECEIPT-rq-118-2026-12-07.md"
    ok.write_text(AGGREGATE, encoding="utf-8")
    assert pc.check_receipt(ok) == []
    bad = tmp_path / "bad.md"
    bad.write_text(AGGREGATE + "message: asked for a ride to the clinic\n", encoding="utf-8")
    assert any("content field" in p for p in pc.check_receipt(bad))
    prose = tmp_path / "prose.md"
    prose.write_text(AGGREGATE + "she said she liked it\n", encoding="utf-8")
    assert pc.check_receipt(prose)
    nweeks = tmp_path / "nweeks.md"
    nweeks.write_text(AGGREGATE.replace("weeks: 1-4\n", "").replace("n: 8\n", ""), encoding="utf-8")
    assert len(pc.check_receipt(nweeks)) == 2


def test_exit_receipt_needs_an_outcome(tmp_path):
    body = AGGREGATE.replace("id: RQ-118", "id: RQ-119") + "build_list: ride, grocery\n"
    p = tmp_path / "exit.md"
    p.write_text(body, encoding="utf-8")
    assert any("outcome" in x for x in pc.check_receipt(p))
    p.write_text(body + "outcome: go\nhabit_last_two_weeks: yes\nprefers_phone: no\n", encoding="utf-8")
    assert pc.check_receipt(p) == []
    p.write_text(body + "outcome: maybe\n", encoding="utf-8")
    assert pc.check_receipt(p)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("note", "Zz Synthetic Person liked the rides"),
        ("signer", "Zz Synthetic"),
        ("revision", "the main branch"),
        ("date", "last Tuesday"),
        ("command", "echo she said yes"),
        ("result", "went well"),
        ("id", "a talk with the family"),
        ("kind", "story"),
        ("router", "a person"),
    ],
)
def test_base_keys_take_only_their_coded_form(tmp_path, key, value):
    p = tmp_path / "r.md"
    p.write_text(AGGREGATE + f"{key}: {value}\n", encoding="utf-8")
    assert any(f"{key} is not in its coded form" in x for x in pc.check_receipt(p))


@pytest.mark.parametrize("value", list(pc.NOTE_CODES))
def test_coded_note_is_accepted(tmp_path, value):
    p = tmp_path / "r.md"
    p.write_text(AGGREGATE + f"note: {value}\n", encoding="utf-8")
    assert pc.check_receipt(p) == []


def test_receipt_runs_the_leak_scan_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("SAATHI_NAMES_FILE", raising=False)
    p = tmp_path / "r.md"
    phone = "+91 98" + "765 43" + "128"
    p.write_text(AGGREGATE.replace("no content.", f"no content, call {phone}."), encoding="utf-8")
    assert any("leak scan phone" in x for x in pc.check_receipt(p))


def test_receipt_runs_the_names_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SAATHI_NAMES_FILE", raising=False)
    names = tmp_path / "names.txt"
    names.write_text("# synthetic\nZzsynthname\n", encoding="utf-8")
    p = tmp_path / "r.md"
    p.write_text(AGGREGATE.replace("no content.", "no content for Zzsynthname."), encoding="utf-8")
    assert pc.check_receipt(p) == []
    problems = pc.check_receipt(p, names_file=names)
    assert any("leak scan names" in x for x in problems)
    assert all("Zzsynthname" not in x for x in problems)
    monkeypatch.setenv("SAATHI_NAMES_FILE", str(names))
    assert any("leak scan names" in x for x in pc.check_receipt(p))
