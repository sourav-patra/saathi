#!/usr/bin/env python3
"""Fail the build if a dependency, image or Dockerfile breaks the licence policy.

Policy (POLICY PL-09, PL-10, PL-11; evidence RQ-71):
  ALLOW  MIT, Apache-2.0, BSD-2/3-Clause, ISC, PSF, PostgreSQL, Unlicense, 0BSD,
         Zlib, HPND, CC0 and public domain, and anything carrying one of
         these as an option. OFL (fonts) is allowed in --manifest mode only.
  DENY   GPL, LGPL, AGPL, SSPL, ELv2/Elastic, BUSL, RSAL, Commons Clause,
         non-commercial. No exception, ever.
  REVIEW anything else (e.g. MPL-2.0) and any missing licence. A missing
         licence fails. A weak-copyleft licence passes only through an entry in
         scripts/license_exceptions.toml with licence, reason and date.

Modes (several may be combined; with none given, --packages runs):
  --packages            installed Python distributions, transitive ones included
  --dockerfiles         every Dockerfile under infra/: no ffmpeg, no opusinfo,
                        no `--convert` flag
  --manifest FILE       an image binary list (repeatable). One `name | licence`
                        per line, `#` starts a comment. Covers every binary,
                        library, font and model file an image ships. Packages
                        named in scripts/image_base_allowlist.toml (base layer
                        and C/C++ runtime only) pass with their listed licence.

Usage: uv run python scripts/license_audit.py [--packages] [--dockerfiles]
       [--manifest FILE ...] [--verbose]
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCEPTIONS_FILE = Path(__file__).with_name("license_exceptions.toml")
BASE_ALLOWLIST_FILE = Path(__file__).with_name("image_base_allowlist.toml")

ALLOW = re.compile(
    r"\b(MIT|Apache|BSD|ISC|PSF|Python Software Foundation|PostgreSQL|Unlicense|0BSD|Zlib|HPND|CMU"
    r"|Expat|CC0(-1\.0)?|CC0 1\.0|public[- ]domain)\b",
    re.I,
)
# Fonts only: an image manifest may list OFL fonts; a Python package may not carry OFL.
FONT_ALLOW = re.compile(r"\b(OFL|SIL Open Font)\b", re.I)
# "or later" / "or any later version" is a version clause, not a dual-licence choice.
OR_LATER = re.compile(r"\bor (any )?later\b", re.I)
DENY = re.compile(
    r"(\bA?GPL|\bLGPL|GNU (Lesser |Affero )?General Public|SSPL|Server Side Public|Elastic|\bELv2|"
    r"\bBUSL|Business Source|Commons Clause|Non[- ]?Commercial|\bNC\b|\bRSAL\b|Redis Source Available)",
    re.I,
)
SELF = {"saathi"}

# Programs and libraries an image must never ship (D-46): ffmpeg and its libraries,
# and opusinfo (the voice path uses opusdec and opusenc only).
FORBIDDEN_BINARIES = re.compile(
    r"(ffmpeg|ffprobe|ffplay|\blibav(codec|format|util|filter|device|resample)|\blibsw(scale|resample)|"
    r"\blibpostproc|\blibav-|\bopusinfo|[\w.+-]*-libav[\w.+-]*|\bgstreamer[\w.+-]*libav|\bpyav\b|"
    r"\b(pip3?|uv pip)\s+install\b[^#\n]*(?<![\w.-])av(?![\w.-]))",
    re.I,
)
CONVERT_FLAG = re.compile(r"(^|[\s\"',\[])--convert\b")


def license_strings(dist: metadata.Distribution) -> list[str]:
    md = dist.metadata
    out: list[str] = []
    if expr := md.get("License-Expression"):
        out.append(expr)
    out += [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    lic = md.get("License")
    if lic and len(lic) < 200:  # long values are the full license text; skip them
        out.append(lic)
    return [s for s in out if s and s.upper() not in {"UNKNOWN", "OSI APPROVED"}]


def _split_top_level(expr: str, op: str) -> list[str]:
    parts, depth, cur = [], 0, []
    tokens = re.split(r"(\(|\)|\s+)", expr)
    for tok in tokens:
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        if depth == 0 and tok.strip().upper() == op:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(tok)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _ok(option: str, fonts: bool) -> bool:
    return bool((ALLOW.search(option) or (fonts and FONT_ALLOW.search(option))) and not DENY.search(option))


def spdx_verdict(expr: str, fonts: bool = False) -> str:
    """SPDX: every AND-term must be satisfiable; an OR-group needs one allowed option."""
    results = []
    for term in _split_top_level(expr, "AND"):
        term = term.strip()
        if term.startswith("(") and term.endswith(")"):
            term = term[1:-1]
        options = _split_top_level(term, "OR")
        if any(_ok(o, fonts) for o in options):
            results.append("allow")
        elif any(DENY.search(o) for o in options):
            results.append("deny")
        else:
            results.append("review")
    for v in ("deny", "review"):
        if v in results:
            return v
    return "allow"


def verdict(strings: list[str], expression: str | None = None, fonts: bool = False) -> str:
    if expression:
        return spdx_verdict(expression, fonts)
    joined = " | ".join(strings)
    if not strings:
        return "review"
    # A dual license such as "MIT OR GPL" passes, because we may choose MIT.
    # "GPLv2 or later" is not a choice between licences, so strip it first.
    choice = OR_LATER.sub("", joined)
    if ALLOW.search(joined) or (fonts and FONT_ALLOW.search(joined)):
        if DENY.search(joined) and " OR " not in choice.upper() and "/" not in joined:
            return "deny"
        return "allow"
    if DENY.search(joined):
        return "deny"
    return "review"


# --------------------------------------------------------------------------- #
# Exceptions (PL-11)
# --------------------------------------------------------------------------- #
def load_exceptions(path: Path = EXCEPTIONS_FILE) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text()).get("allow", {})
    return {k.lower(): v if isinstance(v, dict) else {"reason": str(v)} for k, v in raw.items()}


def check_exceptions(exceptions: Mapping[str, Mapping[str, str]]) -> list[str]:
    """An entry needs licence, reason and date, and its licence must not be banned."""
    problems = []
    for name, entry in exceptions.items():
        missing = [f for f in ("licence", "reason", "date") if not str(entry.get(f, "")).strip()]
        if missing:
            problems.append(f"exception {name}: missing {', '.join(missing)}")
        if DENY.search(str(entry.get("licence", ""))) and not _has_allowed_option(str(entry["licence"])):
            problems.append(f"exception {name}: banned licence {entry['licence']!r} can never be excepted")
    return problems


def _norm(lic: str) -> str:
    return re.sub(r"\s+", " ", lic).strip().lower()


def _exception_matches(entry: Mapping[str, str], strings: Sequence[str]) -> bool:
    """An exception covers a package only for the licence it names."""
    want = _norm(str(entry.get("licence", "")))
    return bool(want) and any(want == _norm(s) or want in _norm(s) for s in strings)


def _has_allowed_option(expr: str) -> bool:
    return spdx_verdict(expr) == "allow"


# --------------------------------------------------------------------------- #
# Image base allowlist (owner decision, option (a)): --manifest mode only
# --------------------------------------------------------------------------- #
BASE_REASONS = frozenset(
    {
        "unmodified distribution base layer, not linked into or distributed as part of Saathi code",
        "GCC runtime exception",
    }
)
_BASE_FIELDS = {"name", "licence", "reason", "reported"}
_PKG_NAME = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")


def load_base_allowlist(path: Path = BASE_ALLOWLIST_FILE) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Return (entries by package name, problems). A malformed entry is rejected, never used."""
    if not path.exists():
        return {}, []
    entries: dict[str, dict[str, str]] = {}
    problems: list[str] = []
    for i, e in enumerate(tomllib.loads(path.read_text()).get("package", []), 1):
        if not isinstance(e, dict):
            problems.append(f"base allowlist entry {i}: not a table")
            continue
        name = str(e.get("name", "")).strip()
        where = f"base allowlist entry {i} ({name or 'no name'})"
        bad = [f"missing {f}" for f in ("name", "licence", "reason") if not str(e.get(f, "")).strip()]
        bad += [f"unknown field {f}" for f in sorted(set(e) - _BASE_FIELDS)]
        if name and not _PKG_NAME.match(name):
            bad.append("name must be one exact package name (no pattern, no :arch)")
        if e.get("reason") and e["reason"] not in BASE_REASONS:
            bad.append("reason is not one of the fixed reasons")
        if name in entries:
            bad.append("duplicate entry")
        if bad:
            problems.append(f"{where}: {'; '.join(bad)}")
            continue
        entries[name] = {k: str(v) for k, v in e.items()}
    return entries, problems


def _base_listed(name: str, lic: str, base: Mapping[str, Mapping[str, str]]) -> bool:
    """A package passes only if named exactly and the image reports the licence the entry gives."""
    entry = base.get(name.split(":", 1)[0].lower())
    if not entry:
        return False
    reported = _norm(lic or "UNKNOWN")
    return reported in {_norm(entry["licence"]), _norm(entry.get("reported", ""))}


# --------------------------------------------------------------------------- #
# Modes: each returns a list of failure lines (empty means pass)
# --------------------------------------------------------------------------- #
def audit_packages(
    dists: Iterable[metadata.Distribution],
    exceptions: Mapping[str, Mapping[str, str]],
    verbose: bool = False,
) -> list[str]:
    failures: list[str] = check_exceptions(exceptions)
    for dist in sorted(dists, key=lambda d: (d.metadata["Name"] or "").lower()):
        name = (dist.metadata["Name"] or "").lower()
        if name in SELF:
            continue
        strings = license_strings(dist)
        v = verdict(strings, dist.metadata.get("License-Expression"))
        # Only a weak-copyleft ("review") package with some licence metadata may be
        # excepted. A banned licence and a missing licence always fail.
        if v == "review" and strings and name in exceptions and _exception_matches(exceptions[name], strings):
            v = "excepted"
        if verbose or v not in ("allow", "excepted"):
            print(f"{v:9} {name}=={dist.version}  [{'; '.join(strings) or 'no license metadata'}]")
        if v in ("deny", "review"):
            failures.append(f"package {name}: {v} [{'; '.join(strings) or 'no license metadata'}]")
    return failures


def audit_dockerfile(path: Path) -> list[str]:
    """No ffmpeg, opusinfo or `--convert` anywhere in the file, comments included."""
    failures = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if m := FORBIDDEN_BINARIES.search(line):
            failures.append(f"{path}:{n}: forbidden program or library '{m.group(0)}'")
        if CONVERT_FLAG.search(line):
            failures.append(f"{path}:{n}: '--convert' flag (it needs ffmpeg)")
    return failures


def find_dockerfiles(infra: Path) -> list[Path]:
    return sorted(p for p in infra.rglob("Dockerfile*") if p.is_file())


def audit_dockerfiles(infra: Path) -> list[str]:
    files = find_dockerfiles(infra)
    if not files:
        return [f"no Dockerfile found under {infra}"]
    return [f for p in files for f in audit_dockerfile(p)]


def audit_manifest(manifest: Path, base: Mapping[str, Mapping[str, str]] | None = None) -> list[str]:
    """Image binary list: `name | licence`. Fails on ffmpeg, opusinfo, GPL, LGPL, unknown.

    `base` is the named image base allowlist; only an exact name with its listed licence passes.
    """
    base = base or {}
    if not manifest.is_file():
        return [f"{manifest}: manifest not found"]
    failures, seen = [], 0
    for n, raw in enumerate(manifest.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        seen += 1
        name, sep, lic = (s.strip() for s in line.partition("|"))
        where = f"{manifest}:{n}: {name}"
        if m := FORBIDDEN_BINARIES.search(name):
            failures.append(f"{where}: forbidden program or library '{m.group(0)}'")
        if _base_listed(name, lic if sep else "", base):
            continue
        if not sep or not lic or lic.upper() in {"UNKNOWN", "NONE", "N/A"}:
            failures.append(f"{where}: missing or unknown licence")
            continue
        v = verdict([lic], lic, fonts=True)
        if v != "allow":
            failures.append(f"{where}: {v} licence [{lic}]")
    if not seen:
        failures.append(f"{manifest}: manifest lists nothing")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packages", action="store_true")
    ap.add_argument("--dockerfiles", action="store_true")
    ap.add_argument("--manifest", action="append", default=[], type=Path)
    ap.add_argument("--infra", type=Path, default=ROOT / "infra", help="directory searched by --dockerfiles")
    ap.add_argument("--exceptions", type=Path, default=EXCEPTIONS_FILE)
    ap.add_argument("--base-allowlist", type=Path, default=BASE_ALLOWLIST_FILE)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    run_packages = args.packages or not (args.dockerfiles or args.manifest)
    failures: list[str] = []
    if run_packages:
        failures += audit_packages(metadata.distributions(), load_exceptions(args.exceptions), args.verbose)
    if args.dockerfiles:
        failures += audit_dockerfiles(args.infra)
    if args.manifest:
        base, problems = load_base_allowlist(args.base_allowlist)
        failures += problems
        for m in args.manifest:
            failures += audit_manifest(m, base)

    if failures:
        print("\n".join(f"  {f}" for f in failures))
        print("\nLICENSE AUDIT FAILED")
        print("Remove the dependency, or (for 'review') justify it in scripts/license_exceptions.toml.")
        return 1
    print("license audit passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
