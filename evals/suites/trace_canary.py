"""Trace canary suite: personal canaries go through the real exporter into a file sink; a leak is a canary in a line.

Twenty seeded cases. Each hands the exporter what a careless call site could pass: a transcript under an unlisted key,
a whole node output, an ID number in a listed field, a transcript longer than the string limit in a listed field, bytes,
and a short pattern-free word (a name) in a coded field.
The gate is zero canaries outside `raw_text` (the text itself is the only place a canary may live, and it is never given
to the exporter here). Canaries are generated from the manifest seed at load; no ID-shaped value is committed.
"""

from __future__ import annotations

import json
import random
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from evals.dataset import Dataset, EvalError, read_manifest, sha256_bytes
from evals.results import Rate, Results
from evals.suites.redaction import _make
from saathi.clock import FakeClock
from saathi.trace import JsonlExporter

N_CASES = 20
KINDS = ("aadhaar", "pan", "phone", "card")
SHAPES = ("unlisted_key", "node_output", "listed_field", "long_transcript", "bytes_value", "short_word")
_SHORT = ("ravindra", "sunita", "dawai", "mohalla", "chachaji")  # pattern-free words no redactor can know
_WORDS = ("mujhe", "kal", "dawai", "chahiye", "doctor", "ghar", "paise", "beta", "bank", "kaam", "subah", "shaam")
_AT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def generate(seed: int, n: int) -> list[dict]:
    cases = []
    for i in range(n):
        rng = random.Random(f"{seed}-{i}")
        kind = KINDS[i % len(KINDS)]
        text, canary = _make(kind, rng, i)
        shape = SHAPES[i % len(SHAPES)]
        if shape == "short_word":
            canary = rng.choice(_SHORT)
        marker = f"CANARY-{rng.getrandbits(64):016x}"
        words = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(14, 24)))
        cases.append(
            {
                "id": f"tc-{i + 1:02d}", "kind": kind, "shape": shape, "marker": marker,
                "canary": canary, "text": f"{words} {marker} {text}",
            }
        )  # fmt: skip
    return cases


def seal(cases: list[dict]) -> str:
    return sha256_bytes(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _fields(case: dict) -> dict:
    shape, text = case["shape"], case["text"]
    if shape == "unlisted_key":
        return {"transcript": text, "count": 1}
    if shape == "node_output":
        return {"state": {"summary": text, "items": [text]}, "count": 1}
    if shape == "listed_field":
        return {"reason": case["canary"], "intent": case["canary"]}  # an ID number where a coded value belongs
    if shape == "short_word":
        return {"reason": case["canary"], "intent": case["canary"], "stage": case["canary"]}  # a name where a code goes
    if shape == "long_transcript":
        return {"reason": text, "state": text}
    return {"reason": text.encode(), "count": 1}


def _alnum(text: str) -> str:
    folded = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    return "".join(ch for ch in folded if ch.isalnum())


class TraceCanarySuite:
    name = "trace_canary"

    def load(self, root: Path) -> Dataset:
        manifest = read_manifest(root / self.name, self.name)
        if manifest.seed is None:
            raise EvalError("dataset trace_canary: seed must be set")
        cases = generate(manifest.seed, manifest.n)
        digest = seal(cases)
        if digest != manifest.sha256:
            raise EvalError("dataset trace_canary: generated cases do not match the sealed sha256")
        return Dataset(name=self.name, manifest=manifest, cases=cases, sha256=digest)

    def run(self, ds: Dataset) -> Results:
        leaks: dict[str, int] = {s: 0 for s in SHAPES}
        total: dict[str, int] = {s: 0 for s in SHAPES}
        failures: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            exporter = JsonlExporter(Path(tmp) / "traces", FakeClock(_AT))
            for case in ds.cases:
                total[case["shape"]] += 1
                before = exporter.spans
                exporter.span("eval.case", **_fields(case))
                if exporter.spans != before + 1:
                    raise EvalError("trace_canary: the exporter wrote no line for a case")
            body = "".join(p.read_text(encoding="utf-8") for p in Path(tmp, "traces").glob("trace-*.jsonl"))
        lines = body.splitlines()
        if len(lines) != len(ds.cases):
            raise EvalError("trace_canary: one line per case was expected")
        for case, line in zip(ds.cases, lines, strict=True):
            hay = _alnum(line)
            needles = (case["marker"], _alnum(case["canary"]))
            if any(n in line or _alnum(n) in hay for n in needles):
                leaks[case["shape"]] += 1
                failures.append(case["id"])
        rates = [Rate(f"{s} leaks", leaks[s], total[s], target_n=total[s], max_k=0) for s in SHAPES if total[s]]
        state = "verified" if all(r.gate_ok for r in rates) else "invalid"
        return Results(
            suite=self.name, state=state, rates=rates, dataset_sha256=ds.sha256, n=len(ds.cases),
            author_role=ds.manifest.author_role, failures=failures,
        )
