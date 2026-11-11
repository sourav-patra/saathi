"""LT-42: the leak scan (PL-08, PL-12, PL-15, PL-31). Every planted value is built at run time from pieces, so
this file holds no key, number, address or term the scan would flag, and the denylist terms are synthetic."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("leak_scan", ROOT / "scripts" / "leak_scan.py")
assert _spec and _spec.loader
leak_scan = importlib.util.module_from_spec(_spec)
sys.modules["leak_scan"] = leak_scan
_spec.loader.exec_module(leak_scan)

KEY = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
TOKEN = "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
PHONE = "+91 " + "98" + "7" + "01 23456"
EMAIL = "someone" + "@" + "mail-host" + ".io"
TERM = "zorblax"  # a synthetic denylist term
CO = "Co-" + "Authored-By: "


def write(root: Path, name: str, body: str | bytes) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body if isinstance(body, bytes) else body.encode())


def rules(findings: list) -> set[str]:
    return {f.rule for f in findings}


def test_planted_key_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "cfg.txt", f"aws = {KEY}\n")
    write(tmp_path, "tok.md", f"token {TOKEN}\n")
    write(tmp_path, "pem.txt", "-----BEGIN " + "RSA PRIVATE KEY-----\n")
    found = leak_scan.scan_tree(tmp_path)
    assert {f.where for f in found} == {"cfg.txt", "tok.md", "pem.txt"} and rules(found) == {"key"}
    assert leak_scan.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert KEY not in out and TOKEN not in out  # a value never reaches the log


def test_names_file_hit_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    names = tmp_path / "names.txt"
    names.write_text(f"# synthetic\n{TERM}\n\n")
    tree = tmp_path / "tree"
    write(tree, "doc.md", f"Ask {TERM.upper()} about it.\n")
    write(tree, "fine.md", "nothing here, and zorblaxian is a different word\n")
    found = leak_scan.scan_tree(tree, leak_scan.load_names(names))
    assert [(f.rule, f.where, f.line) for f in found] == [("names", "doc.md", 1)]
    assert leak_scan.main([str(tree), "--names-file", str(names)]) == 1
    out = capsys.readouterr().out
    assert TERM not in out.lower().replace("zorblaxian", "")  # reported by position, not by value


def test_names_unavailable_without_a_list(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch) -> None:
    monkeypatch.delenv("SAATHI_NAMES_FILE", raising=False)
    write(tmp_path, "a.txt", "plain\n")
    assert leak_scan.main([str(tmp_path)]) == 0
    assert "names: unavailable" in capsys.readouterr().out
    assert leak_scan.main([str(tmp_path), "--require-names"]) == 2
    names = tmp_path / "n.txt"
    names.write_text(TERM + "\n")
    monkeypatch.setenv("SAATHI_NAMES_FILE", str(names))
    write(tmp_path, "b.txt", TERM + "\n")
    assert leak_scan.main([str(tmp_path)]) == 1  # the env var supplies the list


def test_media_extension_fails(tmp_path: Path) -> None:
    for name in ("note.ogg", "clip.MP4", "dir/voice.opus", "pic.jpeg"):
        write(tmp_path, name, b"\x00\x01 not real media")
    found = leak_scan.scan_tree(tmp_path)
    assert rules(found) == {"media"} and len(found) == 4
    write(tmp_path, "figure.svg", "<svg/>")
    assert len(leak_scan.scan_tree(tmp_path)) == 4  # a vector figure is not elder media


def test_clean_tree_passes(tmp_path: Path) -> None:
    write(tmp_path, "a.md", "Call +91 0000 000000 or write to noreply@example.com.\n")
    write(
        tmp_path, "b.py", "NUMBER = '+910000000000'\nAUTHOR = 'dev@users.noreply.github.com'\nHOME = '/home/user/x'\n"
    )
    write(tmp_path, "c.env", 'API_TOKEN="your-token-here-placeholder-0000"\nSECRET_KEY=${SECRET_KEY}\n')
    write(tmp_path, "d.py", "key = settings.some_secret_key.get_secret_value()\nvalue = 9876543210\n")
    write(tmp_path, "blob.bin", b"\x00" + KEY.encode())
    assert leak_scan.scan_tree(tmp_path) == []
    assert leak_scan.main([str(tmp_path)]) == 0


def test_phone_email_and_private_path_fail(tmp_path: Path) -> None:
    write(tmp_path, "a.txt", f"{PHONE}\n{EMAIL}\n/home/" + "alice" + "/notes\nC:\\Users\\" + "bob" + "\\x\n")
    found = leak_scan.scan_tree(tmp_path)
    assert rules(found) == {"phone", "email", "private_path"} and len(found) == 4


def test_the_repository_itself_is_clean() -> None:
    assert leak_scan.scan_tree(ROOT) == []


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def test_history_scan_reads_commit_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for k, v in (
        ("user.name", TERM),
        ("user.email", EMAIL),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", "/dev/null"),
    ):
        _git(repo, "config", k, v)
    write(repo, "a.txt", "plain\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first\n\n" + CO + "a tool")
    write(repo, "b.txt", f"later {KEY}\n")
    write(repo, "clip.ogg", b"\x00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    _git(repo, "rm", "-q", "b.txt", "clip.ogg")
    _git(repo, "commit", "-q", "-m", "remove them")  # the tree is clean now; history still holds them
    assert leak_scan.scan_tree(repo) == []
    found = leak_scan.scan_history(repo, [TERM])
    assert {"email", "names", "attribution", "key", "media"} <= rules(found)
    assert leak_scan.main([str(repo), "--history"]) == 1
    clean = tmp_path / "clean"
    clean.mkdir()
    _git(clean, "init", "-q")
    for k, v in (
        ("user.name", "Example Dev"),
        ("user.email", "dev@users.noreply.github.com"),
        ("commit.gpgsign", "false"),
    ):
        _git(clean, "config", k, v)
    write(clean, "a.txt", "plain\n")
    _git(clean, "add", "-A")
    _git(clean, "commit", "-q", "-m", "plain")
    assert leak_scan.scan_history(clean, [TERM]) == []


def test_history_unavailable_outside_a_repository(tmp_path: Path) -> None:
    assert leak_scan.main([str(tmp_path), "--history"]) == 2


def _repo(
    tmp_path: Path, name: str = "repo", user: str = "Example Dev", email: str = "dev@users.noreply.github.com"
) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    for k, v in (
        ("user.name", user),
        ("user.email", email),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", "/dev/null"),
    ):
        _git(repo, "config", k, v)
    return repo


def test_history_scan_sees_a_key_added_in_a_rename_plus_edit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    write(repo, "old.txt", "".join(f"line {i} of plain text in this file\n" for i in range(20)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "mv", "old.txt", "new.txt")
    with (repo / "new.txt").open("a") as fh:
        fh.write(f"aws = {KEY}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rename and edit")
    assert "key" in rules(leak_scan.scan_history(repo))


def test_history_scan_sees_a_key_added_in_a_merge(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    write(repo, "a.txt", "plain\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "side")
    write(repo, "b.txt", "side\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side")
    _git(repo, "checkout", "-q", "-")
    write(repo, "c.txt", "main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    write(repo, "c.txt", f"main\naws = {KEY}\n")  # a change made only in the merge commit
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "merge")
    assert "key" in rules(leak_scan.scan_history(repo))


@pytest.mark.parametrize(
    "address",
    ["noreply" + "@" + "vendor-host" + ".io", "no-reply" + "@" + "tool-host" + ".io", "git" + "@" + "code-hub" + ".io"],
)
def test_vendor_noreply_address_is_flagged(tmp_path: Path, address: str) -> None:
    write(tmp_path, "a.txt", f"contact {address}\n")
    assert rules(leak_scan.scan_tree(tmp_path)) == {"email"}


def test_only_configured_email_domains_pass(tmp_path: Path) -> None:
    ok = "a@users.noreply.github.com b@example.com c@sub.example.org"
    bad = "a" + "@" + "notexample.com"
    write(tmp_path, "ok.txt", ok + "\n")
    write(tmp_path, "bad.txt", bad + "\n")
    assert [f.where for f in leak_scan.scan_tree(tmp_path)] == ["bad.txt"]


PHONE_FORMS = ["98" + "123 45670", "0" + "98123" + " 45670", "98" + "123-45670", "0" + "98123" + "45670"]


@pytest.mark.parametrize("number", PHONE_FORMS)
def test_phone_forms_without_country_code_fail(tmp_path: Path, number: str) -> None:
    write(tmp_path, "a.txt", f"call {number} now\n")
    assert rules(leak_scan.scan_tree(tmp_path)) == {"phone"}


def test_phone_placeholders_stay_clean(tmp_path: Path) -> None:
    write(tmp_path, "a.txt", "+91 0000 000000 and +91 00000 00000 and 00000 00000 and 90000 00000\n")
    assert leak_scan.scan_tree(tmp_path) == []


def test_plain_run_scans_commit_metadata(tmp_path: Path) -> None:
    repo = _repo(tmp_path, user=TERM, email=EMAIL)
    write(repo, "a.txt", "plain\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first\n\n" + CO + "a tool")
    assert leak_scan.scan_tree(repo) == []  # the tree itself is clean
    assert leak_scan.main([str(repo)]) == 1  # no --history needed: attribution and identity fail the plain run
    found = leak_scan.scan_metadata(repo, [TERM])
    assert {"email", "names", "attribution"} <= rules(found)


def test_plain_run_in_a_clean_repository_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAATHI_NAMES_FILE", raising=False)
    repo = _repo(tmp_path)
    write(repo, "a.txt", "plain\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "plain")
    assert leak_scan.main([str(repo)]) == 0
    empty = _repo(tmp_path, "empty")
    assert leak_scan.main([str(empty)]) == 0  # no commits yet


@pytest.mark.parametrize("tld", ["example", "test", "invalid", "localhost"])
def test_rfc2606_reserved_names_pass_for_email_and_url_userinfo(tmp_path: Path, tld: str) -> None:
    write(tmp_path, "a.txt", f"mail someone@host.{tld} or fetch https://user@evil.{tld}/x\n")
    assert leak_scan.scan_tree(tmp_path) == []


@pytest.mark.parametrize("tld", ["io", "in", "exampled", "testing"])
def test_non_reserved_names_still_flagged(tmp_path: Path, tld: str) -> None:
    write(tmp_path, "a.txt", "fetch https://user" + "@" + f"evil.{tld}/x\n")
    assert rules(leak_scan.scan_tree(tmp_path)) == {"email"}


def test_sandbox_temp_path_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "a.txt", "see /tmp/" + "claude-0/scratch/out.log\n")
    write(tmp_path, "b.txt", "a plain /tmp/build/out.log is fine\n")
    assert [(f.where, f.rule) for f in leak_scan.scan_tree(tmp_path)] == [("a.txt", "sandbox_path")]


@pytest.mark.parametrize(
    "domain", ["mail-host.com.example", "corp.in.test", "zz-host.co.invalid", "bank.org.localhost"]
)
def test_reserved_name_with_a_public_inner_label_is_flagged(tmp_path: Path, domain: str) -> None:
    write(tmp_path, "a.txt", "write to role" + "@" + domain + "\n")
    assert rules(leak_scan.scan_tree(tmp_path)) == {"email"}


@pytest.mark.parametrize("host", ["zz-host.invalid", "zz-host.example", "zz-host.io", "zz-host.com.test"])
def test_url_userinfo_with_a_password_is_always_flagged(tmp_path: Path, host: str) -> None:
    write(tmp_path, "a.txt", "fetch https://role" + ":" + "pw-synthetic" + "@" + host + "/x\n")
    assert "url_password" in rules(leak_scan.scan_tree(tmp_path))


def test_url_userinfo_without_a_password_on_a_reserved_name_passes(tmp_path: Path) -> None:
    write(tmp_path, "a.txt", "fetch https://role" + "@" + "zz-host.invalid/x and a time 10:30 at noon\n")
    assert leak_scan.scan_tree(tmp_path) == []


def test_url_password_placeholders_pass(tmp_path: Path) -> None:
    at = "@"
    write(tmp_path, "a.txt", f"postgresql://role:<password>{at}db:5432/x\npostgresql://role:${{PW_VAR}}{at}db/x\n")
    assert leak_scan.scan_tree(tmp_path) == []


def _commit(repo: Path, name: str, body: str, msg: str) -> str:
    write(repo, name, body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_url_password_placeholders_pass_in_history_diffs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    at = "@"
    body = f"  URL: postgresql://role:${{PW_VAR}}{at}db:5432/x\n  B: postgresql://role:<password>{at}db/x\n"
    _commit(repo, "compose.yml", body, "placeholders")
    assert "url_password" not in rules(leak_scan.scan_history(repo, exceptions={}))


def test_history_exception_waives_only_the_named_commit_and_rule(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    line = 'url = "postgresql://role:' + "role" + "@" + 'db:5432/x"'
    old = _commit(repo, "a.py", line + "\n", "old")
    exc = {(old, leak_scan.line_digest(line)): {"url_password"}}
    assert "url_password" not in rules(leak_scan.scan_history(repo, exceptions=exc))
    _commit(repo, "b.py", line + "\n", "new")  # the same line in a new commit is still flagged
    found = [f for f in leak_scan.scan_history(repo, exceptions=exc) if f.rule == "url_password"]
    assert len(found) == 1 and old[:12] not in found[0].where
    assert "url_password" in rules(leak_scan.scan_tree(repo))  # never applies to the tree
    assert "url_password" in rules(leak_scan.scan_history(repo, rev_range="HEAD~1..HEAD", exceptions=exc))


def test_history_exception_keeps_other_rules_on_the_line(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    line = "postgresql://role:" + "role" + "@" + f"db/x {KEY}"
    old = _commit(repo, "a.py", line + "\n", "old")
    exc = {(old, leak_scan.line_digest(line)): {"url_password"}}
    assert rules(leak_scan.scan_history(repo, exceptions=exc)) == {"key"}


def test_checked_in_exceptions_are_well_formed() -> None:
    exc = leak_scan.load_exceptions()
    assert exc and all(rules_ == {"url_password"} for rules_ in exc.values())


def test_malformed_exception_is_rejected(tmp_path: Path) -> None:
    body = '[[exception]]\ncommit = "abc"\nrule = "url_password"\nline_sha256 = "00"\nreason = "x"\n'
    write(tmp_path, "e.toml", body)
    with pytest.raises(ValueError):
        leak_scan.load_exceptions(tmp_path / "e.toml")


def test_platform_merge_committer_is_allowed_exactly(tmp_path: Path) -> None:
    """A pull request run checks out the merge commit GitHub builds, whose committer is the platform address."""
    assert leak_scan.scan_text("GitHub <" + "noreply" + "@" + "github.com>", "meta", None) == []
    at = "@"
    others = ("someone" + at + "github.com", "noreply" + at + "github.com.evil.test", "x.noreply" + at + "github.com")
    for other in others:
        assert [f.rule for f in leak_scan.scan_text(other, "meta", None)] == ["email"], other
