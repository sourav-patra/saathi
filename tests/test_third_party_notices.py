"""THIRD-PARTY-NOTICES.md is generated from the image-audit manifests and checked for staleness."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("third_party_notices", ROOT / "scripts" / "third_party_notices.py")
assert _spec and _spec.loader
tpn = importlib.util.module_from_spec(_spec)
sys.modules["third_party_notices"] = tpn
_spec.loader.exec_module(tpn)


def _manifests(d: Path, image: str = "asr") -> None:
    (d / f"manifest-{image}.txt").write_text("bash | GPL-3+\nzlib1g:amd64 | Zlib\n")
    (d / f"versions-{image}.txt").write_text("bash | 5.2-1\nzlib1g:amd64 | 1:1.3\n")


def _allow(d: Path) -> Path:
    p = d / "allow.toml"
    p.write_text('[[package]]\nname = "bash"\nlicence = "GPL-3+"\nreason = "x"\n')
    return p


def test_generate_marks_gpl_and_lists_versions(tmp_path: Path) -> None:
    _manifests(tmp_path)
    out = tmp_path / "N.md"
    args = ["--dir", str(tmp_path), "--out", str(out), "--allowlist", str(_allow(tmp_path)), "asr"]
    assert tpn.main(args) == 0
    text = out.read_text()
    assert "| bash | 5.2-1 | GPL-3+ | owner-approved |" in text
    assert "| zlib1g | 1:1.3 | Zlib |  |" in text
    assert "/usr/share/doc/<pkg>/copyright" in text and "not published" in text
    assert "Debian source packages" in text
    assert tpn.main(["--check", *args]) == 0


def test_check_fails_when_stale(tmp_path: Path) -> None:
    _manifests(tmp_path)
    out = tmp_path / "N.md"
    args = ["--dir", str(tmp_path), "--out", str(out), "--allowlist", str(_allow(tmp_path)), "asr"]
    assert tpn.main(args) == 0
    (tmp_path / "versions-asr.txt").write_text("bash | 5.2-2\nzlib1g | 1:1.3\n")
    assert tpn.main(["--check", *args]) == 1


def test_check_fails_when_section_missing(tmp_path: Path) -> None:
    _manifests(tmp_path, "speech")
    out = tmp_path / "N.md"
    out.write_text("# Third-party notices\n")
    assert tpn.main(["--check", "--dir", str(tmp_path), "--out", str(out), "speech"]) == 1


def test_check_skips_without_manifest(tmp_path: Path) -> None:
    assert tpn.main(["--check", "--dir", str(tmp_path), "--out", str(tmp_path / "N.md")]) == 0


def test_generate_needs_every_manifest(tmp_path: Path) -> None:
    _manifests(tmp_path, "asr")
    assert tpn.main(["--dir", str(tmp_path), "--out", str(tmp_path / "N.md")]) == 1


def test_committed_file_says_not_published() -> None:
    text = (ROOT / "THIRD-PARTY-NOTICES.md").read_text()
    assert "not published" in text
    for image in tpn.IMAGES:
        assert tpn.begin(image) in text and tpn.end(image) in text
