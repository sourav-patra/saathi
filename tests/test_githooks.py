"""The pre-commit hook runs the doc lint on staged markdown (PL-19, RQ-101); added by orchestrator decision."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOOD = "# Title\n\nPlain words here.\n\nSkipped or not checked: nothing.\n"
BAD = "# Title\n\nA clause — joined by a dash.\n\nSkipped or not checked: nothing.\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for k, v in (("user.name", "Example Dev"), ("user.email", "dev@example.com"), ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)
    (repo / "scripts").symlink_to(ROOT / "scripts")
    (repo / ".githooks").symlink_to(ROOT / ".githooks")
    _git(repo, "config", "core.hooksPath", ".githooks")
    return repo


def test_precommit_blocks_a_failing_staged_markdown_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "note.md").write_text(BAD)
    _git(repo, "add", "note.md")
    done = _git(repo, "commit", "-m", "WU-25: note")
    assert done.returncode != 0 and "dash" in done.stdout + done.stderr


def test_precommit_passes_clean_markdown_and_other_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "note.md").write_text(GOOD)
    (repo / "code.py").write_text("x = 1\n")
    _git(repo, "add", "note.md", "code.py")
    done = _git(repo, "commit", "-m", "WU-25: note")
    assert done.returncode == 0, done.stdout + done.stderr
    (repo / "code.py").write_text("x = 2\n")
    _git(repo, "add", "code.py")
    assert _git(repo, "commit", "-m", "WU-25: code only").returncode == 0


def test_precommit_passes_names_file_as_separate_arguments(tmp_path: Path) -> None:
    import os

    repo = _repo(tmp_path)
    names = tmp_path / "dir with space" / "names.txt"
    names.parent.mkdir()
    names.write_text("# synthetic\nzorblax\n")
    (repo / "note.md").write_text(GOOD)
    _git(repo, "add", "note.md")
    env = {**os.environ, "SAATHI_NAMES_FILE": str(names)}
    done = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "WU-25: note"], capture_output=True, text=True, check=False, env=env
    )
    assert done.returncode == 0, done.stdout + done.stderr
    (repo / "hit.md").write_text(GOOD.replace("Plain words", "Plain zorblax words"))
    _git(repo, "add", "hit.md")
    done = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "WU-25: hit"], capture_output=True, text=True, check=False, env=env
    )
    assert done.returncode != 0


def test_precommit_handles_non_ascii_file_names(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "café.md").write_text(GOOD)
    _git(repo, "add", "-A")
    done = _git(repo, "commit", "-m", "WU-25: accent")
    assert done.returncode == 0, done.stdout + done.stderr
