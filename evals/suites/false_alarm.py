"""False-alarm counter and cadence suite (SPEC evidence section 3; PE-30): six fake-clock scripts, graded by code.

Each script is data in `datasets/false_alarm/scripts.json`: the expected send times of the stage a cadence, the calls a
cap allows, and the counts and review decisions of the false-alarm counter. The runner builds the events the scripts
describe, drives `saathi.alerts.plan` with a fixed clock and compares. A changed cadence constant, call cap, window or
budget rule makes at least one script fail, so the suite is shown to fail by changing the constant. The scripts change
no keyword and no stage.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evals.dataset import Dataset, EvalError, read_manifest, sha256_bytes
from evals.results import Rate, Results
from saathi.alerts import plan as plan_mod
from saathi.domain import AlertView
from saathi.ids import ApproverId

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # the fake clock of every script
N_SCRIPTS = 6
_RANK = {"a": 0, "b": 1, "c": 2}


def _seal(cases: list[dict]) -> str:
    return sha256_bytes(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _view(i: str, kind: str, t0: datetime, **fields) -> AlertView:
    return AlertView(
        id=f"ev-{i}", household_id="h-eval", elder_id="e-eval", inbox_id=f"in-{i}", kind=kind, t0=t0, **fields
    )


def _stage_at(ev: AlertView, approvers: list[ApproverId], at: datetime, cfg: plan_mod.AlertConfig) -> str:
    """The highest stage the plan had started by `at`, with the sign-off valid and WhatsApp up."""
    due, _ = plan_mod.plan(ev, approvers, at, so1_valid=True, whatsapp_down=False, cfg=cfg)
    return max((p.stage for p in due), key=_RANK.__getitem__, default="a")


def _counter(script: dict) -> list[str]:
    fails: list[str] = []
    approvers = [ApproverId(a) for a in script["approvers"]]
    for budget in script["budgets"]:
        cfg = plan_mod.AlertConfig(false_alarm_budget_per_approver_week=budget)
        events: list[AlertView] = []
        opened: list[bool] = []
        for e in script["events"]:
            t0 = NOW - timedelta(days=e["days_ago"])
            probe = _view(e["id"], e["event_kind"], t0)
            marked = t0 + timedelta(seconds=e["marked_at_s"])
            stage = _stage_at(probe, approvers, marked, cfg)  # the stage the event had reached when it was marked
            events.append(_view(e["id"], e["event_kind"], t0, state="acknowledged", stage=stage,
                                acked_by=ApproverId(e["acked_by"]), false_alarm=True))  # fmt: skip
            count = plan_mod.false_alarm_count(events, ApproverId(e["acked_by"]), NOW)
            opened.append(plan_mod.review_opens(count, cfg))
        want = script["expect"]["review_after"][str(budget)]
        if opened != want:
            fails.append(f"{script['id']}: budget {budget} review after each event {opened}, expected {want}")
        for who, n in script["expect"]["counts"].items():
            got = plan_mod.false_alarm_count(events, ApproverId(who), NOW)
            if got != n:
                fails.append(f"{script['id']}: budget {budget} count for {who} is {got}, expected {n}")
    return fails


def _offsets(script: dict, stage: str, *, kind: str = "emergency") -> list[list[int]]:
    approvers = [ApproverId(a) for a in script["approvers"]]
    cfg = plan_mod.AlertConfig(voice_calls_per_event=script.get("voice_calls_per_event", 6))
    ev = _view("x", kind, NOW)
    at = NOW + timedelta(seconds=script["at_s"])
    due, _ = plan_mod.plan(ev, approvers, at, so1_valid=True, whatsapp_down=False, cfg=cfg)
    secs = [(p, int((p.due_at - NOW).total_seconds())) for p in due if p.stage == stage]
    return [[s for p, s in secs if p.approver == who] for who in approvers]


def _calls(script: dict) -> list[str]:
    per_approver = _offsets(script, "c")
    flat = sorted(s for offsets in per_approver for s in offsets)
    want = script["expect"]
    fails = []
    if len(flat) != want["calls"]:
        fails.append(f"{script['id']}: {len(flat)} calls placed, expected {want['calls']}")
    if flat != want["offsets_s"]:
        fails.append(f"{script['id']}: calls at {flat}, expected {want['offsets_s']}")
    return fails


def _cadence(script: dict) -> list[str]:
    [offsets] = _offsets(script, "a")
    want = script["expect"]
    fails = []
    if offsets != want["offsets_s"]:
        fails.append(f"{script['id']}: stage a sends at {offsets}, expected {want['offsets_s']}")
    if len(offsets) != want["sends"]:
        fails.append(f"{script['id']}: {len(offsets)} stage a sends, expected {want['sends']}")
    return fails


_RUNNERS = {"counter": _counter, "calls": _calls, "cadence": _cadence}


class FalseAlarmSuite:
    name = "false_alarm"

    def load(self, root: Path) -> Dataset:
        manifest = read_manifest(root / self.name, self.name)
        path = root / self.name / "scripts.json"
        try:
            scripts = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EvalError(f"dataset {self.name}: scripts.json is not readable ({type(exc).__name__})") from exc
        digest = _seal(scripts)
        if digest != manifest.sha256:
            raise EvalError(f"dataset {self.name}: scripts do not match the sealed sha256")
        if manifest.n != len(scripts) or len(scripts) != N_SCRIPTS:
            raise EvalError(f"dataset {self.name}: expected {N_SCRIPTS} scripts, found {len(scripts)}")
        return Dataset(name=self.name, manifest=manifest, cases=scripts, sha256=digest)

    def run(self, ds: Dataset) -> Results:
        failed_ids: list[str] = []
        for script in ds.cases:
            runner = _RUNNERS.get(script.get("kind", ""))
            if runner is None:
                raise EvalError(f"dataset {self.name}: script {script.get('id')} has an unknown kind")
            if runner(script):
                failed_ids.append(str(script["id"]))
        rates = [Rate("script failures", len(failed_ids), len(ds.cases), target_n=N_SCRIPTS, max_k=0)]
        state = "verified" if not failed_ids else "invalid"
        return Results(
            suite=self.name, state=state, rates=rates, dataset_sha256=ds.sha256, n=len(ds.cases),
            author_role=ds.manifest.author_role, failures=failed_ids,
        )  # fmt: skip
