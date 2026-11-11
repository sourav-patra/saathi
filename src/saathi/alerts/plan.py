"""The alert plan (M14; RQ-18; A02 to A05, A09, A11): a pure function of the event, the approvers and the time.

`plan` returns every attempt whose due time has come and the time of the next one. It reads no clock and no store, so
a fake clock drives every case. The same inputs give the same attempts with the same numbers `n`, which is what makes
the idempotency key of an attempt stable across ticks and restarts: a repeat is a conflict, never a second send.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from saathi.domain import AlertView
from saathi.householdcfg import HouseholdConfig
from saathi.ids import ApproverId

Stage = Literal["a", "b", "c"]

# Stage a, Telegram: at these offsets from t0, then every THEN_EVERY_S, at most STAGE_A_CAP sends per approver.
CADENCE_A_S = (0, 60, 120, 180, 240)
THEN_EVERY_S = 300
STAGE_A_CAP = 20
# Stage b, a WhatsApp template (a fake in Phase 0): from 120 s, every 120 s, three at most per approver.
STAGE_B_AT_S = 120
STAGE_B_EVERY_S = 120
STAGE_B_MAX = 3
# Stage c, a voice call (a fake in Phase 0): from 240 s, every 180 s, three per approver; each backup rings later.
STAGE_C_AT_S = 240
STAGE_C_EVERY_S = 180
STAGE_C_PER_APPROVER = 3
BACKUP_CALL_DELAY_S = 60
# The elder hears the 108 and 112 clip again, and a follow-up is asked after an acknowledgement.
REPEAT_CLIP_AT_S = 600
FOLLOWUP_S = 900
# False-alarm counter window (SPEC safety, RQ-18): events of the last seven days.
FALSE_ALARM_WINDOW = timedelta(days=7)
SIGNOFF_NOTICE_DAYS = 14  # RQ-95a: the owner hears of a sign-off 14 days before it ends


@dataclass(frozen=True)
class AlertConfig:
    """The household values the alert path reads (PRD Q18; D-56). Read from the household config row."""

    voice_calls_per_event: int = 6
    false_alarm_budget_per_approver_week: int = 2

    @classmethod
    def from_household(cls, cfg: HouseholdConfig) -> AlertConfig:
        return cls(
            voice_calls_per_event=cfg.voice_calls_per_event,
            false_alarm_budget_per_approver_week=cfg.false_alarm_budget_per_approver_week,
        )


@dataclass(frozen=True)
class Planned:
    stage: Stage
    approver: ApproverId
    n: int
    due_at: datetime


def stage_a_offsets() -> tuple[int, ...]:
    """Seconds from t0 of every stage a send of one approver: 0, 60, 120, 180, 240, then every 300 up to the cap."""
    out = list(CADENCE_A_S)
    while len(out) < STAGE_A_CAP:
        out.append(out[-1] + THEN_EVERY_S)
    return tuple(out[:STAGE_A_CAP])


def _schedule(
    ev: AlertView, approvers: Sequence[ApproverId], *, so1_valid: bool, whatsapp_down: bool, cfg: AlertConfig
) -> list[Planned]:
    """Every attempt the event can ever have, in due order. Time does not enter: only the event and the switches."""
    out: list[Planned] = []

    def at(seconds: int) -> datetime:
        return ev.t0 + timedelta(seconds=seconds)

    for who in approvers:
        out += [Planned("a", who, n, at(s)) for n, s in enumerate(stage_a_offsets(), start=1)]
    if ev.kind == "emergency" and so1_valid:  # a scam or an unscanned alert is stage a only (A01, A11)
        if not whatsapp_down:
            for who in approvers:
                out += [
                    Planned("b", who, n, at(STAGE_B_AT_S + (n - 1) * STAGE_B_EVERY_S))
                    for n in range(1, STAGE_B_MAX + 1)
                ]
        calls: list[Planned] = []
        for rank, who in enumerate(approvers):
            delay = 0 if rank == 0 else BACKUP_CALL_DELAY_S  # the primary rings first, a backup 60 s later
            calls += [
                Planned("c", who, n, at(STAGE_C_AT_S + delay + (n - 1) * STAGE_C_EVERY_S))
                for n in range(1, STAGE_C_PER_APPROVER + 1)
            ]
        order = {who: i for i, who in enumerate(approvers)}
        calls.sort(key=lambda p: (p.due_at, order[p.approver]))
        out += calls[: max(cfg.voice_calls_per_event, 0)]  # the per event cap on calls
    order = {who: i for i, who in enumerate(approvers)}
    out.sort(key=lambda p: (p.due_at, p.stage, order[p.approver]))
    return out


def plan(
    ev: AlertView,
    approvers: Sequence[ApproverId],
    now: datetime,
    *,
    so1_valid: bool,
    whatsapp_down: bool,
    cfg: AlertConfig,
) -> tuple[list[Planned], datetime | None]:
    """The attempts due at `now` and the next time something is due, or None when every cap is spent.

    `approvers` lists the primary first. Stages b and c appear only for an emergency with a valid sign-off SO-1, and
    stage b is left out while WhatsApp is down. A closed event plans nothing.
    """
    if ev.state != "open" or not approvers:
        return [], None
    every = _schedule(ev, approvers, so1_valid=so1_valid, whatsapp_down=whatsapp_down, cfg=cfg)
    due = [p for p in every if p.due_at <= now]
    later = [p.due_at for p in every if p.due_at > now]
    if ev.kind == "emergency" and now < ev.t0 + timedelta(seconds=REPEAT_CLIP_AT_S):
        later.append(ev.t0 + timedelta(seconds=REPEAT_CLIP_AT_S))
    return due, (min(later) if later else None)


def clip_due(ev: AlertView, now: datetime) -> Literal["first", "repeat"] | None:
    """Which elder clip is owed now (A01, A09). An `unscanned` alert plays none: nothing was heard."""
    if ev.state != "open" or ev.kind == "unscanned":
        return None
    if ev.last_elder_clip_at is None:
        return "first"
    if ev.kind == "emergency" and now >= ev.t0 + timedelta(seconds=REPEAT_CLIP_AT_S):
        if ev.last_elder_clip_at < ev.t0 + timedelta(seconds=REPEAT_CLIP_AT_S):
            return "repeat"
    return None


# --------------------------------------------------------------------------- #
# The false-alarm counter (PE-30)
# --------------------------------------------------------------------------- #
def counts_toward_budget(ev: AlertView) -> bool:
    """A false alarm counts once stage b or c had started, so only an emergency can count: an `unscanned` or a scam
    alert never leaves stage a, and a tap before t0 + 2 min finds the event still at stage a."""
    return ev.false_alarm and ev.kind == "emergency" and ev.stage in ("b", "c")


def false_alarm_count(events: Sequence[AlertView], approver: ApproverId, now: datetime) -> int:
    """False-alarm events of this approver in the last seven days that count toward the budget."""
    return sum(
        1
        for e in events
        if e.acked_by == approver and now - e.t0 <= FALSE_ALARM_WINDOW and counts_toward_budget(e)
    )


def review_opens(count: int, cfg: AlertConfig) -> bool:
    """True when `count` has just passed the budget: the third event with the default of 2, the fourth with 3. It opens
    one review per crossing. It never removes a keyword and never changes a stage."""
    return count == cfg.false_alarm_budget_per_approver_week + 1
