"""Report format (RQ-59, RQ-60): states in the shared words, every rate with k, n and a bound, no model score."""

from __future__ import annotations

from evals.results import Results
from evals.stats import format_rate

MIN_HUMAN_LABELS = 20
KAPPA_MARGIN = 0.6


def model_score_text(human_labels: int, kappa: float | None = None) -> str:
    """No model score is printed without 20 human labels; after that it is screening until kappa clears."""
    if human_labels < MIN_HUMAN_LABELS:
        return f"model score withheld: {human_labels} human labels, {MIN_HUMAN_LABELS} needed"
    if kappa is None or kappa < KAPPA_MARGIN:
        return "model score screening only: agreement with human labels not yet at the declared margin"
    return f"model score screening: kappa {kappa:.2f} against human labels"


def render(res: Results) -> list[str]:
    head = f"{res.suite}: {res.state}"
    if res.state == "unavailable":
        return [f"{head} ({res.reason or 'NO REASON GIVEN'})"]
    if res.reason:
        return [f"{head} ({res.reason})"]
    lines = [f"{head} (n={res.n}, grader={res.grader})"]
    if res.grader == "model":
        lines.append("  " + model_score_text(0))
        return lines
    for rate in res.rates:
        gate = "" if rate.max_k is None else f" [gate: at most {rate.max_k}]"
        lines.append(f"  {rate.name}: {format_rate(rate.k, rate.n, rate.target_n)}{gate}")
    lines.append(f"  abstain: {res.abstain}, errors: {res.errors}")
    if res.dataset_sha256:
        lines.append(f"  dataset sha256: {res.dataset_sha256}")
    if res.failures:
        lines.append(f"  failing case ids: {', '.join(res.failures[:20])}")
    return lines


def summary(results: list[Results]) -> str:
    counts = {s: sum(r.state == s for r in results) for s in ("verified", "invalid", "unavailable")}
    return f"summary: verified {counts['verified']}, invalid {counts['invalid']}, unavailable {counts['unavailable']}"


def _keyword_author_line(suite: str) -> str:
    if suite == "radar_heldout":
        return "keyword author: the keyword author never saw this set"
    return "keyword author: the keywords were tuned against this regression set, so it is not a held-out measure"


def radar_receipt(res: Results, *, date: str) -> str:
    """The owner receipt for a radar run (RQ-65): counts, dataset hash, author role, result. No phrase, no content."""
    emergency = next(r for r in res.rates if r.name.startswith("emergency"))
    scam = next(r for r in res.rates if r.name.startswith("scam"))
    normal = next(r for r in res.rates if r.name.startswith("normal"))
    lines = [
        f"This receipt proves a radar run on the {res.suite} set: counts and hash only, no phrase.",
        f"date: {date}",
        f"result: {res.state}",
        f"counts: emergency {emergency.n} (misses {emergency.k}), scam {scam.n} (misses {scam.k}), "
        f"normal {normal.n} (false alarms {normal.k}), total {res.n}, errors {res.errors}",
        f"emergency misses: {format_rate(emergency.k, emergency.n, emergency.target_n)}",
        f"scam misses: {format_rate(scam.k, scam.n, scam.target_n)}",
        f"normal false alarms: {format_rate(normal.k, normal.n, normal.target_n)}",
        f"dataset_sha256: {res.dataset_sha256}",
        f"author_role: {res.author_role}",
        _keyword_author_line(res.suite),
    ]
    return "\n".join(lines) + "\n"
