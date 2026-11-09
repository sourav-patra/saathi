#!/usr/bin/env python3
"""Fail the build if any installed Python dependency has a disallowed license.

Policy (see docs/ARCHITECTURE.md § License policy):
  ALLOW  MIT, Apache-2.0, BSD-2/3-Clause, ISC, PSF, PostgreSQL, Unlicense, 0BSD,
         Zlib, HPND, and anything carrying one of these as an option
  DENY   GPL, LGPL, AGPL, SSPL, Elastic, BUSL, Commons Clause, non-commercial
  REVIEW anything else (e.g. MPL-2.0); it must be listed with a justification
         in scripts/license_exceptions.toml

Usage: uv run python scripts/license_audit.py [--verbose]
"""

from __future__ import annotations

import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path

ALLOW = re.compile(
    r"\b(MIT|Apache|BSD|ISC|PSF|Python Software Foundation|PostgreSQL|Unlicense|0BSD|Zlib|HPND|CMU)\b",
    re.I,
)
DENY = re.compile(
    r"(\bA?GPL|\bLGPL|GNU (Lesser |Affero )?General Public|SSPL|Server Side Public|Elastic|"
    r"\bBUSL|Business Source|Commons Clause|Non[- ]?Commercial|\bNC\b|RSAL)",
    re.I,
)
SELF = {"saathi"}


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


def spdx_verdict(expr: str) -> str:
    """SPDX: every AND-term must be satisfiable; an OR-group needs one allowed option."""
    results = []
    for term in _split_top_level(expr, "AND"):
        term = term.strip()
        if term.startswith("(") and term.endswith(")"):
            term = term[1:-1]
        options = _split_top_level(term, "OR")
        if any(ALLOW.search(o) and not DENY.search(o) for o in options):
            results.append("allow")
        elif any(DENY.search(o) for o in options):
            results.append("deny")
        else:
            results.append("review")
    for v in ("deny", "review"):
        if v in results:
            return v
    return "allow"


def verdict(strings: list[str], expression: str | None = None) -> str:
    if expression:
        return spdx_verdict(expression)
    joined = " | ".join(strings)
    if not strings:
        return "review"
    # A dual license such as "MIT OR GPL" passes, because we may choose MIT.
    if ALLOW.search(joined):
        if DENY.search(joined) and " OR " not in joined.upper() and "/" not in joined:
            return "deny"
        return "allow"
    if DENY.search(joined):
        return "deny"
    return "review"


def main() -> int:
    verbose = "--verbose" in sys.argv
    exceptions_file = Path(__file__).with_name("license_exceptions.toml")
    exceptions = tomllib.loads(exceptions_file.read_text()).get("allow", {}) if exceptions_file.exists() else {}

    failures: list[str] = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = (dist.metadata["Name"] or "").lower()
        if name in SELF:
            continue
        strings = license_strings(dist)
        v = verdict(strings, dist.metadata.get("License-Expression"))
        if v != "allow" and name in exceptions:
            v = "excepted"
        if verbose or v not in ("allow", "excepted"):
            print(f"{v:9} {name}=={dist.version}  [{'; '.join(strings) or 'no license metadata'}]")
        if v in ("deny", "review"):
            failures.append(name)

    if failures:
        print(f"\nLICENSE AUDIT FAILED for: {', '.join(failures)}")
        print("Remove the dependency, or (for 'review') justify it in scripts/license_exceptions.toml.")
        return 1
    print("license audit passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
