"""LT-44: the repository settings reader (PL-16, 17, 18, 21, 22), on recorded synthetic JSON; no network."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "repo_settings"
_spec = importlib.util.spec_from_file_location("repo_settings", ROOT / "scripts" / "repo_settings.py")
assert _spec and _spec.loader
repo_settings = importlib.util.module_from_spec(_spec)
sys.modules["repo_settings"] = repo_settings
_spec.loader.exec_module(repo_settings)

REPO = "example-owner/example-repo"


class FakeGh:
    def __init__(self, answers: dict[str, tuple[int, str, str]]) -> None:
        self.answers = answers
        self.paths: list[str] = []

    def __call__(self, path: str) -> tuple[int, str, str]:
        self.paths.append(path)
        for prefix, answer in self.answers.items():
            if path.startswith(prefix):
                return answer
        return 1, "", "HTTP 404: Not Found"


def fixture(name: str) -> tuple[int, str, str]:
    return 0, (FIX / name).read_text(), ""


def gh_with(protection: str, collaborators: str) -> FakeGh:
    return FakeGh(
        {
            f"repos/{REPO}/branches/main/protection": fixture(protection),
            f"repos/{REPO}/collaborators": fixture(collaborators),
        }
    )


@pytest.mark.parametrize("check_id", repo_settings.CHECK_IDS)
def test_unavailable_without_gh_auth(check_id: str) -> None:
    unauth = FakeGh({"repos/": (1, "", "gh: To get started with GitHub CLI, please run: gh auth login")})
    assert repo_settings.read_setting(check_id, REPO, unauth) == "unavailable"

    def no_gh(path: str) -> tuple[int, str, str]:
        raise FileNotFoundError("gh")

    assert repo_settings.read_setting(check_id, REPO, no_gh) == "unavailable"
    garbled = FakeGh({"repos/": (0, "<html>", "")})
    assert repo_settings.read_setting(check_id, REPO, garbled) == "unavailable"


@pytest.mark.parametrize("check_id", repo_settings.CHECK_IDS)
def test_each_check_id_reads_one_setting(check_id: str) -> None:
    gh = gh_with("protection_good.json", "collaborators_good.json")
    assert repo_settings.read_setting(check_id, REPO, gh) == "verified"
    assert len(gh.paths) == 1  # one REST read per id, never GraphQL or a users endpoint
    assert all(p.startswith(f"repos/{REPO}/") for p in gh.paths)
    expect = "collaborators" if check_id == "PL-21" else "branches/main/protection"
    assert expect in gh.paths[0]


@pytest.mark.parametrize("check_id", ["PL-16", "PL-17", "PL-18", "PL-22"])
def test_wrong_protection_is_invalid(check_id: str) -> None:
    gh = gh_with("protection_bad.json", "collaborators_good.json")
    assert repo_settings.read_setting(check_id, REPO, gh) == "invalid"


def test_unprotected_branch_is_invalid_not_unavailable() -> None:
    gh = FakeGh(
        {"repos/": (1, '{"message":"Branch not protected","status":"404"}', "gh: Branch not protected (HTTP 404)")}
    )
    assert repo_settings.read_setting("PL-22", REPO, gh) == "invalid"


def test_second_writer_fails_owner_only_merge() -> None:
    gh = gh_with("protection_good.json", "collaborators_bad.json")
    assert repo_settings.read_setting("PL-21", REPO, gh) == "invalid"


def test_missing_permissions_are_not_a_pass() -> None:
    answer = (0, json.dumps([{"login": "example-other"}]), "")
    assert repo_settings.read_setting("PL-21", REPO, FakeGh({"repos/": answer})) == "invalid"


def test_each_required_check_matters() -> None:
    base = json.loads((FIX / "protection_good.json").read_text())
    for name in repo_settings.REQUIRED_CHECKS:
        p = json.loads(json.dumps(base))
        p["required_status_checks"]["contexts"].remove(name)
        assert repo_settings.judge_protection("PL-22", p) == "invalid", name
    p = json.loads(json.dumps(base))
    p["required_status_checks"] = {"strict": True, "contexts": [], "checks": [{"context": "evals", "app_id": 1}]}
    assert repo_settings.judge_protection("PL-18", p) == "verified"


def test_unknown_id_and_exit_codes(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        repo_settings.read_setting("PL-99", REPO, FakeGh({}))
    for state, code in (("verified", 0), ("invalid", 1), ("unavailable", 2)):
        monkeypatch.setattr(repo_settings, "read_setting", lambda cid, repo=None, s=state: s)
        assert repo_settings.main(["--check", "PL-22", "--repo", REPO]) == code
    assert "PL-22: unavailable" in capsys.readouterr().out


def test_pl22_needs_pull_request_but_not_strict() -> None:
    """PL-22 per POLICY: pull request required, checks required, admins enforced; no `strict` (F8)."""
    for name, expect in (("protection_not_strict.json", "verified"), ("protection_no_pr.json", "invalid")):
        p = json.loads((FIX / name).read_text())
        assert repo_settings.judge_protection("PL-22", p) == expect, name
