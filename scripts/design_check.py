#!/usr/bin/env python3
"""Checks on the design set. Run from the repository root: python3 scripts/design_check.py [--root DIR]

1. coverage: every RQ and PL id of the contracts appears in the Serves column of a TQ row
2. pins: every `upstream:` pin equals sha256sum of the named file
3. index: every 12 character prefix in 00-INDEX.md equals sha256sum; every design file is listed
4. locks: the current sha256 of every indexed file appears in docs/LOCKS.md
5. sizes and closing line: contract under 24000 bytes (warn at 22500), record 16000, index 2000
6. dashes: no em or en dash in a design file
7. figure: edges of figures/architecture.svg equal the edge table, endpoints exist
8. lld: every Phase 0 id of the PRD phase table is in the Serves cell of a module row (`| M01 |`) and of a test row
   (`| LT-01 |`) of the LLD files, and every id in those cells exists in a contract
Index paths: a name with a slash is relative to the repository root; a name without one is in docs/design/.
Exit 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_ids(cell: str) -> set[str]:
    """RQ-79, 35 means RQ-79 and RQ-35; RQ-58 to 62 is a range; PL-27 (RQ-26) switches prefix twice."""
    out: set[str] = set()
    prefix, last, rng = None, None, False
    for m in re.finditer(r"(RQ|PL)-(\d+[ab]?)|\b(\d+[ab]?)\b|\bto\b", cell):
        if m.group(0) == "to":
            rng = True
            continue
        if m.group(1):
            prefix, num = m.group(1), m.group(2)
        else:
            if prefix is None:
                continue
            num = m.group(3)
        if rng and last is not None and num.isdigit() and last.isdigit():
            for n in range(int(last) + 1, int(num) + 1):
                out.add(f"{prefix}-{n}")
        out.add(f"{prefix}-{num}")
        last, rng = num, False
    return out


def wide_ids(cell: str) -> set[str]:
    """Like parse_ids, but keeps zero padding: PL-01 to PL-05 gives PL-01 .. PL-05."""
    out: set[str] = set()
    prefix, last, width, rng = None, None, 0, False
    for m in re.finditer(r"(RQ|PL)-(\d+[ab]?)|\b(\d+[ab]?)\b|\bto\b", cell):
        if m.group(0) == "to":
            rng = True
            continue
        if m.group(1):
            prefix, num = m.group(1), m.group(2)
        else:
            if prefix is None:
                continue
            num = m.group(3)
        if rng and last is not None and num.isdigit() and last.isdigit():
            for n in range(int(last) + 1, int(num)):
                out.add(f"{prefix}-{n:0{width}d}")
        out.add(f"{prefix}-{num}")
        width = len(num) if num.isdigit() else width
        last, rng = num, False
    return out


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def resolve(root: Path, name: str) -> Path:
    return root / name if "/" in name else root / "docs" / "design" / name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    root = Path(ap.parse_args().root).resolve()
    D = root / "docs" / "design"
    fails: list[str] = []
    warns: list[str] = []

    def fail(msg: str) -> None:
        fails.append(msg)

    files = sorted(D.glob("*.md"))

    # 1 coverage
    req: set[str] = set()
    for f in files:
        if f.name.startswith(("PRD-", "SPEC-", "POLICY-")):
            for line in f.read_text().splitlines():
                m = re.match(r"^\| (RQ-\d+[ab]?|PL-\d+) \|", line)
                if m:
                    req.add(m.group(1))
    checks = [f for f in files if f.name.startswith("TRD-saathi-checks-")]
    mapped: set[str] = set()
    tq = 0
    for f in checks:
        for line in f.read_text().splitlines():
            if re.match(r"^\| TQ-\d+ \|", line):
                tq += 1
                mapped |= parse_ids(line.split("|")[3])
    missing = sorted(req - mapped, key=lambda s: (s[:2], int(re.sub(r"\D", "", s)), s))
    unknown = sorted(mapped - req)
    rq = sorted(i for i in req if i.startswith("RQ"))
    pl = sorted(i for i in req if i.startswith("PL"))
    print(f"coverage: {len(rq)} RQ ids and {len(pl)} PL ids in the contracts; {tq} TQ rows; "
          f"{len(req) - len(missing)} mapped, {len(missing)} unmapped, {len(unknown)} unknown")
    if missing:
        fail("unmapped ids: " + ", ".join(missing))
    if unknown:
        fail("ids in TQ rows that no contract defines: " + ", ".join(unknown))
    if not tq:
        fail("no TQ rows found")

    # 2 pins
    npins = 0
    for f in files:
        text = f.read_text()
        fm = text.split("\n---\n", 1)[0] if text.startswith("---") else ""
        for line in fm.splitlines():
            if line.startswith("upstream:"):
                for name, h in re.findall(r"(\S+\.md) sha256 ([0-9a-f]{64})", line):
                    npins += 1
                    t = D / name
                    if not t.exists():
                        fail(f"{f.name}: pin names missing file {name}")
                    elif sha(t) != h:
                        fail(f"{f.name}: pin of {name} is stale")
    print(f"pins: {npins} upstream pins checked")

    # 3 index
    idx = root / "00-INDEX.md"
    listed: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for line in idx.read_text().splitlines():
        m = re.match(r"^\| (\w+) \| (\S+) \| ([0-9a-f]{12}) \|", line)
        if m:
            full = resolve(root, m.group(2)).relative_to(root).as_posix()
            listed[full] = m.group(3)
            kinds[full] = m.group(1)
            p = root / full
            if not p.exists():
                fail(f"index path missing: {m.group(2)}")
            elif sha(p)[:12] != m.group(3):
                fail(f"index prefix stale: {m.group(2)}")
    for f in files:
        if f"docs/design/{f.name}" not in listed:
            fail(f"design file not in the index: {f.name}")
    print(f"index: {len(listed)} rows checked")

    # 4 locks
    locks = (root / "docs" / "LOCKS.md").read_text()
    for path in listed:
        if kinds[path] not in ("contract", "record") or path.endswith("LOCKS.md"):
            continue
        if sha(root / path) not in locks:
            fail(f"current hash of {path} has no row in docs/LOCKS.md")

    # 5 sizes, closing line; 6 dashes
    for p in [*files, idx, root / "docs" / "LOCKS.md"]:
        b = p.read_bytes()
        text = b.decode()
        kind = re.search(r"^kind: (\w+)", text, re.M)
        limit = 2000 if p == idx else 16000 if (kind and kind.group(1) == "record") else 24000 if kind else None
        if limit and len(b) > limit:
            fail(f"{p.name}: {len(b)} bytes over {limit}")
        elif limit == 24000 and len(b) > 22500:
            warns.append(f"{p.name}: {len(b)} bytes, warn at 22500")
        last = text.rstrip("\n").splitlines()[-1]
        if not last.startswith("Skipped or not checked:"):
            fail(f"{p.name}: last line is not the skipped line")
        if "—" in text or "–" in text:
            fail(f"{p.name}: em or en dash")
    print("sizes, closing lines, dashes: checked")

    # 7 figure
    core = next((f for f in files if f.name == "TRD-saathi-2026-11-09.md"), None)
    svg = D / "figures" / "architecture.svg"
    if core and svg.exists():
        ctext = core.read_text()
        table = {}
        for line in ctext.splitlines():
            m = re.match(r"^\| (E\d+) \| ([^|]+) \| ([^|]+) \|", line)
            if m:
                table[m.group(1)] = (set(m.group(2).split()), set(m.group(3).split()))
        comps = set(re.findall(r"^\| ((?:C|X)\d+) \|", ctext, re.M))
        outside = set(re.findall(r"\b(A\d|D\d)\b", ctext.split("Outside parties:", 1)[1].split("\n", 1)[0]))
        zones = {f"Z{i}" for i in range(1, 6)}
        root_el = ET.parse(svg).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        ids = {e.get("id") for e in root_el.iter() if e.get("id")}
        edges = {e.get("id"): (set(e.get("data-from", "").split()), set(e.get("data-to", "").split()))
                 for e in root_el.iter(ns + "g") if re.fullmatch(r"E\d+", e.get("id", ""))}
        if set(edges) != set(table):
            fail(f"figure edges differ from the edge table: {sorted(set(edges) ^ set(table))}")
        for eid, (a, b) in edges.items():
            if eid in table and (a, b) != table[eid]:
                fail(f"edge {eid} endpoints differ: figure {a}->{b}, table {table[eid]}")
            for n in a | b:
                if n not in comps | outside | zones:
                    fail(f"edge {eid}: endpoint {n} is not in the components table, outside parties or zones")
                if n not in ids:
                    fail(f"edge {eid}: endpoint {n} has no element in the figure")
        undrawn = sorted(comps - ids)
        print(f"figure: {len(edges)} edges match the edge table; {len(comps & ids)} of {len(comps)} components drawn; "
              f"not drawn: {', '.join(undrawn) or 'none'}")
        if "Z6" in ctext and not outside:
            fail("outside parties line missing")
    else:
        fail("core TRD or architecture.svg missing")

    # 8 lld coverage
    prd = next((f for f in files if f.name.startswith("PRD-")), None)
    lld = [f for f in files if f.name.startswith("LLD-")]
    if prd and lld:
        phase0: set[str] = set()
        for line in prd.read_text().splitlines():
            if line.startswith("| 0 Foundation |"):
                phase0 = wide_ids(cells(line)[2])
        mod: set[str] = set()
        tst: set[str] = set()
        nm = nt = 0
        for f in lld:
            for line in f.read_text().splitlines():
                if re.match(r"^\| M\d+ \|", line):
                    nm += 1
                    mod |= wide_ids(cells(line)[2])
                elif re.match(r"^\| LT-\d+ \|", line):
                    nt += 1
                    tst |= wide_ids(cells(line)[5])
        if not phase0:
            fail("lld: no Phase 0 row found in the PRD phase table")
        for what, got in (("module", mod), ("test", tst)):
            miss = sorted(phase0 - got, key=lambda i: (i[:2], i))
            if miss:
                fail(f"lld: Phase 0 ids with no {what} row: " + ", ".join(miss))
            bad = sorted(got - req)
            if bad:
                fail(f"lld: {what} rows name ids no contract defines: " + ", ".join(bad))
        bad0 = sorted(phase0 - req)
        if bad0:
            fail("lld: the PRD Phase 0 row names ids no contract defines: " + ", ".join(bad0))
        print(f"lld: {len(phase0)} Phase 0 ids; {nm} module rows and {nt} test rows; "
              f"{len(phase0 - mod)} without a module, {len(phase0 - tst)} without a test")
    elif lld:
        fail("lld files exist but the PRD is missing")

    for w in warns:
        print("warn:", w)
    for m in fails:
        print("FAIL:", m)
    print("result:", "FAIL" if fails else "OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
