"""Radar suite: recall on emergency and scam phrases, false alarms on normal ones.

Two datasets feed the same grader. `radar` is the committed synthetic fixture and gates CI. `radar_heldout` is the
caregiver-written set (D-95): it is read only from a private path at run time, never from the repository, and when it
is absent the suite reports unavailable, which is not a pass. `scan` never loads either set.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from evals.dataset import Dataset, DatasetMissing, EvalError, load_jsonl_dataset
from evals.results import Rate, Results
from saathi.guards.radar import scan

HELDOUT_ENV = "SAATHI_RADAR_HELDOUT_DIR"
HELDOUT_DEFAULT = Path("private") / "evals" / "radar_heldout"
REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DATASETS = Path(__file__).resolve().parents[1] / "datasets"
ERROR_RULE = "RD-ERR-1"  # `scan` returns this when it crashed; it fails towards the humans, so it can never pass
LABELS = ("emergency", "scam", "normal")
TARGET_N = {"emergency": 100, "scam": 100, "normal": 300}


def _grade(ds: Dataset, suite: str) -> Results:
    counts = {label: [0, 0] for label in LABELS}  # label -> [events, n]; events: misses or false alarms
    failures: list[str] = []
    abstain = 0
    errors = 0
    for case in ds.cases:
        label = case.get("label")
        if label not in counts:
            raise EvalError(f"dataset {ds.name}: case {case.get('id')} has unknown label")
        text = case.get("text")
        if not isinstance(text, str) or not text.strip():
            abstain += 1  # nothing to grade: its own class, never a pass
            continue
        r = scan(text)
        counts[label][1] += 1
        crashed = ERROR_RULE in r.rule_ids
        if crashed:
            errors += 1  # a crash is an error and a miss, never a hit
        if label == "emergency":
            event = crashed or not r.emergency
        elif label == "scam":
            event = crashed or not r.scam
        else:
            event = r.triggered
        if event:
            counts[label][0] += 1
            if label != "normal":
                failures.append(str(case.get("id")))
    rates = [
        Rate("emergency misses", *counts["emergency"], target_n=TARGET_N["emergency"], max_k=0),
        Rate("scam misses", *counts["scam"], target_n=TARGET_N["scam"], max_k=0),
        Rate("normal false alarms", *counts["normal"], target_n=TARGET_N["normal"]),
    ]
    empty = [r.name for r in rates if r.n == 0]
    if empty:
        raise EvalError(f"dataset {ds.name}: no gradable case for {', '.join(empty)}")
    state = "verified" if errors == 0 and all(r.gate_ok for r in rates) else "invalid"
    return Results(
        suite=suite,
        state=state,
        rates=rates,
        abstain=abstain,
        errors=errors,
        dataset_sha256=ds.sha256,
        n=len(ds.cases),
        author_role=ds.manifest.author_role,
        failures=failures,
    )


class RadarSuite:
    name = "radar"

    def load(self, root: Path) -> Dataset:
        return load_jsonl_dataset(root / self.name, self.name)

    def run(self, ds: Dataset) -> Results:
        return _grade(ds, self.name)


def heldout_dir(env: dict[str, str] | None = None, repo_root: Path = REPO_ROOT) -> Path | None:
    """None only when the variable is unset and the default folder is absent. Anything else is a folder to check."""
    environ = os.environ if env is None else env
    value = environ.get(HELDOUT_ENV)
    if value:
        path = Path(value)
        if not path.is_dir():
            raise EvalError(f"{HELDOUT_ENV} is set but is not a folder")
        return path
    path = repo_root / HELDOUT_DEFAULT
    if not path.exists() and not path.is_symlink():
        return None
    if not path.is_dir():
        raise EvalError("the default held-out location exists but is not a folder")
    return path


def _git_ignored(resolved: Path, repo_root: Path) -> bool:
    """True only when git itself says the path is ignored. Any other answer, including no git, counts as tracked."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-q", "--", str(resolved)],
            capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _inside_tracked_area(path: Path, repo_root: Path) -> bool:
    """True when the resolved path sits in the repository and git does not ignore it."""
    resolved = path.resolve()
    root = repo_root.resolve()
    if not resolved.is_relative_to(root):
        return False
    return not _git_ignored(resolved, root)


def _committed_shas() -> set[str]:
    shas: set[str] = set()
    for manifest in PUBLIC_DATASETS.glob("*/manifest.json"):
        try:
            shas.add(str(json.loads(manifest.read_text(encoding="utf-8")).get("sha256", "")))
        except (OSError, ValueError):
            continue
    shas.discard("")
    return shas


def _public_texts() -> set[str]:
    texts: set[str] = set()
    for cases in PUBLIC_DATASETS.glob("*/cases.jsonl"):
        for line in cases.read_text(encoding="utf-8").splitlines():
            if line.strip():
                text = json.loads(line).get("text")
                if isinstance(text, str):
                    texts.add(" ".join(text.split()).casefold())
    return texts


class RadarHeldoutSuite:
    name = "radar_heldout"
    absent_is_unavailable = True  # an absent private set is reported unavailable, never passed

    def __init__(self, env: dict[str, str] | None = None, repo_root: Path = REPO_ROOT) -> None:
        self._env = env
        self._repo_root = repo_root

    def load(self, root: Path) -> Dataset:  # root is unused: the set is never read from the repository
        path = heldout_dir(self._env, self._repo_root)
        if path is None:
            raise DatasetMissing("the caregiver-written phrase set is not present")
        for name in ("manifest.json", "cases.jsonl"):
            if not (path / name).is_file():
                raise EvalError(f"the held-out folder is present but {name} is missing")
            if _inside_tracked_area(path / name, self._repo_root):
                raise EvalError(
                    "the held-out set must live outside the repository or under a git-ignored private folder"
                )
        try:
            ds = load_jsonl_dataset(path, self.name)
        except DatasetMissing as exc:
            raise EvalError(str(exc)) from exc
        if ds.manifest.suite != self.name:
            raise EvalError(f"the held-out manifest must name the suite {self.name}")
        if ds.sha256 in _committed_shas():
            raise EvalError("the held-out set is identical to a committed dataset")
        public = _public_texts()
        if any(" ".join(str(c.get("text", "")).split()).casefold() in public for c in ds.cases):
            raise EvalError("the held-out set repeats text found in the public fixture")
        return ds

    def run(self, ds: Dataset) -> Results:
        return _grade(ds, self.name)
