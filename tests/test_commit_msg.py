"""LT-43: the commit-message check and the commit-msg hook (PL-31)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("commit_msg_check", ROOT / "scripts" / "commit_msg_check.py")
assert _spec and _spec.loader
cmc = importlib.util.module_from_spec(_spec)
sys.modules["commit_msg_check"] = cmc
_spec.loader.exec_module(cmc)

# Planted values are built here so the repository scan finds no trailer or link in this file.
CO = "Co-" + "Authored-By: "
SESSION = "https://example.test/code/" + "session_" + "0123456789abcdefghij"


def test_attribution_trailer_fails() -> None:
    assert cmc.check_message("WU-25: x\n\n" + CO + "Some Tool <noreply@example.com>\n")
    assert cmc.check_message("WU-25: x\n\n" + CO.lower() + "someone")
    assert cmc.check_message("WU-25: x\n\nAssisted-" + "by: a tool")
    assert cmc.check_message("WU-25: x\n\nGenerated " + "with [a tool](https://example.test)")
    assert cmc.check_message("WU-25: x\n\n\U0001f916 Generated " + "by an AI")


def test_session_link_fails() -> None:
    assert cmc.check_message("WU-25: x\n\n" + SESSION)
    assert cmc.check_message("WU-25: x\n\nsee session_" + "0123456789abcdef")


def test_plain_message_passes() -> None:
    assert cmc.check_message("WU-25: plain words\n\nWhat changed and why. The session handler now retries.\n") == []
    assert cmc.check_message("WU-25: x\n\nSigned-off-by: Example Person <person@example.com>\n") == []  # PL-37
    assert cmc.check_message("WU-25: x\n\n# " + CO + "dropped by git as a comment\n") == []


def test_scissors_section_is_ignored() -> None:
    assert cmc.check_message("WU-25: x\n" + cmc.SCISSORS + "\n" + CO + "z\n") == []


def test_main_on_a_message_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad, good = tmp_path / "bad", tmp_path / "good"
    bad.write_text("WU-25: x\n\n" + CO + "a\n")
    good.write_text("WU-25: x\n")
    assert cmc.main([str(bad)]) == 1
    assert "rejected" in capsys.readouterr().out
    assert cmc.main([str(good)]) == 0
    assert cmc.main([]) == 2


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Example Dev")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "scripts").symlink_to(ROOT / "scripts")
    (repo / ".githooks").symlink_to(ROOT / ".githooks")
    _git(repo, "config", "core.hooksPath", ".githooks")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    return repo


def test_hook_blocks_a_planted_trailer_and_allows_plain(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    bad = _git(repo, "commit", "-m", "WU-25: x\n\n" + CO + "a tool", check=False)
    assert bad.returncode != 0 and "rejected" in bad.stdout + bad.stderr
    ok = _git(repo, "commit", "-m", "WU-25: plain", check=False)
    assert ok.returncode == 0, ok.stderr


def test_range_check_finds_a_pushed_trailer(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "WU-25: x\n\n" + CO + "a tool")
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "commit_msg_check.py"), "--range", "HEAD"],
        capture_output=True, text=True, cwd=repo, check=False,
    )  # fmt: skip
    assert done.returncode == 1 and "Co-Authored-By" in done.stdout


@pytest.mark.parametrize("word", ["Generated", "generated", "Written", "Created", "Made", "Assisted", "Drafted"])
@pytest.mark.parametrize("prep", ["with", "by"])
def test_line_start_generated_with_or_by_fails(word: str, prep: str) -> None:
    assert cmc.check_message(f"WU-25: x\n\n{word} {prep} Some Tool\n")
    assert cmc.check_message(f"WU-25: x\n\n  * {word} {prep} Some Tool\n")


def test_line_start_rule_leaves_prose_alone() -> None:
    assert cmc.check_message("WU-25: x\n\nThe report is generated with the nightly job.\n") == []
    assert cmc.check_message("WU-25: x\n\nThe fix was made by hand, written in plain words.\n") == []


def test_names_file_terms_are_rejected() -> None:
    terms = ["zorblax", "quuxmodel"]
    assert cmc.check_message("WU-25: x\n\nreviewed by ZORBLAX today\n", terms)
    assert cmc.check_message("WU-25: x\n\nthe quuxmodel run\n", terms)
    assert cmc.check_message("WU-25: x\n\nzorblaxian is another word\n", terms) == []
    assert cmc.check_message("WU-25: x\n\nreviewed by ZORBLAX today\n") == []  # no list, no term rule


def test_main_reads_terms_from_the_names_file_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = tmp_path / "names.txt"
    names.write_text("# synthetic\nzorblax\n")
    msg = tmp_path / "MSG"
    msg.write_text("WU-25: x\n\nthanks zorblax\n")
    monkeypatch.delenv("SAATHI_NAMES_FILE", raising=False)
    assert cmc.main([str(msg)]) == 0
    monkeypatch.setenv("SAATHI_NAMES_FILE", str(names))
    assert cmc.main([str(msg)]) == 1
