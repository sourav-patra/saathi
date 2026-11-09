"""Deterministic policy engine — the ONLY gate in front of any commit.

Rule #1 of Saathi: the LLM proposes, this code commits. Nothing here calls a
model. Every branch is unit-tested (tests/test_policy.py) and must stay at
100% pass before any release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from saathi.domain import Intent, PaymentMethod, Quote
from saathi.policy.quote import ApprovalToken, quote_hash, quote_hash_without_total


class Verdict(StrEnum):
    OK = "ok"
    NEEDS_SECOND_CONFIRMATION = "needs_second_confirmation"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyConfig:
    # Spending caps are personal: no public defaults, supplied from private config.
    per_order_cap: Decimal
    per_day_cap: Decimal
    monthly_soft_cap: Decimal
    allowed_payment_methods: frozenset[PaymentMethod] = frozenset(
        {PaymentMethod.COD, PaymentMethod.WALLET}
    )
    ride_fare_tolerance_pct: Decimal = Decimal("10")
    ride_fare_tolerance_abs: Decimal = Decimal("20")
    ride_quote_ttl: timedelta = timedelta(minutes=3)
    cart_quote_ttl: timedelta = timedelta(minutes=30)
    blocked_categories: frozenset[str] = frozenset({"alcohol", "tobacco", "gift_card", "gold"})
    # category -> threshold above which a second confirmation is required
    # (no public default: thresholds are personal, supplied from private config)
    second_confirm_categories: dict[str, Decimal] = field(default_factory=dict)
    duplicate_window: timedelta = timedelta(hours=2)
    allowed_address_ids: frozenset[str] | None = None  # None = not yet configured -> deny


@dataclass(frozen=True)
class RecentOrder:
    service: str
    skus: frozenset[str]
    committed_at: datetime


@dataclass(frozen=True)
class PolicyContext:
    paused: bool
    spent_today: Decimal
    spent_month: Decimal
    recent_orders: tuple[RecentOrder, ...] = ()


@dataclass
class PolicyDecision:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.verdict != Verdict.DENY


@dataclass
class CommitAuthorization:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def _ttl(cfg: PolicyConfig, quote: Quote) -> timedelta:
    return cfg.ride_quote_ttl if quote.intent == Intent.RIDE else cfg.cart_quote_ttl


class PolicyEngine:
    def __init__(self, config: PolicyConfig, secret: bytes):
        if len(secret) < 32:
            raise ValueError("approval secret must be at least 32 bytes")
        self.cfg = config
        self._secret = secret

    # ------------------------------------------------------------------ #
    # Stage 1: before showing the approval card
    # ------------------------------------------------------------------ #
    def evaluate(self, quote: Quote, ctx: PolicyContext, now: datetime) -> PolicyDecision:
        deny: list[str] = []
        confirm: list[str] = []
        alerts: list[str] = []
        cfg = self.cfg

        if ctx.paused:
            deny.append("paused: /pause is active")

        if quote.payment_method not in cfg.allowed_payment_methods:
            deny.append(f"payment method '{quote.payment_method}' not allowed (COD/wallet only)")

        if quote.currency != "INR":
            deny.append(f"unsupported currency {quote.currency}")

        if quote.intent != Intent.RIDE and quote.intent != Intent.APPOINTMENT and not quote.items:
            deny.append("empty cart")

        if quote.intent == Intent.RIDE and not (quote.pickup and quote.drop and quote.ride_type):
            deny.append("ride quote missing pickup, drop or ride type")

        if quote.items:
            computed = sum((i.total for i in quote.items), Decimal("0"))
            # Delivery/handling fees can make the total larger, never smaller.
            if quote.total < computed:
                deny.append(f"total {quote.total} is below item sum {computed}")

        if quote.intent != Intent.RIDE:
            if cfg.allowed_address_ids is None:
                deny.append("address allowlist not configured")
            elif quote.address_id not in cfg.allowed_address_ids:
                deny.append(f"address '{quote.address_id}' not in allowlist")

        for item in quote.items:
            if item.category in cfg.blocked_categories:
                deny.append(f"blocked category '{item.category}': {item.name}")
            threshold = cfg.second_confirm_categories.get(item.category)
            if threshold is not None and item.total > threshold:
                confirm.append(f"{item.category} item over ₹{threshold}: {item.name}")

        if quote.total > cfg.per_order_cap:
            confirm.append(f"order ₹{quote.total} exceeds per-order cap ₹{cfg.per_order_cap}")
        if ctx.spent_today + quote.total > cfg.per_day_cap:
            confirm.append(
                f"day spend would be ₹{ctx.spent_today + quote.total} "
                f"(cap ₹{cfg.per_day_cap})"
            )
        if ctx.spent_month + quote.total > cfg.monthly_soft_cap:
            alerts.append(
                f"monthly spend would be ₹{ctx.spent_month + quote.total} "
                f"(soft cap ₹{cfg.monthly_soft_cap})"
            )

        skus = frozenset(i.sku for i in quote.items)
        for prev in ctx.recent_orders:
            if (
                prev.service == quote.service
                and skus
                and skus == prev.skus
                and now - prev.committed_at <= cfg.duplicate_window
            ):
                confirm.append("possible duplicate of an order placed in the last 2 hours")
                break

        if deny:
            return PolicyDecision(Verdict.DENY, deny + confirm, alerts)
        if confirm:
            return PolicyDecision(Verdict.NEEDS_SECOND_CONFIRMATION, confirm, alerts)
        return PolicyDecision(Verdict.OK, [], alerts)

    # ------------------------------------------------------------------ #
    # Stage 2: immediately before the commit, against a LIVE re-read quote
    # ------------------------------------------------------------------ #
    def authorize_commit(
        self,
        *,
        approved_quote: Quote,
        live_quote: Quote,
        token: ApprovalToken,
        ctx: PolicyContext,
        now: datetime,
    ) -> CommitAuthorization:
        reasons: list[str] = []

        if not token.verify(self._secret):
            reasons.append("approval token signature invalid")
        if token.quote_id != approved_quote.id:
            reasons.append("approval token is for a different quote")
        if token.quote_hash != quote_hash(approved_quote):
            reasons.append("approved quote was modified after approval")
        if token.approved_at > now:
            reasons.append("approval timestamp is in the future")

        ttl = _ttl(self.cfg, approved_quote)
        if now - approved_quote.created_at > ttl:
            reasons.append(f"quote expired (older than {int(ttl.total_seconds())}s); re-quote")

        reasons.extend(self._live_matches_approved(approved_quote, live_quote))

        decision = self.evaluate(live_quote, ctx, now)
        if decision.verdict == Verdict.DENY:
            reasons.extend(decision.reasons)
        elif (
            decision.verdict == Verdict.NEEDS_SECOND_CONFIRMATION
            and not token.second_confirmation
        ):
            reasons.append("second confirmation required: " + "; ".join(decision.reasons))

        return CommitAuthorization(allowed=not reasons, reasons=reasons)

    def _live_matches_approved(self, approved: Quote, live: Quote) -> list[str]:
        if approved.intent == Intent.RIDE:
            if quote_hash_without_total(approved) != quote_hash_without_total(live):
                return ["live ride differs from approved ride (route, type or payment)"]
            pct = approved.total * self.cfg.ride_fare_tolerance_pct / Decimal("100")
            allowed = approved.total + max(pct, self.cfg.ride_fare_tolerance_abs)
            if live.total > allowed:
                return [f"live fare ₹{live.total} exceeds approved ₹{approved.total} + tolerance"]
            return []
        if quote_hash(approved) != quote_hash(live):
            return ["live cart differs from approved cart (items, price, address or payment)"]
        return []
