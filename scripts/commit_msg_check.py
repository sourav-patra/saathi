#!/usr/bin/env python3
"""Commit-message check (PL-31): python3 scripts/commit_msg_check.py MSGFILE | --range REV_RANGE

Rejects AI attribution in a commit message: any `Co-Authored-By:` trailer (a human co-author signs off with
`Signed-off-by:`, which PL-37 requires and this check allows), a session link or session id, a generated-by
footer or robot marker, a line that starts with generated/written/created/made/assisted/drafted
with or by, and an assisted-by or generated-by trailer. Terms from the SAATHI_NAMES_FILE list (model and provider
names, which never live in this repository) are rejected too. Standard library only, so the
`.githooks/commit-msg` hook runs it with a bare `python3`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence

CO_AUTHOR = re.compile(r"^\s*co-?authored-?by\s*:", re.I)
TOOL_TRAILER = re.compile(r"^\s*(?:assisted|generated|written|authored|created|made)[- ]?(?:with|by)\s*:", re.I)
GENERATED_FOOTER = re.compile(
    r"\b(?:generated|written|authored|created|made|assisted|drafted)\s+(?:with|by)\s+(?:\[|an?\s+ai\b|ai\b|a\s+(?:model|bot|agent)\b)",
    re.I,
)
LINE_START_GENERATED = re.compile(r"^\W*(?:generated|written|created|made|assisted|drafted)\s+(?:with|by)\b", re.I)
ROBOT = re.compile("[\U0001f916\U0001f9be]")
SESSION_LINK = re.compile(r"https?://\S*/(?:code/)?sessions?[/_]\S+", re.I)
SESSION_ID = re.compile(r"\bsession_[0-9A-Za-z]{12,}\b")
SCISSORS = "# ------------------------ >8 ------------------------"


def body_lines(text: str) -> list[str]:
    """The lines git would keep: comment lines and everything under the scissors line are dropped."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith(SCISSORS):
            break
        if line.startswith("#"):
            continue
        out.append(line)
    return out


def load_terms(path: str | None) -> list[str]:
    """Private terms (model and provider names) from the names file; they live outside the repository (PL-12)."""
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [s for raw in fh if (s := raw.strip()) and not s.startswith("#")]


def check_message(text: str, terms: Sequence[str] = ()) -> list[str]:
    problems: list[str] = []
    patterns = [re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)", re.I) for t in terms]
    for n, line in enumerate(body_lines(text), 1):
        if any(pat.search(line) for pat in patterns):
            problems.append(f"line {n}: a term from the names file (PL-12)")
        if CO_AUTHOR.match(line):
            problems.append(f"line {n}: Co-Authored-By trailer (PL-31)")
        elif TOOL_TRAILER.match(line):
            problems.append(f"line {n}: generated-by or assisted-by trailer (PL-31)")
        if GENERATED_FOOTER.search(line) or LINE_START_GENERATED.match(line):
            problems.append(f"line {n}: generated-by footer (PL-31)")
        if ROBOT.search(line):
            problems.append(f"line {n}: robot marker (PL-31)")
        if SESSION_LINK.search(line) or SESSION_ID.search(line):
            problems.append(f"line {n}: session link or session id (PL-31)")
    return problems


def check_range(rev_range: str, terms: Sequence[str] = ()) -> list[str]:
    done = subprocess.run(
        ["git", "log", "--format=%H%x1f%B%x1e", rev_range], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "git log failed")
    problems: list[str] = []
    for record in done.stdout.split("\x1e"):
        if "\x1f" not in record:
            continue
        sha, message = record.strip("\n").split("\x1f", 1)
        problems += [f"{sha[:12]}: {p}" for p in check_message(message, terms)]
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    terms = load_terms(os.environ.get("SAATHI_NAMES_FILE"))
    if len(args) == 2 and args[0] == "--range":
        try:
            problems = check_range(args[1], terms)
        except RuntimeError as exc:
            print(f"commit message check: unavailable: {exc}")
            return 2
    elif len(args) == 1 and not args[0].startswith("-"):
        with open(args[0], encoding="utf-8", errors="replace") as fh:
            problems = check_message(fh.read(), terms)
    else:
        print("usage: commit_msg_check.py MSGFILE | --range REV_RANGE", file=sys.stderr)
        return 2
    for p in problems:
        print(f"commit message rejected: {p}")
    if problems:
        print("Say what changed and why in plain words; no AI attribution, no session link (PL-31).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
