#!/usr/bin/env python3
"""Document lint (RQ-101, PL-19): python3 scripts/doc_lint.py [--names-file FILE] [--words FILE] PATH...

A PATH is a markdown file or a directory searched for *.md files. Rules (names as in LLD M22):
filename_date, filename_space, dash, filler, size, index_row, record_shape, closing_line, bullet_bold, names.
Exit 1 when any rule fires. The names rule needs --names-file; without it that rule is unavailable and does not fail.

Scope and exemptions, kept next to the code so the lint and the design agree:
- kind comes from front matter `kind:`, else the 00-INDEX.md row, else the file name prefix.
- size applies to records (16000 bytes), contracts and rules (24000, warning at 22500), the index (2000) and every
  other file except README.md (24000, the contract limit).
- closing_line applies to every file except README.md (the entry page, DOC-PHILOSOPHY, Entry pages) and receipts.
  A receipt is machine format (DOC-PHILOSOPHY, Four kinds): its skipped-or-not-checked line is a labelled field,
  so it is exempt from closing_line and record_shape.
- fenced code blocks (``` or ~~~) and the front matter block are skipped by the prose rules (dash, filler,
  bullet_bold).
- dash fires only with letters on both sides or spaces around the dash; a number range with an en dash passes.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_WORDS = HERE / "doc_lint_words.txt"
LIMITS = {"record": 16000, "contract": 24000, "rules": 24000, "index": 2000}
WARN_CONTRACT = 22500
CLOSING = "Skipped or not checked:"
ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?!\d)")
DATE_LIKE = re.compile(r"(?<!\d)(?:\d{1,4}[-_.]\d{1,2}[-_.]\d{1,4}|(?:19|20)\d{6})(?!\d)")
PREFIX_KINDS = {
    "RECORD-": "record", "RECEIPT-": "receipt", "PRD-": "contract", "SPEC-": "contract", "POLICY-": "contract",
    "TRD-": "contract", "LLD-": "contract", "DOC-": "rules",
}
INDEX_ROW = re.compile(r"^\|\s*(\w+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|")
HASH12 = re.compile(r"[0-9a-f]{12}")
ENTRY_PAGE = "README.md"
DEFAULT_LIMIT = 24000
RECORD_LINE = re.compile(r"^- \d{4}-\d{2}-\d{2} \S")
DASH = re.compile(r"[^\W\d_]\s*[—–]\s*[^\W\d_]|\s[—–]\s")


@dataclass(frozen=True)
class Finding:
    rule: str
    path: Path
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.message}"


def front_matter_kind(text: str) -> str | None:
    if text.startswith("---\n"):
        head = text.split("\n---\n", 1)[0]
        m = re.search(r"^kind:\s*(\w+)", head, re.M)
        return m.group(1) if m else None
    return None


def kind_of(path: Path, text: str) -> str | None:
    if path.name == "00-INDEX.md":
        return "index"
    return front_matter_kind(text) or next((k for p, k in PREFIX_KINDS.items() if path.name.startswith(p)), None)


def prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield (line number, line) outside front matter and fenced code blocks."""
    lines = text.splitlines()
    start = 0
    if text.startswith("---\n"):
        for n, line in enumerate(lines[1:], 1):
            if line == "---":
                start = n + 1
                break
    fenced = False
    for n in range(start, len(lines)):
        line = lines[n]
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced:
            yield n + 1, line


def strip_inline_code(line: str) -> str:
    return re.sub(r"`[^`]*`", "", line)


def load_words(path: Path = DEFAULT_WORDS) -> list[str]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        w = raw.strip()
        if w and not w.startswith("#"):
            out.append(w)
    return out


def rule_filename_date(path: Path, text: str) -> list[Finding]:
    name = path.name
    stem = re.sub(r"\.md$", "", name)
    clean = ISO_DATE.sub("", stem)
    found = DATE_LIKE.search(clean)
    if found:
        return [Finding("filename_date", path, 0, f"date {found.group(0)!r} in the name is not YYYY-MM-DD")]
    return []


def rule_filename_space(path: Path, text: str) -> list[Finding]:
    return [Finding("filename_space", path, 0, "the file name contains a space")] if " " in path.name else []


def rule_dash(path: Path, text: str) -> list[Finding]:
    out = []
    for n, line in prose_lines(text):
        if DASH.search(strip_inline_code(line)):
            out.append(Finding("dash", path, n, "an em or en dash joins clauses; use a period, comma or colon"))
    return out


def rule_filler(path: Path, text: str, words: Sequence[str] | None = None) -> list[Finding]:
    words = load_words() if words is None else words
    pats = [(w, re.compile(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])" if not w.endswith(".") else
                           r"(?<![\w-])" + re.escape(w), re.I)) for w in words]
    out = []
    for n, line in prose_lines(text):
        plain = strip_inline_code(line)
        for w, pat in pats:
            if pat.search(plain):
                out.append(Finding("filler", path, n, f"filler or promotional word {w!r}"))
    return out


def rule_size(path: Path, text: str) -> list[Finding]:
    kind = kind_of(path, text)
    limit = LIMITS.get(kind or "")
    if limit is None and kind != "receipt" and path.name != ENTRY_PAGE:
        limit = DEFAULT_LIMIT
    size = len(text.encode("utf-8"))
    if limit and size > limit:
        return [Finding("size", path, 0, f"{size} bytes is over the {limit} byte limit of a {kind}")]
    return []


def size_warnings(path: Path, text: str) -> list[str]:
    kind = kind_of(path, text)
    size = len(text.encode("utf-8"))
    if kind in ("contract", "rules") and WARN_CONTRACT < size <= LIMITS["contract"]:
        return [f"{path}: {size} bytes, warn at {WARN_CONTRACT}"]
    return []


def resolve_index_path(root: Path, name: str) -> Path:
    """A name with a slash is relative to the repository root; a name without one is in docs/design/ (D-94)."""
    return root / name if "/" in name else root / "docs" / "design" / name


def rule_index_row(path: Path, text: str) -> list[Finding]:
    if path.name != "00-INDEX.md":
        return []
    root = path.parent
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = INDEX_ROW.match(line)
        if not m or m.group(1).lower() == "kind" or set(m.group(2)) <= set("-:"):
            continue
        if not HASH12.fullmatch(m.group(3)):
            msg = f"hash {m.group(3)!r} of {m.group(2)} is not 12 lowercase hex characters"
            out.append(Finding("index_row", path, n, msg))
            continue
        target = resolve_index_path(root, m.group(2))
        if not target.is_file():
            where = target.relative_to(root)
            out.append(Finding("index_row", path, n, f"path {m.group(2)} does not resolve to {where}"))
        elif hashlib.sha256(target.read_bytes()).hexdigest()[:12] != m.group(3):
            out.append(Finding("index_row", path, n, f"hash prefix of {m.group(2)} does not match the file"))
    return out


def rule_record_shape(path: Path, text: str) -> list[Finding]:
    if kind_of(path, text) != "record":
        return []
    return [Finding("record_shape", path, n, "a record bullet must start with `- YYYY-MM-DD `")
            for n, line in prose_lines(text) if line.startswith(("- ", "* ")) and not RECORD_LINE.match(line)]


def rule_closing_line(path: Path, text: str) -> list[Finding]:
    if path.name == ENTRY_PAGE or kind_of(path, text) == "receipt":
        return []
    body = text.rstrip("\n").splitlines()
    if not body or not body[-1].startswith(CLOSING):
        return [Finding("closing_line", path, len(body), f"the last line must start with {CLOSING!r}")]
    return []


def rule_bullet_bold(path: Path, text: str) -> list[Finding]:
    return [Finding("bullet_bold", path, n, "a bullet must not start with a bold span")
            for n, line in prose_lines(text) if re.match(r"^\s*[-*+]\s+(\*\*|__)", line)]


RULES: dict[str, Callable[[Path, str], list[Finding]]] = {
    "filename_date": rule_filename_date,
    "filename_space": rule_filename_space,
    "dash": rule_dash,
    "filler": rule_filler,
    "size": rule_size,
    "index_row": rule_index_row,
    "record_shape": rule_record_shape,
    "closing_line": rule_closing_line,
    "bullet_bold": rule_bullet_bold,
}


def rule_names(path: Path, text: str, names: Sequence[str]) -> list[Finding]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for name in names:
            if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", line, re.I):
                out.append(Finding("names", path, n, "a private name from the names file appears"))
    return out


def load_names(path: Path) -> list[str]:
    return [s for raw in path.read_text(encoding="utf-8").splitlines() if (s := raw.strip()) and not s.startswith("#")]


def collect(paths: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files += sorted(f for f in p.rglob("*.md") if f.is_file())
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(str(p))
    return files


def lint(
    paths: Sequence[Path], names_file: Path | None = None, words_file: Path = DEFAULT_WORDS
) -> tuple[list[Finding], list[str]]:
    """Return (findings, notes). A note is a warning or an unavailable rule; neither fails the run."""
    words = load_words(words_file)
    names = load_names(names_file) if names_file else None
    findings: list[Finding] = []
    notes: list[str] = []
    if names is None:
        notes.append("names: unavailable (no --names-file); the private-name check did not run")
    for f in collect(paths):
        text = f.read_text(encoding="utf-8")
        for name, fn in RULES.items():
            findings += rule_filler(f, text, words) if name == "filler" else fn(f, text)
        if names is not None:
            findings += rule_names(f, text, names)
        notes += [f"warn: {w}" for w in size_warnings(f, text)]
    return findings, notes


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doc_lint.py", description="Document lint (RQ-101).")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--names-file", type=Path, default=None)
    ap.add_argument("--words", type=Path, default=DEFAULT_WORDS)
    args = ap.parse_args(argv)
    findings, notes = lint(args.paths, args.names_file, args.words)
    for note in notes:
        print(note)
    for f in findings:
        print(f)
    print(f"doc lint: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
