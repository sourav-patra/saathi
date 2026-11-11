#!/usr/bin/env python3
"""Manual pilot counts (RQ-117, 118, 119): python3 scripts/pilot_counts.py LOG.csv | --check-receipt FILE

The pilot log lives in the git-ignored private/ folder and never enters the repository. Each row has coded fields
only (SPEC product RQ-117): week, request_type, task_or_social, elder_initiated, minutes_to_answer, clarifications,
call_followed. A header with any other column, or a value outside the coded set (free text, a name, a date), fails
the whole log: nothing is counted from a log that holds content.

The output is counts only (integers): n, the week range, counts by request type, the elder-initiated count, the task
and social split, median and p90 minutes to answer, and the call-followed count. No row, no date finer than a week.

--check-receipt validates a pilot receipt (RQ-118 aggregate or RQ-119 exit): first line a sentence, then `key: value`
lines from a fixed set; a key outside the set (a content field) fails, n and the week range are required, and an
exit receipt needs an outcome of go, narrow or stop.

Exit 0 ok, 1 the log or receipt is refused.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

FIELDS = (
    "week",
    "request_type",
    "task_or_social",
    "elder_initiated",
    "minutes_to_answer",
    "clarifications",
    "call_followed",
)
# The intents of D-09 (rides, groceries, shopping, food, debunking forwards, understanding voice, image and video,
# phone and tech help, appointments, reminders, general questions) or `other`.
REQUEST_TYPES = (
    "ride",
    "grocery",
    "shopping",
    "food",
    "claim_check",
    "understand_media",
    "tech_help",
    "appointment",
    "reminder",
    "general_qa",
    "other",
)
YES_NO = ("yes", "no")
TASK_SOCIAL = ("task", "social")
MAX_WEEK = 8  # the pilot runs 2 to 4 weeks, beside 2 baseline weeks; a larger number is not a week number
MAX_MINUTES = 7 * 24 * 60
MAX_CLARIFICATIONS = 50
BUILD_LIST_MIN = 3  # RQ-119 check 2: an intent with at least 3 requests stays in the Phase 1 build list
OUTCOMES = ("go", "narrow", "stop")

# Receipt keys: the RQ-100 fields plus the aggregate counts. Anything else is a content field and fails.
RECEIPT_BASE_KEYS = {"id", "revision", "date", "command", "result", "signer", "note", "kind", "router"}
# Every base key has a closed value pattern, so no base key can carry a name or a quote.
NOTE_CODES = ("no content", "aggregate counts only", "owner signed", "owner action pending")
RECEIPT_BASE_PATTERNS: dict[str, re.Pattern[str]] = {
    "id": re.compile(r"^(?:RQ|PL)-\d{1,3}[a-z]?$"),
    "revision": re.compile(r"^[0-9a-f]{7,40}$"),
    "date": re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$"),
    "command": re.compile(r"^(?:uv run )?python3? scripts/pilot_counts\.py(?: [\w./-]+){0,3}$"),
    "result": re.compile(r"^(?:verified|invalid|unavailable)$"),
    "signer": re.compile(r"^(?:owner|caregiver|builder|reviewer|orchestrator)$"),
    "note": re.compile(r"^(?:" + "|".join(re.escape(c) for c in NOTE_CODES) + r")$"),
    "kind": re.compile(r"^receipt$"),
    "router": re.compile(r"^(?:absent|present)$"),
}
RECEIPT_COUNT_KEYS = {
    "n",
    "weeks",
    "elder_initiated",
    "task",
    "social",
    "minutes_median",
    "minutes_p90",
    "call_followed",
    "outcome",
    "habit_last_two_weeks",
    "prefers_phone",
    "build_list",
    "deferred",
} | {f"type_{t}" for t in REQUEST_TYPES}
WEEKS = re.compile(r"^(\d{1,2})-(\d{1,2})$")
INT = re.compile(r"^\d{1,6}$")


class PilotLogError(ValueError):
    """The log holds a column or a value outside the coded set."""


def _int_in(value: str, lo: int, hi: int, field: str, where: str) -> int:
    if not INT.match(value) or not lo <= int(value) <= hi:
        raise PilotLogError(f"{where}: {field} is not a number from {lo} to {hi}")
    return int(value)


def _one_of(value: str, allowed: Sequence[str], field: str, where: str) -> str:
    if value not in allowed:
        raise PilotLogError(f"{where}: {field} is outside the coded set")
    return value


def parse_row(row: Mapping[str, str | None], where: str) -> dict[str, int | str]:
    """One coded row; any value outside its set raises (the value itself is never echoed)."""
    if set(row) != set(FIELDS) or any(v is None for v in row.values()):
        raise PilotLogError(f"{where}: the row does not have exactly the coded fields")
    v = {k: (row[k] or "").strip() for k in FIELDS}
    return {
        "week": _int_in(v["week"], 1, MAX_WEEK, "week", where),
        "request_type": _one_of(v["request_type"], REQUEST_TYPES, "request_type", where),
        "task_or_social": _one_of(v["task_or_social"], TASK_SOCIAL, "task_or_social", where),
        "elder_initiated": _one_of(v["elder_initiated"], YES_NO, "elder_initiated", where),
        "minutes_to_answer": _int_in(v["minutes_to_answer"], 0, MAX_MINUTES, "minutes_to_answer", where),
        "clarifications": _int_in(v["clarifications"], 0, MAX_CLARIFICATIONS, "clarifications", where),
        "call_followed": _one_of(v["call_followed"], YES_NO, "call_followed", where),
    }


def _percentile(sorted_values: Sequence[int], q: float) -> int:
    """Nearest-rank percentile, an integer minute."""
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[rank - 1]


def count_pilot(log: Path) -> dict[str, int]:
    """Counts only. Raises PilotLogError on a header other than FIELDS or any value outside the coded set."""
    with log.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, restkey="_extra")
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise PilotLogError("header: the log must have exactly the coded columns, in order")
        rows = [parse_row(r, f"row {n}") for n, r in enumerate(reader, 1)]
    if not rows:
        raise PilotLogError("the log has no rows")
    minutes = sorted(int(r["minutes_to_answer"]) for r in rows)
    weeks = [int(r["week"]) for r in rows]
    out: dict[str, int] = {
        "n": len(rows),
        "week_first": min(weeks),
        "week_last": max(weeks),
        "elder_initiated": sum(r["elder_initiated"] == "yes" for r in rows),
        "task": sum(r["task_or_social"] == "task" for r in rows),
        "social": sum(r["task_or_social"] == "social" for r in rows),
        "minutes_median": _percentile(minutes, 0.5),
        "minutes_p90": _percentile(minutes, 0.9),
        "clarifications": sum(int(r["clarifications"]) for r in rows),
        "call_followed": sum(r["call_followed"] == "yes" for r in rows),
    }
    for t in REQUEST_TYPES:
        out[f"type_{t}"] = sum(r["request_type"] == t for r in rows)
    return out


def build_list(counts: Mapping[str, int]) -> tuple[list[str], list[str]]:
    """RQ-119 check 2: intents with at least BUILD_LIST_MIN requests stay; the others are deferred. `other` is never
    an intent of the build list."""
    keep = [t for t in REQUEST_TYPES if t != "other" and counts.get(f"type_{t}", 0) >= BUILD_LIST_MIN]
    deferred = [t for t in REQUEST_TYPES if t != "other" and t not in keep]
    return keep, deferred


def _leak_problems(text: str, names_file: Path | None) -> list[str]:
    """The leak scan text rules (and the private names file when present) over the whole receipt; by rule only."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import leak_scan  # noqa: PLC0415

    env = os.environ.get("SAATHI_NAMES_FILE")
    path = names_file or (Path(env) if env else None)
    names = leak_scan.load_names(path) if path is not None and path.is_file() else None
    return [f"line {f.line}: leak scan {f.rule}" for f in leak_scan.scan_text(text, "receipt", names)]


def check_receipt(path: Path, *, exit_receipt: bool | None = None, names_file: Path | None = None) -> list[str]:
    """Problems with a pilot receipt; empty means accepted. `exit_receipt` None: an RQ-119 id means an exit receipt.
    Base keys take only their closed value patterns; the whole text also passes the leak scan rules and names file."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    problems: list[str] = _leak_problems(text, names_file)
    if not lines or not lines[0].strip() or re.match(r"^[a-z_0-9]+:", lines[0]):
        problems.append("first line must be one plain sentence")
    fields: dict[str, str] = {}
    for n, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not re.fullmatch(r"[a-z_0-9]+", key):
            problems.append(f"line {n}: not a `key: value` line (free text is a content field)")
            continue
        if key not in RECEIPT_BASE_KEYS | RECEIPT_COUNT_KEYS:
            problems.append(f"line {n}: {key} is a content field, not an aggregate count")
            continue
        fields[key] = value.strip()
        pattern = RECEIPT_BASE_PATTERNS.get(key)
        if pattern is not None and not pattern.match(fields[key]):
            problems.append(f"line {n}: {key} is not in its coded form")
    if not INT.match(fields.get("n", "")):
        problems.append("n is missing or not a count")
    m = WEEKS.match(fields.get("weeks", ""))
    if not m or int(m.group(1)) > int(m.group(2)):
        problems.append("weeks is missing or not a week range such as 1-4")
    for key in RECEIPT_COUNT_KEYS - {"n", "weeks", "outcome", "build_list", "deferred", "prefers_phone",
                                     "habit_last_two_weeks"}:  # fmt: skip
        if key in fields and not INT.match(fields[key]):
            problems.append(f"{key} is not a count")
    for key in ("build_list", "deferred"):
        if key in fields and any(t and t not in REQUEST_TYPES for t in re.split(r"[,\s]+", fields[key])):
            problems.append(f"{key} names something other than a coded request type")
    for key in ("prefers_phone", "habit_last_two_weeks"):
        if key in fields and fields[key] not in YES_NO:
            problems.append(f"{key} must be yes or no")
    is_exit = exit_receipt if exit_receipt is not None else fields.get("id", "").upper() == "RQ-119"
    if is_exit and fields.get("outcome") not in OUTCOMES:
        problems.append("an exit receipt needs outcome go, narrow or stop")
    if "outcome" in fields and fields["outcome"] not in OUTCOMES:
        problems.append("outcome must be go, narrow or stop")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Count a manual pilot log (coded fields only) or check a receipt.")
    ap.add_argument("log", nargs="?", type=Path)
    ap.add_argument("--check-receipt", type=Path, default=None)
    ap.add_argument("--names-file", type=Path, default=None, help="private names list (else SAATHI_NAMES_FILE)")
    args = ap.parse_args(argv)
    if args.check_receipt is not None:
        problems = check_receipt(args.check_receipt, names_file=args.names_file)
        for p in problems:
            print(f"receipt: {p}")
        print(f"pilot receipt: {'refused' if problems else 'accepted'}")
        return 1 if problems else 0
    if args.log is None:
        ap.error("a log path or --check-receipt is required")
    try:
        counts = count_pilot(args.log)
    except PilotLogError as exc:
        print(f"pilot log refused: {exc}")
        return 1
    keep, deferred = build_list(counts)
    for key, value in counts.items():
        print(f"{key}: {value}")
    print(f"build_list: {', '.join(keep)}")
    print(f"deferred: {', '.join(deferred)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
