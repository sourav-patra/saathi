"""Redaction suite: seeded synthetic canaries per ID type; a leak is a canary still readable after `redact`.

Canaries are generated from the manifest seed when the dataset loads, so no ID-shaped value is committed. The
manifest seals the sha256 of the generated cases; a change to the generator or the seed changes the seal and fails.
"""

from __future__ import annotations

import json
import random
import re
import string
from pathlib import Path

from evals.dataset import Dataset, EvalError, read_manifest, sha256_bytes
from evals.results import Rate, Results
from saathi.guards.pii import RestrictedDataLeft, assert_clean, luhn_valid, redact, verhoeff_valid

TYPES = ("aadhaar", "pan", "card", "phone", "voter", "passport", "email", "upi", "otp")
PER_TYPE = 30


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _letters(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(n))


def _with_check(rng: random.Random, payload: str, valid) -> str:
    return next(payload + d for d in "0123456789" if valid(payload + d))


_DEV = str.maketrans("0123456789", "०१२३४५६७८९")


def _make(kind: str, rng: random.Random, i: int) -> tuple[str, str]:
    """Return (text, canary). The canary is the sensitive substring that must not survive."""
    style = i % 3
    if kind == "aadhaar":
        raw = _with_check(rng, rng.choice("23456789") + _digits(rng, 10), verhoeff_valid)
        canary = (raw, f"{raw[:4]} {raw[4:8]} {raw[8:]}", f"{raw[:4]}-{raw[4:8]}-{raw[8:]}")[style]
        text = f"mera number {canary} hai"
        if i % 10 == 9:
            canary = canary.translate(_DEV)
            text = f"mera number {canary} hai"
        return text, canary
    if kind == "pan":
        canary = _letters(rng, 5) + _digits(rng, 4) + _letters(rng, 1)
        return (f"PAN {canary} bhej do", f"my pan is {canary}", f"{canary}")[style], canary
    if kind == "card":
        raw = _with_check(rng, rng.choice("456") + _digits(rng, 14), luhn_valid)
        groups = [raw[j : j + 4] for j in range(0, 16, 4)]
        canary = (raw, " ".join(groups), "-".join(groups))[style]
        return f"card {canary} expiry soon", canary
    if kind == "phone":
        raw = rng.choice("6789") + _digits(rng, 9)
        canary = (raw, f"+91 {raw}", f"+91-{raw[:5]}-{raw[5:]}")[style]
        return f"call me on {canary} please", canary
    if kind == "voter":
        canary = _letters(rng, 3) + _digits(rng, 7)
        return f"voter id {canary}", canary
    if kind == "passport":
        canary = _letters(rng, 1) + _digits(rng, 7)
        return f"passport {canary} expires", canary
    if kind == "email":
        local = "".join(rng.choice(string.ascii_lowercase) for _ in range(6)) + _digits(rng, 2)
        canary = f"{local}@example.com"
        return f"mail {canary} for the form", canary
    if kind == "upi":
        local = "".join(rng.choice(string.ascii_lowercase) for _ in range(6)) + _digits(rng, 2)
        canary = f"{local}@testbank"
        return f"pay to {canary} now", canary
    if kind == "otp":
        canary = _digits(rng, rng.choice((4, 6)))
        return (f"OTP is {canary}", f"your code {canary}", f"ओटीपी {canary}")[style], canary
    raise ValueError(kind)


def generate(seed: int, per_type: int) -> list[dict]:
    cases = []
    for kind in TYPES:
        rng = random.Random(f"{seed}-{kind}")
        for i in range(per_type):
            text, canary = _make(kind, rng, i)
            cases.append({"id": f"{kind}-{i + 1:02d}", "kind": kind, "text": text, "canary": canary})
    return cases


def seal(cases: list[dict]) -> str:
    return sha256_bytes(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _alnum(text: str) -> str:
    folded = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    return re.sub(r"[\s\-]", "", folded)


def _leaks(canary: str, redacted: str) -> bool:
    """A leak is any 4-character window of the canary surviving, or a restricted pattern still in the output."""
    needle, out = _alnum(canary), _alnum(redacted)
    windows = [needle[i : i + 4] for i in range(max(len(needle) - 3, 1))]
    if any(w in out for w in windows):
        return True
    try:
        assert_clean(redacted)
    except RestrictedDataLeft:
        return True
    return False


class RedactionSuite:
    name = "redaction"

    def load(self, root: Path) -> Dataset:
        manifest = read_manifest(root / self.name, self.name)
        if manifest.seed is None or not manifest.per_type:
            raise EvalError("dataset redaction: seed and per_type must be set")
        cases = generate(manifest.seed, manifest.per_type)
        digest = seal(cases)
        if digest != manifest.sha256:
            raise EvalError("dataset redaction: generated cases do not match the sealed sha256")
        if manifest.n != len(cases):
            raise EvalError("dataset redaction: manifest n differs from the generated cases")
        return Dataset(name=self.name, manifest=manifest, cases=cases, sha256=digest)

    def run(self, ds: Dataset) -> Results:
        leaks: dict[str, int] = {t: 0 for t in TYPES}
        total: dict[str, int] = {t: 0 for t in TYPES}
        failures: list[str] = []
        for case in ds.cases:
            kind = case["kind"]
            total[kind] += 1
            redacted = redact(case["text"]).text
            if _leaks(case["canary"], redacted):
                leaks[kind] += 1
                failures.append(case["id"])
        rates = [Rate(f"{t} leaks", leaks[t], total[t], target_n=PER_TYPE, max_k=0) for t in TYPES if total[t]]
        if len(rates) != len(TYPES):
            raise EvalError("dataset redaction: an ID type has no case")
        state = "verified" if all(r.gate_ok for r in rates) else "invalid"
        return Results(
            suite=self.name, state=state, rates=rates, dataset_sha256=ds.sha256, n=len(ds.cases),
            author_role=ds.manifest.author_role, failures=failures,
        )
