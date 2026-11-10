"""LT-35: the rule index fails on a rule with no test and on an unknown marker (RQ-63)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import rule_index  # noqa: E402

RULES = """
[[rule]]
id = "PE-01"
title = "a"
implements = "x"
phase = 0

[[rule]]
id = "PE-02"
title = "b"
implements = "x"
phase = 1
"""


def _tests(tmp: Path, body: str) -> Path:
    d = tmp / "tests"
    d.mkdir()
    (d / "test_x.py").write_text(body)
    return d


def _rules(tmp: Path) -> Path:
    p = tmp / "rules.toml"
    p.write_text(RULES)
    return p


def test_rule_without_test_fails(tmp_path):
    d = _tests(tmp_path, "def test_a():\n    pass\n")
    findings = rule_index.check_rules(_rules(tmp_path), rule_index.collect_markers(d), 0)
    assert findings == ["rule PE-01 (phase 0) has no test"]
    rules_path = _rules(tmp_path)
    assert rule_index.main(["--phase", "0", "--rules", str(rules_path), "--tests", str(d)]) == 1


def test_unknown_marker_fails(tmp_path):
    body = (
        'from markers import rule\n\n@rule("PE-01")\ndef test_a():\n    pass\n\n'
        '@rule("PE-99")\ndef test_b():\n    pass\n'
    )
    d = _tests(tmp_path, body)
    findings = rule_index.check_rules(_rules(tmp_path), rule_index.collect_markers(d), 0)
    assert len(findings) == 1 and "PE-99" in findings[0]


def test_later_phase_rule_not_evaluated(tmp_path):
    d = _tests(tmp_path, 'from markers import rule\n\n@rule("PE-01")\ndef test_a():\n    pass\n')
    markers = rule_index.collect_markers(d)
    assert rule_index.check_rules(_rules(tmp_path), markers, 0) == []
    assert rule_index.check_rules(_rules(tmp_path), markers, 1) == ["rule PE-02 (phase 1) has no test"]
    assert rule_index.main(["--phase", "0", "--rules", str(_rules(tmp_path)), "--tests", str(d)]) == 0
