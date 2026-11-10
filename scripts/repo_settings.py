#!/usr/bin/env python3
"""Repository settings reader (PL-16, 17, 18, 21, 22): python3 scripts/repo_settings.py --check ID [--repo OWNER/NAME]

Reads one setting per check id through the GitHub REST API (`gh api`; GraphQL and users endpoints are not used):

  PL-16  branch protection: a review is required and the last pusher cannot approve their own push
  PL-17  branch protection: pull request required, force pushes and deletion off (agents change main only by PR)
  PL-18  branch protection: the `evals` status check is required
  PL-21  collaborators: no one but the owner holds write access (only the owner merges, tags, publishes)
  PL-22  branch protection: the required checks, administrators included, strict up-to-date

Exit 0 verified, 1 invalid (the setting is read and wrong), 2 unavailable (no `gh`, no authentication, no network,
no admin rights, or an unreadable answer). Unavailable is never a pass. The repository comes from --repo,
GITHUB_REPOSITORY or the `origin` remote; nothing about it is stored in the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any, Literal

State = Literal["verified", "invalid", "unavailable"]
Gh = Callable[[str], tuple[int, str, str]]  # api path -> (exit code, stdout, stderr)

BRANCH = "main"
REQUIRED_CHECKS = ("lint", "test", "license-audit", "design-check", "doc-lint", "evals", "compose")
WRITE_LEVELS = {"push", "maintain", "admin"}
CHECK_IDS = ("PL-16", "PL-17", "PL-18", "PL-21", "PL-22")
PROTECTION = "repos/{repo}/branches/" + BRANCH + "/protection"
COLLABORATORS = "repos/{repo}/collaborators?affiliation=direct&per_page=100"


def gh_api(path: str) -> tuple[int, str, str]:
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False, timeout=60)
    return done.returncode, done.stdout, done.stderr


def default_repo() -> str | None:
    if env := os.environ.get("GITHUB_REPOSITORY"):
        return env
    done = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False)
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", done.stdout)
    return m.group(1) if m else None


def _enabled(block: Any) -> bool:
    return isinstance(block, dict) and bool(block.get("enabled"))


def _contexts(protection: dict[str, Any]) -> set[str]:
    checks = protection.get("required_status_checks") or {}
    found = set(checks.get("contexts") or [])
    found |= {c.get("context", "") for c in checks.get("checks") or [] if isinstance(c, dict)}
    return found


def judge_protection(check_id: str, p: dict[str, Any]) -> State:
    reviews = p.get("required_pull_request_reviews")
    if check_id == "PL-16":
        ok = (
            bool(reviews)
            and reviews.get("required_approving_review_count", 0) >= 1
            and bool(reviews.get("require_last_push_approval"))
        )
    elif check_id == "PL-17":
        ok = bool(reviews) and not _enabled(p.get("allow_force_pushes")) and not _enabled(p.get("allow_deletions"))
    elif check_id == "PL-18":
        ok = "evals" in _contexts(p)
    else:  # PL-22
        ok = bool(reviews) and set(REQUIRED_CHECKS) <= _contexts(p) and _enabled(p.get("enforce_admins"))
    return "verified" if ok else "invalid"


def _load(gh: Gh, path: str) -> tuple[State | None, Any]:
    """(None, parsed JSON) on success; (state, None) when the read decides the answer."""
    try:
        code, out, err = gh(path)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unavailable", None
    if code != 0:
        if "not protected" in (out + err).lower():
            return "invalid", None
        return "unavailable", None  # no auth, no network, no admin rights, or an unknown repository
    try:
        return None, json.loads(out)
    except ValueError:
        return "unavailable", None


def read_setting(check_id: str, repo: str | None = None, gh: Gh = gh_api) -> State:
    if check_id not in CHECK_IDS:
        raise ValueError(f"unknown check id {check_id!r}; known: {', '.join(CHECK_IDS)}")
    repo = repo or default_repo()
    if not repo:
        return "unavailable"
    if check_id == "PL-21":
        state, data = _load(gh, COLLABORATORS.format(repo=repo))
        if state or not isinstance(data, list):
            return state or "unavailable"
        owner = repo.split("/")[0].lower()
        writers = [
            u
            for u in data
            if isinstance(u, dict) and u.get("login", "").lower() != owner and _writes(u.get("permissions"))
        ]
        return "invalid" if writers else "verified"
    state, data = _load(gh, PROTECTION.format(repo=repo))
    if state or not isinstance(data, dict):
        return state or "unavailable"
    return judge_protection(check_id, data)


def _writes(permissions: Any) -> bool:
    if not isinstance(permissions, dict):
        return True  # an unreadable permission is treated as write access, never as a pass
    return any(permissions.get(level) for level in WRITE_LEVELS)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read one repository setting through the GitHub REST API.")
    ap.add_argument("--check", required=True, choices=CHECK_IDS)
    ap.add_argument("--repo", default=None, help="OWNER/NAME (default: GITHUB_REPOSITORY or the origin remote)")
    args = ap.parse_args(argv)
    state = read_setting(args.check, args.repo)
    print(f"{args.check}: {state}")
    return {"verified": 0, "invalid": 1}.get(state, 2)


if __name__ == "__main__":
    sys.exit(main())
