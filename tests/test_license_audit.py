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


def test_mit_plus_gpl_or_later_classifiers_fail():
    dist = FakeDist("mix")
    dist.metadata["Classifier"] = "License :: OSI Approved :: MIT License"
    dist.metadata["Classifier"] = "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)"
    assert la.audit_packages([dist], {})


def test_mit_or_gpl_classifiers_still_pass():
    dist = FakeDist("dual", License="MIT OR GPL-2.0-only")
    assert la.audit_packages([dist], {}) == []


def test_exception_applies_only_to_its_own_licence():
    exc = {"weak": {"licence": "MPL-2.0", "reason": "unmodified wheel", "date": "2026-11-10"}}
    other = FakeDist("weak", License_Expression="EUPL-1.2")
    assert la.audit_packages([other], exc)
    assert la.audit_packages([FakeDist("weak", License_Expression="MPL-2.0")], exc) == []


def test_cc0_is_public_domain():
    assert la.audit_packages([FakeDist("n", License_Expression="CC0-1.0")], {}) == []
    assert la.audit_packages([FakeDist("n", License="CC0 1.0 Universal")], {}) == []


def test_ofl_fails_for_packages_but_passes_in_manifest(tmp_path):
    assert la.audit_packages([FakeDist("f", License_Expression="OFL-1.1")], {})
    assert la.audit_manifest(manifest(tmp_path, "somefont.ttf | OFL-1.1")) == []


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
    # Owner-approved permissive exceptions, named one by one (2026-11-10); every other entry is MPL.
    approved_permissive = {"regex": "CNRI-Python"}
    for name, e in exc.items():
        if name in approved_permissive:
            assert approved_permissive[name] in e["licence"], name
        else:
            assert "MPL" in e["licence"], name


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


@pytest.mark.parametrize(
    "line",
    [
        "RUN apt-get install -y gstreamer1.0-libav",
        "RUN apt-get install -y --no-install-recommends gst-libav foo",
        "RUN apt-get install -y libav-tools",
        "RUN pip install av",
        "RUN pip3 install --no-cache-dir numpy av==12.0",
        "RUN uv pip install PyAV",
    ],
)
def test_dockerfile_libav_and_pyav_fail(tmp_path, line):
    assert la.audit_dockerfiles(dockerfile(tmp_path, f"FROM debian:bookworm-slim\n{line}\n"))


@pytest.mark.parametrize("line", ["RUN pip install avro", "RUN pip install pytest-av-x==1", "RUN pip install numpy"])
def test_dockerfile_similar_names_pass(tmp_path, line):
    assert la.audit_dockerfiles(dockerfile(tmp_path, f"FROM debian:bookworm-slim\n{line}\n")) == []


def test_final_image_has_no_downloader():
    """F1: the final stage installs only opus-tools; no wget or curl (wget is GPL-3.0+)."""
    text = (ROOT / "infra" / "whisper" / "Dockerfile").read_text()
    final = text.split("FROM debian:trixie-slim", 3)[-1]
    assert "wget" not in final and "curl" not in final
    assert "COPY --from=build" in final and "/models/" in final


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


# ------------------------------------------------------------------ image base allowlist (option a)
BASE = {
    "libc6": {"name": "libc6", "licence": "LGPL-2.1+", "reason": "GCC runtime exception"},
    "libstdc++6": {"name": "libstdc++6", "licence": "GPL-3+ WITH GCC-exception-3.1", "reported": "UNKNOWN",
                   "reason": "GCC runtime exception"},
}


def test_listed_base_package_passes_in_manifest_mode(tmp_path):
    m = manifest(tmp_path, "libc6:amd64 | LGPL-2.1+", "libstdc++6:amd64 | UNKNOWN")
    assert la.audit_manifest(m) != []
    assert la.audit_manifest(m, BASE) == []


def test_listed_base_package_with_other_licence_fails(tmp_path):
    assert la.audit_manifest(manifest(tmp_path, "libc6:amd64 | GPL-3+"), BASE)


def test_unlisted_gpl_package_fails(tmp_path):
    out = la.audit_manifest(manifest(tmp_path, "libflac14:amd64 | GPL-2+ or LGPL-2.1+"), BASE)
    assert out and "deny" in out[0]


def test_base_allowlist_does_not_apply_to_packages():
    dist = FakeDist("libc6", License_Expression="LGPL-2.1-or-later")
    assert la.audit_packages([dist], {})
    assert "base" not in la.audit_packages.__code__.co_varnames


def test_repo_base_allowlist_is_well_formed():
    entries, problems = la.load_base_allowlist()
    assert problems == [] and "libc6" in entries
    assert not any("opus" in n for n in entries)
    # libflac14 (opus-tools runtime) is listed only because the owner approved it by name.
    assert entries["libflac14"]["reason"].endswith("owner-approved by name")


OWNER_APPROVED = ("libflac14", "ca-certificates", "netbase", "libgdbm6t64", "libreadline8t64", "readline-common")


def test_owner_approved_packages_listed_by_exact_name():
    entries, _ = la.load_base_allowlist()
    reason = "unmodified distribution package, not linked into Saathi code; owner-approved by name"
    for name in OWNER_APPROVED:
        assert entries[name]["reason"] == reason, name


def test_owner_approved_package_with_other_licence_fails(tmp_path):
    entries, _ = la.load_base_allowlist()
    assert la.audit_manifest(manifest(tmp_path, "libflac14:amd64 | GPL-2+ or LGPL-2.1+"), entries) == []
    assert la.audit_manifest(manifest(tmp_path, "libflac14:amd64 | AGPL-3+"), entries)
    assert la.audit_manifest(manifest(tmp_path, "libflac12:amd64 | GPL-2+ or LGPL-2.1+"), entries)


@pytest.mark.parametrize(
    "entry",
    [
        'name = "libc6"\nreason = "GCC runtime exception"',
        'name = "lib*"\nlicence = "GPL-2+"\nreason = "GCC runtime exception"',
        'name = "libc6"\nlicence = "LGPL-2.1+"\nreason = "needed"',
        'name = "libc6"\nlicence = "LGPL-2.1+"\nreason = "GCC runtime exception"\ncategory = "GPL"',
    ],
)
def test_malformed_base_entry_is_rejected(tmp_path, entry):
    p = tmp_path / "base.toml"
    p.write_text(f"[[package]]\n{entry}\n")
    entries, problems = la.load_base_allowlist(p)
    assert entries == {} and problems
    m = manifest(tmp_path, "libc6:amd64 | LGPL-2.1+")
    assert la.main(["--manifest", str(m), "--base-allowlist", str(p)]) == 1
