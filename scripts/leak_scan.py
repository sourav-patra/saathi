#!/usr/bin/env python3
"""Leak scan (PL-08, PL-12, PL-15, PL-31): python3 scripts/leak_scan.py [--names-file FILE] [--history] [ROOT]

Scans the files git would commit (tracked plus untracked and not ignored; a plain walk outside a git work tree)
for generic leaks: phone numbers, emails outside the owner noreply and reserved example domains, key and token
patterns, private home paths, sandbox temp paths, and media files. RFC 2606 reserved names (.example, .test,
.invalid, .localhost) pass as email domains and URL userinfo hosts unless an inner label is a public top-level
name; URL userinfo that carries a password is always flagged (a `<placeholder>` or `${VAR}` is not a
password). It always scans commit metadata (author and
committer, messages with attribution through commit_msg_check). With --history it also scans every added line and
added media path.

The private-name denylist comes from --names-file or the SAATHI_NAMES_FILE variable: one term per line, `#`
comments. The file lives under the git-ignored private/ folder; no real term belongs in the repository. A hit is
reported by file and line only, never by value, so CI logs stay clean. Without a list the names check is
unavailable: it is printed and does not fail, unless --require-names makes it exit 2.

Exit 0: nothing found. 1: findings. 2: unavailable (--require-names without a list, or git failed for --history).
A finding is reported to the owner; it is never fixed by rewriting history.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import commit_msg_check  # noqa: E402

MEDIA_EXT = {
    ".ogg", ".opus", ".oga", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".amr", ".mp4", ".mov", ".mkv", ".webm",
    ".3gp", ".avi", ".jpg", ".jpeg", ".heic", ".heif",
}  # fmt: skip
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "private"}
MAX_BYTES = 2_000_000

EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
EMAIL_OK_DOMAINS = ("users.noreply.github.com", "example.com", "example.org", "example.net")
# RFC 2606 reserved top-level names: an address or URL userinfo under them can reach no one.
RESERVED_TLDS = ("example", "test", "invalid", "localhost")
# A reserved name is allowed only when no inner label is a public top-level name: an address under `mail.com.test` still
# spells a real address, so it is flagged.
PUBLIC_TLDS = frozenset(
    "com net org in co io gov edu mil int info biz me us uk de fr jp cn ru br au ca app dev ai xyz".split()
)
URL_PASSWORD = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]*:([^\s/@]+)@")  # URL userinfo with a password
PASSWORD_PLACEHOLDER = re.compile(r"(?i)^(?:<[^>]*>|\$\{?[A-Za-z_][\w{}]*|\$\{\{.*|[\w-]*throwaway[\w-]*)$")
PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s-]?\(?\d{2,5}\)?[\s-]?\d{3,5}[\s-]?\d{3,6}|(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5})(?![\w])"
)
KEYS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cloud access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("sk- style secret key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("code host token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("chat platform token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("google style api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("json web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    (
        "secret assignment",
        re.compile(r"""(?i)\b\w*(?:api_?key|token|secret|password|passwd)\w*\s*[=:]\s*["']?([A-Za-z0-9/+_=-]{20,})"""),
    ),
)
PLACEHOLDER = re.compile(r"(?i)(?:your|change|example|placeholder|dummy|fake|redacted|xxxx|\*{4}|<|\$\{?|test)")
PRIVATE_PATH = re.compile(r"(?:/home/(?!user\b|runner\b)[a-z_][\w-]*/|/Users/[\w.-]+/|[A-Za-z]:\\Users\\[\w.-]+\\)")
SANDBOX_PATH = re.compile("/tmp/" + r"claude-[\w.-]+")  # an agent sandbox temp path (split so this line is clean)


@dataclass(frozen=True)
class Finding:
    rule: str
    where: str
    line: int
    message: str

    def __str__(self) -> str:
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"{loc}: {self.rule}: {self.message}"


def load_names(path: Path) -> list[str]:
    return [s for raw in path.read_text(encoding="utf-8").splitlines() if (s := raw.strip()) and not s.startswith("#")]


def _domain_ok(domain: str) -> bool:
    """The owner noreply, the example domains, or an RFC 2606 reserved name with no public label inside it."""
    if any(domain == ok or domain.endswith("." + ok) for ok in EMAIL_OK_DOMAINS):
        return True
    labels = domain.split(".")
    if labels[-1] not in RESERVED_TLDS:
        return False
    return not any(label in PUBLIC_TLDS for label in labels[:-1])


def scan_text(text: str, where: str, names: Sequence[str] | None, first_line: int = 1) -> list[Finding]:
    """Generic and denylist findings for one text; values are never copied into a message."""
    out: list[Finding] = []
    for n, line in enumerate(text.splitlines(), first_line):
        for m in EMAIL.finditer(line):
            domain = m.group(1).lower()
            if _domain_ok(domain):
                continue  # the owner noreply, the example domains, or an RFC 2606 reserved name
            out.append(Finding("email", where, n, "an email address outside the configured domains"))
        for m in PHONE.finditer(line):
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) < 10 or sum(c != "0" for c in digits) <= 4 or _is_run(digits):
                continue  # the +91 0000 placeholder style (mostly zeros) and counting runs such as 9876543210
            if m.group(0).lstrip("+").isdigit() and len(digits) > 12:
                continue  # a long bare number is an id, not a phone number
            out.append(Finding("phone", where, n, "a phone number"))
        for label, pattern in KEYS:
            m = pattern.search(line)
            if not m:
                continue
            value = m.group(1) if pattern.groups else m.group(0)
            if pattern.groups and (
                PLACEHOLDER.search(value)
                or PLACEHOLDER.search(line)
                or not (re.search(r"\d", value) and re.search(r"[A-Za-z]", value))
            ):
                continue  # a placeholder, or a value with no digit and letter mix (an identifier, not a key)
            out.append(Finding("key", where, n, f"a {label} pattern"))
        if any(not PASSWORD_PLACEHOLDER.match(m.group(1)) for m in URL_PASSWORD.finditer(line)):
            out.append(Finding("url_password", where, n, "a URL whose userinfo carries a password"))
        if PRIVATE_PATH.search(line):
            out.append(Finding("private_path", where, n, "a private home path"))
        if SANDBOX_PATH.search(line):
            out.append(Finding("sandbox_path", where, n, "a sandbox temp path"))
        for i, term in enumerate(names or (), 1):
            if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", line, re.I):
                out.append(Finding("names", where, n, f"a term from the names file (entry {i})"))
    return out


def _is_run(digits: str) -> bool:
    steps = {int(b) - int(a) for a, b in zip(digits[-10:], digits[-9:], strict=False)}
    return steps <= {1, -9} or steps <= {-1, 9} or len(set(digits)) <= 2


def is_media(path: str) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXT


def list_files(root: Path) -> list[Path]:
    git = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=False,
    )
    if git.returncode == 0 and (root / ".git").exists():
        rels = [r for r in git.stdout.decode("utf-8", "replace").split("\0") if r]
        return sorted(root / r for r in rels if (root / r).is_file())
    found: list[Path] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        found += [Path(dirpath) / f for f in sorted(files)]
    return found


def scan_tree(root: Path, names: Sequence[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in list_files(root):
        rel = path.relative_to(root).as_posix()
        if is_media(rel):
            findings.append(Finding("media", rel, 0, "a media file (PL-08)"))
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", "replace")
        findings += scan_text(text, rel, names)
        for p in commit_msg_check.check_message(text) if path.name.startswith("COMMIT_EDITMSG") else []:
            findings.append(Finding("attribution", rel, 0, p))
    return findings


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", "replace").strip() or "git failed")
    return done.stdout.decode("utf-8", "replace")


def scan_metadata(
    root: Path, names: Sequence[str] | None = None, rev_range: str = "--all", terms: Sequence[str] = ()
) -> list[Finding]:
    """Commit metadata: author and committer identity, and messages (attribution through commit_msg_check)."""
    findings: list[Finding] = []
    meta = _git(root, "log", rev_range, "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e")
    for record in meta.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) < 6:
            continue
        sha, *ident, message = parts
        where = f"commit {sha[:12]}"
        findings += scan_text("\n".join(ident), where + " author", names)
        findings += scan_text(message, where + " message", names)
        findings += [Finding("attribution", where, 0, p) for p in commit_msg_check.check_message(message, terms)]
    return findings


EXCEPTIONS_FILE = HERE / "leak_scan_exceptions.toml"


def line_digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def load_exceptions(path: Path | None = None) -> dict[tuple[str, str], set[str]]:
    """Reviewed exceptions for immutable history: (full commit sha, sha256 of the added line) -> rule ids."""
    path = path or EXCEPTIONS_FILE
    if not path.is_file():
        return {}
    out: dict[tuple[str, str], set[str]] = {}
    for e in tomllib.loads(path.read_text(encoding="utf-8")).get("exception", []):
        sha, rule, digest, reason = e["commit"], e["rule"], e["line_sha256"], e.get("reason", "")
        if not (re.fullmatch(r"[0-9a-f]{40}", sha) and re.fullmatch(r"[0-9a-f]{64}", digest) and rule and reason):
            raise ValueError(f"malformed leak scan exception for commit {sha[:12]}")
        out.setdefault((sha, digest), set()).add(rule)
    return out


def scan_history(
    root: Path,
    names: Sequence[str] | None = None,
    rev_range: str = "--all",
    terms: Sequence[str] = (),
    exceptions: dict[tuple[str, str], set[str]] | None = None,
) -> list[Finding]:
    """Commit metadata, added lines and added media paths across the history.

    An exception waives one rule for one exact added line in one named commit; tree and other commits unaffected.
    """
    exceptions = load_exceptions() if exceptions is None else exceptions
    findings = scan_metadata(root, names, rev_range, terms)
    diff = _git(
        root,
        "log",
        rev_range,
        "--format=\x1e%H",
        "-p",
        "--no-renames",
        "--diff-merges=separate",
        "--no-color",
        "--no-ext-diff",
        "--diff-filter=AM",
        "-U0",
    )
    for chunk in diff.split("\x1e"):
        if not chunk.strip():
            continue
        sha, _, body = chunk.partition("\n")
        added: list[str] = []
        where = f"commit {sha[:12]} diff"
        for line in body.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                waived = exceptions.get((sha, line_digest(line[1:])))
                if waived:
                    findings += [f for f in scan_text(line[1:], where, names, 0) if f.rule not in waived]
                else:
                    added.append(line[1:])
        findings += scan_text("\n".join(added), where, names, 0)
    names_added = _git(root, "log", rev_range, "--format=\x1e%H", "--name-only", "--diff-filter=AMR", "--no-renames")
    for chunk in names_added.split("\x1e"):
        sha, _, files = chunk.strip().partition("\n")
        findings += [
            Finding("media", f"commit {sha[:12]} {f}", 0, "a media file in history (PL-08)")
            for f in files.splitlines()
            if is_media(f)
        ]
    return _dedupe(findings)


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    out = []
    for f in findings:
        key = (f.rule, f.where, f.message)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan the tree (and history) for leaks.")
    ap.add_argument("root", nargs="?", type=Path, default=HERE.parent)
    ap.add_argument("--names-file", type=Path, default=None)
    ap.add_argument("--history", action="store_true", help="also scan every commit")
    ap.add_argument("--range", dest="rev_range", default="--all", help="revision range for --history")
    ap.add_argument("--require-names", action="store_true", help="exit 2 when no names list is available")
    args = ap.parse_args(argv)
    names_path = args.names_file or (Path(env) if (env := os.environ.get("SAATHI_NAMES_FILE")) else None)
    names: list[str] | None = None
    if names_path is not None and names_path.is_file():
        names = load_names(names_path)
    root: Path = args.root
    findings = scan_tree(root, names)
    terms = names or []
    if args.history:
        try:
            findings += scan_history(root, names, args.rev_range, terms)
        except RuntimeError as exc:
            print(f"leak scan: unavailable: history could not be read: {exc}")
            return 2
    else:
        if (root / ".git").exists():  # the plain run also reads commit metadata (LLD build 2, M22)
            try:
                findings += scan_metadata(root, names, args.rev_range, terms)
            except RuntimeError:
                pass  # a repository with no readable history has no metadata to scan
    for f in findings:
        print(f)
    unavailable = names is None
    if unavailable:
        print("names: unavailable (no names file); the private-name check did not run")
    scope = "tree and history" if args.history else "tree and commit metadata"
    print(f"leak scan: {scope}, {len(findings)} finding(s)")
    if findings:
        return 1
    return 2 if unavailable and args.require_names else 0


if __name__ == "__main__":
    sys.exit(main())
