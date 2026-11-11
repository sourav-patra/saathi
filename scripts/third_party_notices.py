#!/usr/bin/env python3
"""Third-party notices: python3 scripts/third_party_notices.py [--check] [--dir DIR] [--out FILE] [IMAGE...]

Builds THIRD-PARTY-NOTICES.md from the image-audit manifests in DIR (default: the current directory):
- `manifest-<image>.txt`: `name | licence` per line (the file the licence audit reads);
- `versions-<image>.txt`: `name | version` per line.
IMAGE defaults to every image in IMAGES. A package named in scripts/image_base_allowlist.toml with a GPL-family
licence is marked as owner-approved.

Without --check the file is (re)written and every named image needs both manifests. With --check the section of
each image whose manifests exist is compared with the committed file; an image without a manifest is skipped (the
manifest exists only in the image-audit job). Exit 1 when a section is stale or missing.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD-PARTY-NOTICES.md"
ALLOWLIST = ROOT / "scripts" / "image_base_allowlist.toml"
IMAGES = ("asr", "speech")
GPL_FAMILY = re.compile(r"\b(?:A|L)?GPL", re.I)

HEADER = """# Third-party notices

The container images of this repository are built for the owner's own server and are not published. This file lists
every distribution package in each image, as the image-audit job reads it from the built image.

- The licence text of each package is in the image under `/usr/share/doc/<pkg>/copyright`.
- The matching source is available from the Debian source packages of the listed versions.
- A package marked "owner-approved" carries a GPL-family licence and is named one by one in
  `scripts/image_base_allowlist.toml`. It runs as an unmodified distribution package and is not linked into Saathi code.

Regenerate this file with `uv run python scripts/third_party_notices.py` from the image-audit manifests.
"""

FOOTER = "\nSkipped or not checked: the Python packages of the project itself (see `scripts/license_audit.py --packages`).\n"


def begin(image: str) -> str:
    return f"<!-- image: {image} -->"


def end(image: str) -> str:
    return f"<!-- end image: {image} -->"


def read_pairs(path: Path) -> dict[str, str]:
    """`name | value` lines to a dict; the `:arch` suffix is dropped, comments and blanks skipped."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, value = (s.strip() for s in line.partition("|"))
        out[name.split(":", 1)[0]] = value or "UNKNOWN"
    return out


def approved_gpl(path: Path = ALLOWLIST) -> set[str]:
    if not path.exists():
        return set()
    pkgs = tomllib.loads(path.read_text(encoding="utf-8")).get("package", [])
    return {str(p["name"]) for p in pkgs if GPL_FAMILY.search(str(p.get("licence", "")))}


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def section(image: str, licences: Mapping[str, str], versions: Mapping[str, str], approved: set[str]) -> str:
    rows = [
        begin(image),
        f"## Image `{image}`",
        "",
        "| Package | Version | Licence | Note |",
        "|---|---|---|---|",
    ]
    for name in sorted(set(licences) | set(versions)):
        note = "owner-approved" if name in approved else ""
        rows.append(f"| {name} | {_cell(versions.get(name, 'UNKNOWN'))} | {_cell(licences.get(name, 'UNKNOWN'))} | {note} |")
    rows.append(end(image))
    return "\n".join(rows) + "\n"


def load_section(image: str, folder: Path, approved: set[str]) -> str | None:
    m, v = folder / f"manifest-{image}.txt", folder / f"versions-{image}.txt"
    if not (m.is_file() and v.is_file()):
        return None
    return section(image, read_pairs(m), read_pairs(v), approved)


def render(sections: Sequence[str]) -> str:
    return HEADER + "".join("\n" + s for s in sections) + FOOTER


def extract(text: str, image: str) -> str | None:
    a, b = text.find(begin(image)), text.find(end(image))
    if a < 0 or b < a:
        return None
    return text[a : b + len(end(image))] + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dir", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    ap.add_argument("images", nargs="*", default=list(IMAGES))
    args = ap.parse_args(argv)
    approved = approved_gpl(args.allowlist)

    if not args.check:
        sections = []
        for image in args.images:
            s = load_section(image, args.dir, approved)
            if s is None:
                print(f"notices: manifest-{image}.txt or versions-{image}.txt missing in {args.dir}")
                return 1
            sections.append(s)
        args.out.write_text(render(sections), encoding="utf-8")
        print(f"notices: wrote {args.out} ({len(sections)} image(s))")
        return 0

    committed = args.out.read_text(encoding="utf-8") if args.out.is_file() else ""
    failures = 0
    for image in args.images:
        fresh = load_section(image, args.dir, approved)
        if fresh is None:
            print(f"notices: {image}: no manifest here, skipped")
            continue
        if extract(committed, image) != fresh:
            print(f"notices: {image}: {args.out.name} is stale; regenerate it from the image-audit manifests")
            failures += 1
        else:
            print(f"notices: {image}: up to date")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
