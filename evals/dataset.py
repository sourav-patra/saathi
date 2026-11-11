"""Dataset manifest and loading (RQ-58): author, keyword author, sealed hash."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_SHA = re.compile(r"^[0-9a-f]{64}$")


class EvalError(Exception):
    """A dataset or plan is unusable. The runner turns it into exit 1."""


class DatasetMissing(EvalError):
    pass


class DatasetEmpty(EvalError):
    pass


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str
    author: str = ""  # who wrote the cases
    author_role: str = ""  # role only, never a name
    keyword_author: str = ""  # who wrote the keyword list under test
    sha256: str = ""  # seal over the case data, recorded before the change under test is written
    n: int = 0
    seed: int | None = None
    per_type: int | None = None


def check_manifest(m: Manifest) -> list[str]:
    """Problems that make a dataset unusable as a gate. Empty list means the manifest is acceptable."""
    problems: list[str] = []
    if not m.author.strip():
        problems.append("author is not set")
    if not m.keyword_author.strip():
        problems.append("keyword_author is not set")
    if m.author.strip() and m.author.strip().casefold() == m.keyword_author.strip().casefold():
        problems.append("author and keyword_author are the same")
    if not _SHA.match(m.sha256):
        problems.append("sealed sha256 is missing or not 64 hex characters")
    return problems


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Dataset:
    name: str
    manifest: Manifest
    cases: list[dict]
    sha256: str
    extra: dict = field(default_factory=dict)


def read_manifest(directory: Path, name: str) -> Manifest:
    path = directory / "manifest.json"
    if not path.is_file():
        raise DatasetMissing(f"dataset {name}: manifest.json not found")
    try:
        manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise EvalError(f"dataset {name}: manifest is not valid ({type(exc).__name__})") from exc
    problems = check_manifest(manifest)
    if problems:
        raise EvalError(f"dataset {name}: manifest check failed: {'; '.join(problems)}")
    return manifest


def load_jsonl_dataset(directory: Path, name: str) -> Dataset:
    """manifest.json plus cases.jsonl. The file bytes must match the sealed hash and hold at least one case."""
    manifest = read_manifest(directory, name)
    cases_path = directory / "cases.jsonl"
    if not cases_path.is_file():
        raise DatasetMissing(f"dataset {name}: cases.jsonl not found")
    raw = cases_path.read_bytes()
    digest = sha256_bytes(raw)
    if digest != manifest.sha256:
        raise EvalError(f"dataset {name}: cases do not match the sealed sha256")
    cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not cases:
        raise DatasetEmpty(f"dataset {name}: n is 0")
    if manifest.n != len(cases):
        raise EvalError(f"dataset {name}: manifest n {manifest.n} differs from {len(cases)} cases")
    return Dataset(name=name, manifest=manifest, cases=cases, sha256=digest)
