"""LT-39: the licence audit modes (RQ-71, PL-09, PL-10, PL-11)."""

import sys
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import license_audit as la  # noqa: E402

MANIFEST = ROOT / "tests" / "fixtures" / "image_manifest.txt"


class FakeDist:
    """Stands in for importlib.metadata.Distribution with real email-style metadata."""

    def __init__(self, name: str, version: str = "1.0", **headers: str):
        self.metadata = Message()
        self.metadata["Name"] = name
        for k, v in headers.items():
            self.metadata[k.replace("_", "-")] = v
        self.version = version


def manifest(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "manifest.txt"
    p.write_text("\n".join(lines) + "\n")
    return p


def dockerfile(tmp_path: Path, text: str) -> Path:
    d = tmp_path / "infra" / "asr"
    d.mkdir(parents=True)
    (d / "Dockerfile").write_text(text)
    return tmp_path / "infra"


# ------------------------------------------------------------------ packages
def test_package_without_licence_fails(capsys):
    out = la.audit_packages([FakeDist("mystery")], {})
    assert out and "mystery" in out[0]


def test_unknown_licence_fails():
    out = la.audit_packages([FakeDist("odd", License="Totally Custom Terms")], {})
    assert out and "review" in out[0]


def test_unknown_licence_is_not_rescued_by_missing_metadata_exception():
    exc = {"mystery": {"licence": "MPL-2.0", "reason": "r", "date": "2026-11-10"}}
    assert la.audit_packages([FakeDist("mystery")], exc)


@pytest.mark.parametrize(
    "lic", ["GPL-3.0-or-later", "LGPL-2.1-only", "AGPL-3.0", "SSPL-1.0", "ELv2", "BUSL-1.1", "RSAL", "CC-BY-NC-4.0"]
)
def test_banned_licences_fail(lic):
    assert la.audit_packages([FakeDist("bad", License_Expression=lic)], {})


def test_permissive_packages_pass():
    dists = [
        FakeDist("a", License_Expression="MIT"),
        FakeDist("b", License_Expression="Apache-2.0 OR BSD-3-Clause"),
        FakeDist("c", License_Expression="GPL-3.0-only OR MIT"),
    ]
    assert la.audit_packages(dists, {}) == []


def test_exception_never_lgpl():
    # Listing an LGPL package as an exception neither rescues it nor passes the exception check.
    exc = {"lg": {"licence": "LGPL-3.0-only", "reason": "needed", "date": "2026-11-10"}}
    dist = FakeDist("lg", License_Expression="LGPL-3.0-only")
    out = la.audit_packages([dist], exc)
    assert any("package lg: deny" in f for f in out)
    assert any("can never be excepted" in f for f in out)


def test_weak_copyleft_passes_only_with_exception():
    dist = FakeDist("weak", License_Expression="MPL-2.0")
    assert la.audit_packages([dist], {})
    exc = {"weak": {"licence": "MPL-2.0", "reason": "unmodified wheel", "date": "2026-11-10"}}
    assert la.audit_packages([dist], exc) == []


def test_exception_needs_licence_reason_and_date():
    problems = la.check_exceptions({"x": {"reason": "why"}})
    assert problems and "licence" in problems[0] and "date" in problems[0]


def test_documented_mpl_exceptions_kept():
    exc = la.load_exceptions()
    assert {"certifi", "orjson"} <= set(exc)
    assert la.check_exceptions(exc) == []
    assert all("MPL" in e["licence"] for e in exc.values())


def test_banned_list_regex_covers_policy():
    for lic in ["GPL-2.0", "LGPL-3.0", "AGPL-3.0", "SSPL-1.0", "ELv2", "BUSL-1.1", "RSAL", "Non-Commercial"]:
        assert la.DENY.search(lic), lic


def test_installed_packages_pass():
    assert la.main(["--packages"]) == 0


# ----------------------------------------------------------------- manifests
def test_repo_manifest_passes():
    assert la.audit_manifest(MANIFEST) == []


def test_manifest_with_ffmpeg_fails(tmp_path):
    out = la.audit_manifest(manifest(tmp_path, "ffmpeg | LGPL-2.1", "libavcodec59 | MIT"))
    assert sum("forbidden" in f for f in out) == 2


def test_manifest_ffmpeg_fails_even_with_clean_licence(tmp_path):
    assert la.audit_manifest(manifest(tmp_path, "ffmpeg | MIT"))


def test_manifest_with_opusinfo_fails(tmp_path):
    assert la.audit_manifest(manifest(tmp_path, "opusinfo | BSD-3-Clause"))


def test_manifest_with_lgpl_fails(tmp_path):
    out = la.audit_manifest(manifest(tmp_path, "libfoo | LGPL-2.1-or-later"))
    assert out and "deny" in out[0]


def test_manifest_with_gpl_fails(tmp_path):
    assert la.audit_manifest(manifest(tmp_path, "tool | GPL-3.0-only"))


def test_manifest_unknown_or_missing_licence_fails(tmp_path):
    out = la.audit_manifest(manifest(tmp_path, "libx | UNKNOWN", "liby", "libz | Custom Terms"))
    assert len(out) == 3


def test_empty_or_missing_manifest_fails(tmp_path):
    assert la.audit_manifest(manifest(tmp_path, "# only a comment"))
    assert la.audit_manifest(tmp_path / "nope.txt")


# --------------------------------------------------------------- Dockerfiles
def test_dockerfile_convert_flag_fails(tmp_path):
    infra = dockerfile(tmp_path, 'FROM scratch\nCMD ["whisper-server", "--convert", "-l", "hi"]\n')
    out = la.audit_dockerfiles(infra)
    assert out and "--convert" in out[0]


def test_dockerfile_ffmpeg_install_fails(tmp_path):
    infra = dockerfile(tmp_path, "FROM debian\nRUN apt-get install -y ffmpeg\n")
    assert la.audit_dockerfiles(infra)


def test_dockerfile_ffmpeg_base_image_fails(tmp_path):
    assert la.audit_dockerfiles(dockerfile(tmp_path, "FROM example/ffmpeg:6\n"))


def test_dockerfile_opusinfo_fails(tmp_path):
    assert la.audit_dockerfiles(dockerfile(tmp_path, "FROM debian\nRUN opusinfo x.opus\n"))


def test_clean_dockerfile_passes(tmp_path):
    assert la.audit_dockerfiles(dockerfile(tmp_path, "FROM debian\nRUN apt-get install -y opus-tools\n")) == []


def test_no_dockerfile_fails(tmp_path):
    assert la.audit_dockerfiles(tmp_path)


def test_repo_dockerfiles_have_no_ffmpeg():
    """The grep the brief asks for, over every Dockerfile in the tree."""
    files = la.find_dockerfiles(ROOT / "infra")
    assert any(p.parent.name == "whisper" for p in files)
    for p in files:
        text = p.read_text().lower()
        assert "ffmpeg" not in text, p
        assert "--convert" not in text, p
        assert "opusinfo" not in text, p


def test_asr_image_keeps_only_opusdec_and_opusenc():
    text = (ROOT / "infra" / "whisper" / "Dockerfile").read_text()
    assert "opus-tools" in text and "! -name opusdec ! -name opusenc" in text


# ----------------------------------------------------------------------- CLI
def test_cli_all_modes_pass_on_repo():
    assert la.main(["--packages", "--dockerfiles", "--manifest", str(MANIFEST)]) == 0


def test_cli_manifest_failure_exits_1(tmp_path, capsys):
    assert la.main(["--manifest", str(manifest(tmp_path, "ffmpeg | LGPL-2.1"))]) == 1
    assert "LICENSE AUDIT FAILED" in capsys.readouterr().out


def test_cli_dockerfiles_failure_exits_1(tmp_path):
    infra = dockerfile(tmp_path, "FROM debian\nRUN apt-get install ffmpeg\n")
    assert la.main(["--dockerfiles", "--infra", str(infra)]) == 1
