"""Plan checks for model-variable and paid runs (RQ-99). Phase 0 validates plans; it never starts a model run."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from evals.dataset import EvalError

REPEATS = 5
MARGIN_PP = 5.0
PLAN_FIELDS = (
    "suite", "repeats", "temperature", "budget_tokens", "budget_inr", "held_out_set", "baseline", "margin_pp",
    "pass_mark", "paid",
)


class ApprovalMissing(EvalError):
    pass


class BudgetExceeded(EvalError):
    pass


@dataclass(frozen=True)
class Plan:
    suite: str
    repeats: int
    temperature: float
    budget_tokens: int
    budget_inr: float
    held_out_set: str
    baseline: str
    margin_pp: float
    pass_mark: float  # pass rate in percent
    paid: bool


def parse_plan(data: dict) -> Plan:
    missing = [f for f in PLAN_FIELDS if f not in data]
    if missing:
        raise EvalError(f"plan lacks fields: {', '.join(missing)}")
    plan = Plan(**{f: data[f] for f in PLAN_FIELDS})
    problems = []
    if plan.repeats < REPEATS:
        problems.append(f"repeats must be at least {REPEATS}")
    if plan.margin_pp < MARGIN_PP:
        problems.append(f"margin_pp must be at least {MARGIN_PP}")
    if plan.budget_tokens <= 0 or plan.budget_inr <= 0:
        problems.append("budget must be positive in tokens and INR")
    if not 0 < plan.pass_mark <= 100:
        problems.append("pass_mark must be in (0, 100]")
    if not str(plan.held_out_set).strip() or not str(plan.baseline).strip():
        problems.append("held_out_set and baseline must be named")
    if problems:
        raise EvalError("plan invalid: " + "; ".join(problems))
    return plan


def load_plan(path: Path) -> Plan:
    return parse_plan(tomllib.loads(path.read_text(encoding="utf-8")))


def load_approvals(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    return list(tomllib.loads(path.read_text(encoding="utf-8")).get("approval", []))


def authorize(plan: Plan, approvals: list[dict]) -> None:
    """A paid run needs an owner approval row for this suite that covers the budget and carries a date."""
    if not plan.paid:
        return
    for row in approvals:
        if (
            row.get("suite") == plan.suite
            and row.get("date")
            and float(row.get("budget_inr", 0)) >= plan.budget_inr
        ):
            return
    raise ApprovalMissing(f"a paid run of {plan.suite} needs an owner approval row (suite, budget, date)")


class Budget:
    """Counts spend during a run and aborts when either limit is passed."""

    def __init__(self, tokens: int, inr: float) -> None:
        self.tokens, self.inr = tokens, inr
        self.used_tokens, self.used_inr = 0, 0.0

    def spend(self, tokens: int, inr: float) -> None:
        self.used_tokens += tokens
        self.used_inr += inr
        if self.used_tokens > self.tokens or self.used_inr > self.inr:
            raise BudgetExceeded("run budget exceeded: aborting")


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def summarise_arm(rates: list[float]) -> dict[str, float]:
    return {"min": min(rates), "median": _median(rates), "max": max(rates)}


def verdict(baseline: list[float], new: list[float], *, margin_pp: float = MARGIN_PP, pass_mark: float) -> str:
    """`improved` only when the new arm beats the baseline by the margin and the repeat range of both arms is smaller
    than the difference. Every arm under the pass mark is `inconclusive`. Anything else is `inconclusive` too."""
    if not baseline or not new:
        raise EvalError("each arm needs at least one repeat")
    b, n = summarise_arm(baseline), summarise_arm(new)
    if max(b["median"], n["median"]) < pass_mark:
        return "inconclusive"
    diff = n["median"] - b["median"]
    spread = max(b["max"] - b["min"], n["max"] - n["min"])
    if diff >= margin_pp and spread < diff:
        return "improved"
    return "inconclusive"
