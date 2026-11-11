"""LT-18: the alert plan and the false-alarm counter (M14; RQ-18; TQ-09). Fakes only: `plan` is pure.

The cadence constants are checked as literals, so changing one fails here. PE-30 (false-alarm counter and cadence) has
its tests marked. The six counter scripts are the committed scripts of the `false_alarm` eval suite. Synthetic values.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from markers import rule

from evals.dataset import Dataset, read_manifest
from evals.suites.false_alarm import _RUNNERS, FalseAlarmSuite
from saathi.alerts import plan as plan_mod
from saathi.alerts.plan import (
    AlertConfig,
    clip_due,
    counts_toward_budget,
    false_alarm_count,
    plan,
    review_opens,
    stage_a_offsets,
)
from saathi.domain import AlertView
from saathi.ids import ApproverId

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
P, B = ApproverId("ap-primary"), ApproverId("ap-backup")
DATASETS = Path(__file__).resolve().parent.parent / "evals" / "datasets"


def event(kind: str = "emergency", *, t0: datetime = T0, **fields) -> AlertView:
    base = {"id": "ev1", "household_id": "h1", "elder_id": "e1", "inbox_id": "in1"}
    return AlertView(**{**base, **fields}, kind=kind, t0=t0)  # type: ignore[arg-type]


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def offsets(planned, stage: str, who: ApproverId | None = None) -> list[int]:
    mine = [p for p in planned if p.stage == stage and (who is None or p.approver == who)]
    return [int((p.due_at - T0).total_seconds()) for p in mine]


def run(ev: AlertView, seconds: float, *, approvers=(P, B), so1=True, wa_down=False, cfg: AlertConfig | None = None):
    return plan(ev, list(approvers), at(seconds), so1_valid=so1, whatsapp_down=wa_down, cfg=cfg or AlertConfig())


@rule("PE-30")
def test_cadence_constants_literal():
    assert plan_mod.CADENCE_A_S == (0, 60, 120, 180, 240)
    assert (plan_mod.THEN_EVERY_S, plan_mod.STAGE_A_CAP) == (300, 20)
    assert (plan_mod.STAGE_B_AT_S, plan_mod.STAGE_B_EVERY_S, plan_mod.STAGE_B_MAX) == (120, 120, 3)
    assert (plan_mod.STAGE_C_AT_S, plan_mod.STAGE_C_EVERY_S, plan_mod.STAGE_C_PER_APPROVER) == (240, 180, 3)
    assert plan_mod.BACKUP_CALL_DELAY_S == 60
    assert (plan_mod.REPEAT_CLIP_AT_S, plan_mod.FOLLOWUP_S) == (600, 900)
    assert AlertConfig().voice_calls_per_event == 6
    assert AlertConfig().false_alarm_budget_per_approver_week == 2
    assert stage_a_offsets()[:6] == (0, 60, 120, 180, 240, 540)


@rule("PE-30")
def test_stage_a_cap_20():
    due, nxt = run(event("scam"), 10**6, approvers=(P,))
    sends = offsets(due, "a")
    assert sends == [0, 60, 120, 180, 240] + [240 + 300 * k for k in range(1, 16)]
    assert len(sends) == 20  # the cap: no 21st send at any later time
    assert nxt is None  # every cap is spent


def test_stage_a_first_attempt_is_due_at_t0_for_each_approver():
    due, nxt = run(event(), 0)
    assert [(p.stage, p.approver, p.n) for p in due] == [("a", P, 1), ("a", B, 1)]
    assert nxt == at(60)


def test_stage_a_numbers_are_stable_across_ticks():
    """The same attempt keeps its number however late the plan is made: the idempotency key never changes."""
    early, _ = run(event(), 130, approvers=(P,))
    late, _ = run(event(), 4000, approvers=(P,))
    assert [(p.n, p.due_at) for p in early if p.stage == "a"] == [(p.n, p.due_at) for p in late if p.stage == "a"][:3]


@rule("PE-30")
def test_stage_b_at_120s_three_max():
    assert offsets(run(event(), 119)[0], "b") == []
    due, _ = run(event(), 10**6)
    for who in (P, B):
        assert offsets(due, "b", who) == [120, 240, 360]  # every 2 min, three at most
    assert {(p.approver, p.n) for p in due if p.stage == "b"} == {(w, n) for w in (P, B) for n in (1, 2, 3)}


@rule("PE-30")
def test_stage_c_at_240s_backup_60s_later():
    assert offsets(run(event(), 239)[0], "c") == []
    due, _ = run(event(), 10**6)
    assert offsets(due, "c", P) == [240, 420, 600]  # the primary every 3 min, three calls
    assert offsets(due, "c", B) == [300, 480, 660]  # the backup rings 60 s after the primary
    assert len([p for p in due if p.stage == "c"]) == 6  # voice_calls_per_event, default 6


@rule("PE-30")
def test_voice_calls_per_event_caps_the_fifth_call():
    due, _ = run(event(), 10**6, cfg=AlertConfig(voice_calls_per_event=4))
    calls = sorted(offsets(due, "c"))
    assert calls == [240, 300, 420, 480]  # the fifth, the primary's third, is not placed
    assert offsets(run(event(), 10**6, cfg=AlertConfig(voice_calls_per_event=0))[0], "c") == []


def test_scam_stage_a_only():
    due, nxt = run(event("scam"), 10**6)
    assert {p.stage for p in due} == {"a"}
    assert nxt is None


def test_unscanned_stage_a_only_and_plays_no_clip():
    ev = event("unscanned")
    assert {p.stage for p in run(ev, 10**6)[0]} == {"a"}
    assert clip_due(ev, at(0)) is None and clip_due(ev, at(700)) is None


def test_no_stage_b_or_c_without_a_valid_signoff():
    due, nxt = run(event(), 10**6, so1=False)
    assert {p.stage for p in due} == {"a"}
    assert nxt is None


def test_whatsapp_down_removes_stage_b_only():
    due, _ = run(event(), 10**6, wa_down=True)
    assert {p.stage for p in due} == {"a", "c"}


def test_a_closed_event_plans_nothing():
    for state in ("acknowledged", "blocked"):
        assert run(event(state=state), 10**6) == ([], None)


def test_no_approvers_plans_nothing():
    assert run(event(), 100, approvers=()) == ([], None)


def test_plan_is_pure():
    ev = event()
    assert run(ev, 500) == run(ev, 500)
    assert ev == event()  # the view is frozen and unchanged


def test_next_due_includes_the_ten_minute_clip():
    ev = event(last_elder_clip_at=T0)
    _, nxt = run(ev, 541, approvers=(P,), so1=False)
    assert nxt == at(600)  # between the stage a send at 540 and the one at 840
    _, nxt = run(ev, 601, approvers=(P,), so1=False)
    assert nxt == at(840)


def test_clip_due_first_then_repeat_at_ten_minutes():
    assert clip_due(event(), at(0)) == "first"
    sent = event(last_elder_clip_at=at(1))
    assert clip_due(sent, at(599)) is None
    assert clip_due(sent, at(600)) == "repeat"
    assert clip_due(event(last_elder_clip_at=at(601)), at(900)) is None  # repeated once only
    assert clip_due(event("scam", last_elder_clip_at=at(1)), at(900)) is None  # a scam clip is not repeated
    assert clip_due(event(state="acknowledged"), at(900)) is None  # nothing after an acknowledgement


def test_alert_config_reads_the_household_values():
    from saathi.householdcfg import ApproverEntry, HouseholdConfig

    hh = HouseholdConfig(
        version=1, approvers=[ApproverEntry(approver_id="a", role="primary")], elder_channel_active="whatsapp",
        channels_enabled=frozenset({"whatsapp"}), helper_name="Helper", voice_calls_per_event=4,
        false_alarm_budget_per_approver_week=3,
    )  # fmt: skip
    want = AlertConfig(voice_calls_per_event=4, false_alarm_budget_per_approver_week=3)
    assert AlertConfig.from_household(hh) == want


# --- the false-alarm counter -----------------------------------------------------------------------------------------


def flagged(i: str, kind: str, stage: str, who: ApproverId, *, days_ago: float = 1) -> AlertView:
    return event(
        kind, t0=T0 - timedelta(days=days_ago), id=f"ev-{i}", state="acknowledged", stage=stage, acked_by=who,
        false_alarm=True,
    )  # fmt: skip


@rule("PE-30")
def test_unscanned_not_in_false_alarm_budget():
    assert not counts_toward_budget(flagged("u", "unscanned", "a", P))
    assert not counts_toward_budget(flagged("s", "scam", "a", P))
    events = [flagged(f"u{i}", "unscanned", "a", P) for i in range(5)]
    assert false_alarm_count(events, P, T0) == 0  # five unscanned false alarms: no review
    assert not review_opens(false_alarm_count(events, P, T0), AlertConfig())


@rule("PE-30")
def test_counter_needs_stage_b_or_c_started():
    assert not counts_toward_budget(flagged("a", "emergency", "a", P))
    assert counts_toward_budget(flagged("b", "emergency", "b", P))
    assert counts_toward_budget(flagged("c", "emergency", "c", P))
    assert not counts_toward_budget(event(stage="b", state="acknowledged", false_alarm=False))  # no flag, no count


@rule("PE-30")
def test_review_opens_once_per_crossing_and_never_before_the_budget():
    cfg = AlertConfig()
    assert [review_opens(n, cfg) for n in range(5)] == [False, False, False, True, False]  # budget 2: the third event
    assert [review_opens(n, AlertConfig(false_alarm_budget_per_approver_week=3)) for n in range(6)][3:] == [
        False, True, False,
    ]  # fmt: skip


@rule("PE-30")
def test_counter_window_is_seven_days_and_per_approver():
    now = T0
    mine = [flagged("1", "emergency", "b", P, days_ago=6.9), flagged("2", "emergency", "c", P, days_ago=7.1)]
    assert false_alarm_count(mine, P, now) == 1  # the 7.1 day old one dropped out
    assert false_alarm_count(mine, B, now) == 0  # and it never counted for the other approver


def _scripts() -> Dataset:
    return FalseAlarmSuite().load(DATASETS)


@pytest.mark.parametrize("index", range(6), ids=lambda i: f"script {i + 1}")
@rule("PE-30")
def test_six_counter_scripts(index: int):
    ds = _scripts()
    assert len(ds.cases) == 6
    script = ds.cases[index]
    assert _RUNNERS[script["kind"]](script) == [], script["id"]


@rule("PE-30")
def test_changing_the_cadence_constant_fails_the_suite(monkeypatch: pytest.MonkeyPatch):
    """The suite is shown to fail: one changed constant makes a script fail and the suite invalid."""
    suite = FalseAlarmSuite()
    ds = suite.load(DATASETS)
    assert suite.run(ds).state == "verified"
    monkeypatch.setattr(plan_mod, "CADENCE_A_S", (0, 60, 120, 180, 300))
    res = suite.run(ds)
    assert res.state == "invalid" and "fa-6-stage-a-cadence" in res.failures


@rule("PE-30")
@pytest.mark.parametrize(
    "constant, value, script",
    [
        ("STAGE_A_CAP", 19, "fa-6-stage-a-cadence"),
        ("THEN_EVERY_S", 240, "fa-6-stage-a-cadence"),
        ("BACKUP_CALL_DELAY_S", 90, "fa-5-calls-per-event"),
        ("STAGE_C_AT_S", 180, "fa-5-calls-per-event"),
        ("FALSE_ALARM_WINDOW", timedelta(days=10), "fa-4-older-than-7-days"),
    ],
)  # fmt: skip
def test_changing_any_other_constant_fails_the_suite(monkeypatch: pytest.MonkeyPatch, constant, value, script):
    suite = FalseAlarmSuite()
    ds = suite.load(DATASETS)
    monkeypatch.setattr(plan_mod, constant, value)
    res = suite.run(ds)
    assert res.state == "invalid" and script in res.failures


def test_the_scripts_are_sealed_and_synthetic():
    manifest = read_manifest(DATASETS / "false_alarm", "false_alarm")
    assert manifest.n == 6
    text = (DATASETS / "false_alarm" / "scripts.json").read_text()
    assert len(json.loads(text)) == 6
    assert "@" not in text and "+91" not in text  # ids and counts only


# --- the radar phrase table is pinned (SPEC safety evidence section 3: fails if a keyword row changes) ---------------
RADAR_TABLE_SHA256 = "2b72571fbffd4e05873f14f48d5a3e7a16eed699ab47513f918c140ca9aa5807"


def _radar_table_hash() -> str:
    import hashlib

    from saathi.guards import radar

    table = {
        name: list(getattr(radar, name))
        for name in ("EMERGENCY_ROMAN", "EMERGENCY_DEVANAGARI", "SCAM_ROMAN", "SCAM_DEVANAGARI", "_URGENCY_ROMAN")
    }
    return hashlib.sha256(json.dumps(table, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@rule("PE-30")
def test_the_radar_phrase_table_has_not_changed():
    """A change to any phrase row fails here. Adding a phrase is allowed: add it with its own radar test, then update
    the pinned hash in the same commit. Removing a phrase needs a test that says why: recall is the point."""
    assert _radar_table_hash() == RADAR_TABLE_SHA256, "the radar phrase table changed; see the docstring"
