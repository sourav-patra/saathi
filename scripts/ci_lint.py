#!/usr/bin/env python3
"""CI lint (RQ-72a): python3 scripts/ci_lint.py PATH...

PATH is a workflow file or a directory searched for *.yml and *.yaml. Fails (exit 1) on:
- no workflow file found at all (a check on nothing proves nothing);
- a `uses:` value (block form or flow form such as `- {uses: x@v4}`) that does not end in a
  40 hex commit digest (`owner/repo[/path]@<40 hex>`); a local action (`./path`) is exempt, and a `docker://`
  reference must end in `@sha256:<64 hex>`;
- an `image:` value with no `@sha256:<64 hex>` digest (service containers, TQ-61 b).
A trailing `# <tag>` comment on a `uses:` line is expected (build part section 2); a missing one is a warning.
The scan is line based so that it needs no YAML library.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

USES = re.compile(r"\buses:\s*(?P<value>[^\s#,}]+)")
IMAGE = re.compile(r"^\s*(?:-\s+)?image:\s*(?P<value>[^\s#]+)")
ACTION_DIGEST = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
IMAGE_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def code_part(line: str) -> str | None:
    """The line without a trailing comment, or None for a comment-only line."""
    code = re.split(r"(?:^|\s)#", line, maxsplit=1)[0]
    return code if code.strip() else None


def check_actions_pinned(workflow: Path) -> list[str]:
    """Return one message per `uses:` line without a 40 hex digest (and per unpinned `image:` line)."""
    out: list[str] = []
    for n, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        line = code_part(line) or ""
        if m := USES.search(line):
            value = m.group("value").strip("'\"")
            if value.startswith("./"):
                continue
            if value.startswith("docker://"):
                if not IMAGE_DIGEST.search(value):
                    out.append(f"{workflow}:{n}: docker action {value} has no @sha256 digest")
            elif not ACTION_DIGEST.match(value):
                out.append(f"{workflow}:{n}: uses {value} is not pinned to a 40 hex commit digest")
        elif m := IMAGE.match(line):
            value = m.group("value").strip("'\"")
            if not IMAGE_DIGEST.search(value):
                out.append(f"{workflow}:{n}: image {value} has no @sha256 digest")
    return out


def tag_warnings(workflow: Path) -> list[str]:
    out = []
    for n, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        code = code_part(line)
        m = USES.search(code) if code else None
        if not m or m.group("value").strip("'\"").startswith(("./", "docker://")):
            continue
        if len(code.rstrip()) == len(line.rstrip()):
            out.append(f"{workflow}:{n}: warn: no tag comment after the digest")
    return out


def collect(paths: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files += sorted([*p.rglob("*.yml"), *p.rglob("*.yaml")])
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(str(p))
    return files


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: ci_lint.py PATH...", file=sys.stderr)
        return 2
    files = collect([Path(a) for a in args])
    if not files:
        print("ci lint: no workflow files found; a check on nothing proves nothing")
        return 1
    findings: list[str] = []
    for f in files:
        findings += check_actions_pinned(f)
        for w in tag_warnings(f):
            print(w)
    for msg in findings:
        print(msg)
    print(f"ci lint: {len(files)} file(s), {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
