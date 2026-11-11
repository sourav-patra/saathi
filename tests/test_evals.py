"""LT-40: eval harness (M23; RQ-58, 59, 60, 61, 99). Synthetic data only."""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evals import plan as plan_mod
from evals import report, run
from evals.dataset import Manifest, check_manifest, sha256_bytes
from evals.registry import check_registry, load_registry
from evals.results import Rate, Results
from evals.stats import format_rate, n_needed, rule_of_three, wilson
from evals.suites import radar as radar_mod
from evals.suites import redaction as redaction_mod
from saathi.guards import radar as radar_guard
from saathi.guards.radar import scan

DATASETS = run.DATASETS
HELDOUT = radar_mod.HELDOUT_ENV


def _row(name: str):
    return next(r for r in load_registry() if r.name == name)


def _write_set(directory: Path, rows: list[dict], *, author="caregiver", keyword_author="keyword list author") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()
    (directory / "cases.jsonl").write_bytes(data)
    manifest = {
        "suite": "radar_heldout", "author": author, "author_role": "caregiver", "keyword_author": keyword_author,
        "sha256": sha256_bytes(data), "n": len(rows),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def _tiny_rows() -> list[dict]:
    return [
        {"id": "e1", "label": "emergency", "text": "tonight there is chest pain and I cannot breathe properly"},
        {"id": "s1", "label": "scam", "text": "a caller wants my otp to unblock the account"},
        {"id": "n1", "label": "normal", "text": "the kettle has boiled"},
    ]


def test_row_per_suite() -> None:
    assert check_registry(harness_suites=tuple(run.harness_suites())) == []
    names = {r.name for r in load_registry()}
    assert set(run.harness_suites()) <= names
    # the SPEC suite table, one row each
    for suite in (
        "radar", "false_alarm", "redaction", "trace_canary", "inbox_crash", "commit_crash", "asr", "intent",
        "cart_ride", "policy", "injection", "hostile_screen", "debunk", "safety", "tone",
    ):
        assert suite in names


def test_row_without_reason_or_field_fails(tmp_path: Path) -> None:
    bad = tmp_path / "r.toml"
    bad.write_text(
        '[[suite]]\nname = "x"\ngrader_order = "code"\ndataset = "d"\nsize = "1"\nsplit = "s"\n'
        'gate = "g"\nowner = "owner"\nrunner = "private"\nphase = 0\n'
    )
    assert any("named reason" in p for p in check_registry(bad))
    bad.write_text('[[suite]]\nname = "x"\n')
    assert any("field gate is missing" in p for p in check_registry(bad))
    assert any("harness suite has no registry row" in p for p in check_registry(bad, ("y",)))


def test_missing_dataset_fails(tmp_path: Path) -> None:
    shutil.copytree(DATASETS, tmp_path / "ds")
    shutil.rmtree(tmp_path / "ds" / "radar")
    res = run.run_row(_row("radar"), tmp_path / "ds")
    assert res.state == "invalid" and "missing" in res.reason
    assert run.main(["radar", "--root", str(tmp_path / "ds")]) == 1
    assert run.main(["--all", "--root", str(tmp_path / "ds")]) == 1


def test_empty_dataset_fails(tmp_path: Path) -> None:
    shutil.copytree(DATASETS, tmp_path / "ds")
    d = tmp_path / "ds" / "radar"
    (d / "cases.jsonl").write_bytes(b"")
    manifest = json.loads((d / "manifest.json").read_text())
    manifest.update(sha256=sha256_bytes(b""), n=0)
    (d / "manifest.json").write_text(json.dumps(manifest))
    res = run.run_row(_row("radar"), tmp_path / "ds")
    assert res.state == "invalid" and "n is 0" in res.reason
    assert run.main(["radar", "--root", str(tmp_path / "ds")]) == 1


def test_altered_cases_fail_the_seal(tmp_path: Path) -> None:
    shutil.copytree(DATASETS, tmp_path / "ds")
    p = tmp_path / "ds" / "radar" / "cases.jsonl"
    p.write_text(p.read_text() + p.read_text().splitlines()[0] + "\n")
    res = run.run_row(_row("radar"), tmp_path / "ds")
    assert res.state == "invalid" and "sealed sha256" in res.reason


def test_skip_without_named_reason_fails() -> None:
    assert run.exit_code([Results(suite="x", state="unavailable", reason="")]) == 1
    assert run.exit_code([Results(suite="x", state="unavailable", reason="private set absent")]) == 0
    assert run.exit_code([Results(suite="x", state="unavailable", reason="private set absent")], strict=True) == 1


def test_all_phase0_runs_and_unavailable_is_not_pass(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv(HELDOUT, raising=False)
    monkeypatch.setattr(radar_mod, "heldout_dir", lambda *a, **k: None)
    code = run.main(["--all"])
    out = capsys.readouterr().out
    assert code == 0
    assert "radar: verified" in out and "redaction: verified" in out and "false_alarm: verified" in out
    assert "radar_heldout: unavailable" in out
    assert "summary: verified 4, invalid 0, unavailable 3" in out


def test_report_prints_k_n_bound() -> None:
    zero = format_rate(0, 30, 100)
    assert "0/30" in zero and "upper bound 0.100 (3/n)" in zero and "n needed: 100" in zero
    some = format_rate(2, 40, 300)
    lo, hi = wilson(2, 40)
    assert "2/40" in some and f"[{lo:.3f}, {hi:.3f}]" in some and "n needed: 300" in some
    with pytest.raises(ValueError):
        format_rate(0, 0)
    assert rule_of_three(60) == pytest.approx(0.05)
    assert n_needed(0.05) == 60
    res = Results(suite="s", state="verified", n=30, rates=[Rate("leaks", 0, 30, 30, 0)])
    text = "\n".join(report.render(res))
    assert "0/30" in text and "3/n" in text and "n needed" in text


def test_wilson_known_value() -> None:
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)


def test_equal_authors_fail_manifest() -> None:
    sha = "a" * 64
    ok = Manifest(suite="s", author="caregiver", keyword_author="keyword author", sha256=sha, n=1)
    assert check_manifest(ok) == []
    same = ok.model_copy(update={"keyword_author": "Caregiver "})
    assert any("same" in p for p in check_manifest(same))
    assert any("author is not set" in p for p in check_manifest(ok.model_copy(update={"author": ""})))
    assert any("keyword_author is not set" in p for p in check_manifest(ok.model_copy(update={"keyword_author": ""})))
    assert any("sha256" in p for p in check_manifest(ok.model_copy(update={"sha256": "nothex"})))


def test_equal_authors_dataset_is_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _write_set(tmp_path / "set", _tiny_rows(), author="same", keyword_author="same")
    suite = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(d)}, repo_root=tmp_path / "other")
    res = run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite})
    assert res.state == "invalid" and "same" in res.reason


def test_paid_run_needs_approval_row() -> None:
    p = plan_mod.parse_plan(_plan(paid=True))
    with pytest.raises(plan_mod.ApprovalMissing):
        plan_mod.authorize(p, [])
    with pytest.raises(plan_mod.ApprovalMissing):
        plan_mod.authorize(p, [{"suite": "other", "budget_inr": 99, "date": "2026-11-10"}])
    with pytest.raises(plan_mod.ApprovalMissing):
        plan_mod.authorize(p, [{"suite": "intent", "budget_inr": 1, "date": "2026-11-10"}])
    plan_mod.authorize(p, [{"suite": "intent", "budget_inr": 50, "date": "2026-11-10"}])
    plan_mod.authorize(plan_mod.parse_plan(_plan(paid=False)), [])  # a free run needs no row


def test_cli_refuses_paid_plan_without_approval(tmp_path: Path, capsys) -> None:
    f = tmp_path / "plan.toml"
    f.write_text("\n".join(f"{k} = {json.dumps(v)}" for k, v in _plan(paid=True).items()))
    assert run.main(["--plan", str(f)]) == 1
    assert "refused" in capsys.readouterr().out
    appr = tmp_path / "approvals.toml"
    appr.write_text('[[approval]]\nsuite = "intent"\nbudget_inr = 50\ndate = "2026-11-10"\n')
    assert run.main(["--plan", str(f), "--approvals", str(appr)]) == 0


def _plan(paid: bool) -> dict:
    return {
        "suite": "intent", "repeats": 5, "temperature": 0.0, "budget_tokens": 100000, "budget_inr": 50.0,
        "held_out_set": "held-out v1", "baseline": "arm a", "margin_pp": 5.0, "pass_mark": 90.0, "paid": paid,
    }


def test_plan_fields_are_validated() -> None:
    bad = _plan(False)
    del bad["baseline"]
    with pytest.raises(plan_mod.EvalError, match="lacks fields"):
        plan_mod.parse_plan(bad)
    for key, value in (("repeats", 3), ("margin_pp", 2.0), ("budget_inr", 0), ("pass_mark", 0)):
        with pytest.raises(plan_mod.EvalError):
            plan_mod.parse_plan({**_plan(False), key: value})


def test_budget_abort() -> None:
    b = plan_mod.Budget(tokens=100, inr=1.0)
    b.spend(60, 0.4)
    with pytest.raises(plan_mod.BudgetExceeded):
        b.spend(60, 0.1)
    b2 = plan_mod.Budget(tokens=1000, inr=1.0)
    with pytest.raises(plan_mod.BudgetExceeded):
        b2.spend(1, 1.5)


def test_inconclusive_rule() -> None:
    v = plan_mod.verdict
    # beats the baseline by 10 points, repeat range of each arm (2) is smaller than the difference
    assert v([80, 81, 82, 81, 80], [91, 92, 93, 92, 91], pass_mark=90) == "improved"
    # the difference is under the 5 point margin
    assert v([90, 91, 92, 91, 90], [94, 95, 96, 95, 94], pass_mark=90) == "inconclusive"
    # beats the margin but the repeat range is as wide as the difference
    assert v([80, 90, 85, 82, 88], [91, 99, 92, 96, 94], pass_mark=90) == "inconclusive"
    # every arm misses the pass mark
    assert v([50, 51, 50, 52, 50], [70, 71, 70, 72, 70], pass_mark=90) == "inconclusive"
    # the new arm is worse
    assert v([95, 95, 95, 95, 95], [90, 90, 90, 90, 90], pass_mark=90) == "inconclusive"
    with pytest.raises(plan_mod.EvalError):
        v([], [1], pass_mark=90)


def test_no_model_score_in_report() -> None:
    assert "withheld" in report.model_score_text(19, kappa=0.9)
    assert "screening only" in report.model_score_text(20, kappa=0.5)
    assert "screening only" in report.model_score_text(25, kappa=None)
    assert "kappa 0.70" in report.model_score_text(25, kappa=0.7)
    res = Results(suite="tone", state="verified", n=10, grader="model", rates=[Rate("score", 1, 10)])
    text = "\n".join(report.render(res))
    assert "withheld" in text and "1/10" not in text and "score:" not in text


def test_abstention_is_its_own_class(tmp_path: Path) -> None:
    rows = [*_tiny_rows(), {"id": "n2", "label": "normal", "text": "   "}]
    d = _write_set(tmp_path / "set", rows)
    suite = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(d)}, repo_root=tmp_path / "other")
    res = suite.run(suite.load(DATASETS))
    assert res.abstain == 1 and sum(r.n for r in res.rates) == 3
    assert "abstain: 1" in "\n".join(report.render(res))


def test_radar_receipt_names_count_hash_and_author_role() -> None:
    suite = radar_mod.RadarSuite()
    ds = suite.load(DATASETS)
    res = suite.run(ds)
    text = report.radar_receipt(res, date="2026-11-10")
    assert f"dataset_sha256: {ds.sha256}" in text
    assert "author_role: builder, synthetic" in text
    assert f"total {len(ds.cases)}" in text and "emergency 36 (misses 0)" in text
    assert text.splitlines()[0].startswith("This receipt proves")
    assert not any(c["text"] in text for c in ds.cases)  # counts and hash only, no phrase


def test_radar_fixture_passes_and_is_synthetic() -> None:
    ds = radar_mod.RadarSuite().load(DATASETS)
    res = radar_mod.RadarSuite().run(ds)
    assert res.state == "verified"
    by = {r.name: r for r in res.rates}
    assert by["emergency misses"].k == 0 and by["scam misses"].k == 0
    assert by["normal false alarms"].max_k is None  # reported, not gated
    assert {c["lang"] for c in ds.cases} == {"hi", "hinglish", "en"}
    blob = (DATASETS / "radar" / "cases.jsonl").read_text()
    assert "+91" not in blob


def test_radar_miss_fails_the_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = radar_mod.RadarSuite().load(DATASETS)
    monkeypatch.setattr(radar_mod, "scan", lambda text: radar_guard.RadarResult())
    res = radar_mod.RadarSuite().run(ds)
    assert res.state == "invalid" and res.failures
    assert run.exit_code([res]) == 1


def test_heldout_absent_is_unavailable_not_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HELDOUT, raising=False)
    suite = radar_mod.RadarHeldoutSuite(env={}, repo_root=tmp_path)
    res = run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite})
    assert res.state == "unavailable" and res.reason
    assert run.exit_code([res]) == 0 and run.exit_code([res], strict=True) == 1


def test_heldout_loaded_from_env_path_outside_repo(tmp_path: Path) -> None:
    d = _write_set(tmp_path / "outside" / "set", _tiny_rows())
    suite = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(d)}, repo_root=tmp_path / "repo")
    res = run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite})
    assert res.state == "verified" and res.author_role == "caregiver"


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / ".gitignore").write_text("private/\n")
    return path


def test_heldout_inside_repo_tracked_area_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    d = _write_set(repo / "evals" / "datasets" / "heldout", _tiny_rows())
    suite = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(d)}, repo_root=repo)
    res = run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite})
    assert res.state == "invalid" and "outside the repository" in res.reason
    ignored = _write_set(repo / "private" / "evals" / "set", _tiny_rows())
    ok = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(ignored)}, repo_root=repo)
    assert run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": ok}).state == "verified"


def test_heldout_ignored_check_uses_git(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("secret_sets/\n")  # an ignore rule the old prefix check did not know
    d = _write_set(repo / "secret_sets" / "set", _tiny_rows())
    suite = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(d)}, repo_root=repo)
    assert run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite}).state == "verified"
    # a folder named private that git does not ignore is tracked area
    (repo / ".gitignore").write_text("")
    d2 = _write_set(repo / "private" / "evals" / "set", _tiny_rows())
    suite2 = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(d2)}, repo_root=repo)
    assert run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite2}).state == "invalid"


def _heldout_res(tmp_path: Path, d: Path):
    suite = radar_mod.RadarHeldoutSuite(env={HELDOUT: str(d)}, repo_root=tmp_path / "repo")
    return run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite})


def test_heldout_symlink_to_public_fixture_refused(tmp_path: Path) -> None:
    d = tmp_path / "outside" / "set"
    d.mkdir(parents=True)
    for name in ("cases.jsonl", "manifest.json"):
        (d / name).symlink_to(DATASETS / "radar" / name)
    res = _heldout_res(tmp_path, d)
    assert res.state == "invalid"


def test_heldout_copy_of_public_fixture_refused(tmp_path: Path) -> None:
    d = tmp_path / "outside" / "set"
    shutil.copytree(DATASETS / "radar", d)
    m = json.loads((d / "manifest.json").read_text())
    m["suite"] = "radar_heldout"
    (d / "manifest.json").write_text(json.dumps(m))
    res = _heldout_res(tmp_path, d)
    assert res.state == "invalid" and "committed" in res.reason


def test_heldout_manifest_suite_must_be_radar_heldout(tmp_path: Path) -> None:
    d = _write_set(tmp_path / "outside" / "set", _tiny_rows())
    m = json.loads((d / "manifest.json").read_text())
    m["suite"] = "radar"
    (d / "manifest.json").write_text(json.dumps(m))
    res = _heldout_res(tmp_path, d)
    assert res.state == "invalid" and "radar_heldout" in res.reason


def test_heldout_text_from_public_fixture_refused(tmp_path: Path) -> None:
    public = json.loads((DATASETS / "radar" / "cases.jsonl").read_text().splitlines()[0])["text"]
    rows = [*_tiny_rows(), {"id": "x1", "label": "emergency", "text": public}]
    res = _heldout_res(tmp_path, _write_set(tmp_path / "outside" / "set", rows))
    assert res.state == "invalid" and "public" in res.reason


def test_heldout_env_set_but_missing_is_invalid(tmp_path: Path) -> None:
    res = _heldout_res(tmp_path, tmp_path / "nope")
    assert res.state == "invalid"


def test_heldout_present_but_incomplete_is_invalid(tmp_path: Path) -> None:
    d = _write_set(tmp_path / "outside" / "set", _tiny_rows())
    (d / "cases.jsonl").unlink()
    assert _heldout_res(tmp_path, d).state == "invalid"
    d2 = _write_set(tmp_path / "outside" / "set2", _tiny_rows())
    (d2 / "manifest.json").unlink()
    assert _heldout_res(tmp_path, d2).state == "invalid"


def test_heldout_default_folder_absent_is_unavailable(tmp_path: Path) -> None:
    suite = radar_mod.RadarHeldoutSuite(env={}, repo_root=tmp_path / "repo")
    res = run.run_row(_row("radar_heldout"), DATASETS, {"radar_heldout": suite})
    assert res.state == "unavailable"


def test_radar_crash_is_an_error_and_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = radar_mod.RadarSuite().load(DATASETS)
    crash = radar_guard.RadarResult(emergency=True, matches=("radar_error",), rule_ids=("RD-ERR-1",))
    monkeypatch.setattr(radar_mod, "scan", lambda text: crash)
    res = radar_mod.RadarSuite().run(ds)
    assert res.state == "invalid" and res.errors > 0
    by = {r.name: r for r in res.rates}
    assert by["emergency misses"].k == by["emergency misses"].n
    assert by["scam misses"].k == by["scam misses"].n
    assert run.exit_code([res]) == 1


def test_radar_errors_alone_fail_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = radar_mod.RadarSuite().load(DATASETS)
    real = radar_mod.scan
    first = ds.cases[0]["text"]
    crash = radar_guard.RadarResult(emergency=True, scam=True, matches=("radar_error",), rule_ids=("RD-ERR-1",))
    monkeypatch.setattr(radar_mod, "scan", lambda text: crash if text == first else real(text))
    res = radar_mod.RadarSuite().run(ds)
    assert res.errors == 1 and res.state == "invalid"


def _patch_redact(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    monkeypatch.setattr(redaction_mod, "redact", lambda text: type("R", (), {"text": fn(text)})())


def test_redaction_partial_leak_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = redaction_mod.RedactionSuite().load(DATASETS)
    canaries = {c["text"]: c["canary"] for c in ds.cases}

    def mask_one_char(text: str) -> str:
        canary = canaries[text]
        return text.replace(canary, "X" + canary[1:])

    _patch_redact(monkeypatch, mask_one_char)
    res = redaction_mod.RedactionSuite().run(ds)
    assert res.state == "invalid" and all(r.k > 0 for r in res.rates)


def test_redaction_assert_clean_failure_is_a_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    ds = redaction_mod.RedactionSuite().load(DATASETS)
    canaries = {c["text"]: c["canary"] for c in ds.cases}
    _patch_redact(monkeypatch, lambda text: text.replace(canaries[text], "<GONE>") + " call 9876543210")
    res = redaction_mod.RedactionSuite().run(ds)
    assert res.state == "invalid" and res.failures


def test_a_crashing_suite_is_invalid_and_others_still_print(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(radar_mod, "heldout_dir", lambda *a, **k: None)

    def boom(self, ds):
        raise RuntimeError("boom")

    monkeypatch.setattr(redaction_mod.RedactionSuite, "run", boom)
    code = run.main(["--all"])
    out = capsys.readouterr().out
    assert code == 1
    assert "radar: verified" in out
    assert "redaction: invalid" in out and "RuntimeError" in out and "boom" not in out
    assert "summary:" in out


def test_radar_receipt_has_bounds_and_keyword_author_line() -> None:
    suite = radar_mod.RadarSuite()
    res = suite.run(suite.load(DATASETS))
    text = report.radar_receipt(res, date="2026-11-10")
    assert "95% upper bound" in text and "n needed" in text
    assert "regression set" in text and "never saw" not in text
    res.suite = "radar_heldout"
    text = report.radar_receipt(res, date="2026-11-10")
    assert "keyword author never saw this set" in text


def test_public_radar_set_is_labelled_a_regression_set() -> None:
    m = json.loads((DATASETS / "radar" / "manifest.json").read_text())
    assert "seeded" not in m["author"] and "regression" in m["author"]
    assert "regression" in _row("radar").dataset and "regression" in _row("radar").split


def test_readme_points_to_registry() -> None:
    text = Path(run.REPO_ROOT, "evals", "README.md").read_text()
    assert "registry.toml" in text and "| Suite | Phase |" not in text


def test_scan_never_loads_the_heldout_set() -> None:
    assert list(inspect.signature(scan).parameters) == ["text"]
    src = inspect.getsource(radar_guard)
    assert "HELDOUT" not in src and "evals" not in src


def test_no_heldout_content_committed() -> None:
    assert not list(Path(run.REPO_ROOT, "evals").rglob("*heldout*"))
    assert not any(p.name == "radar_heldout" for p in DATASETS.iterdir())


def test_redaction_fixture_passes_with_30_per_type() -> None:
    suite = redaction_mod.RedactionSuite()
    ds = suite.load(DATASETS)
    res = suite.run(ds)
    assert res.state == "verified"
    assert {r.n for r in res.rates} == {30} and len(res.rates) == 9
    assert all(r.k == 0 for r in res.rates)


def test_redaction_leak_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    suite = redaction_mod.RedactionSuite()
    ds = suite.load(DATASETS)
    monkeypatch.setattr(redaction_mod, "redact", lambda text: type("R", (), {"text": text})())
    res = suite.run(ds)
    assert res.state == "invalid" and all(r.k == r.n for r in res.rates)
    assert run.exit_code([res]) == 1


def test_redaction_seal_catches_generator_change(tmp_path: Path) -> None:
    shutil.copytree(DATASETS, tmp_path / "ds")
    p = tmp_path / "ds" / "redaction" / "manifest.json"
    m = json.loads(p.read_text())
    m["seed"] += 1
    p.write_text(json.dumps(m))
    res = run.run_row(_row("redaction"), tmp_path / "ds")
    assert res.state == "invalid" and "sealed sha256" in res.reason


def test_redaction_canaries_are_not_committed() -> None:
    text = "".join(p.read_text() for p in (DATASETS / "redaction").iterdir())
    assert "canary" not in text.replace("canary generator", "")


def test_pending_suites_report_reason_not_pass() -> None:
    pending = [r for r in load_registry() if r.runner == "pending"]
    for row in pending:
        res = run.run_row(row, DATASETS)
        assert res.state == "unavailable" and res.reason
    assert _row("trace_canary").runner == "harness"  # WU-19: the trace canary grades the real exporter
