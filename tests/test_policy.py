"""The policy engine is the last line of defence; every rule gets a test."""

import sys
import tomllib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from markers import rule

from saathi.domain import Intent, LineItem, PaymentMethod, Quote
from saathi.policy.engine import (
    PolicyConfig,
    PolicyContext,
    PolicyEngine,
    RecentOrder,
    Verdict,
)
from saathi.policy.quote import issue_approval, quote_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from rule_index import collect_markers  # noqa: E402

SECRET = b"x" * 32
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
CTX = PolicyContext(paused=False, spent_today=Decimal("0"), spent_month=Decimal("0"))


def engine(**overrides) -> PolicyEngine:
    params = dict(
        per_order_cap=Decimal("700"),
        per_day_cap=Decimal("2200"),
        monthly_soft_cap=Decimal("9000"),
        allowed_address_ids=frozenset({"home"}),
    )
    params.update(overrides)
    cfg = PolicyConfig(**params)
    return PolicyEngine(cfg, SECRET)


def cart(**kw) -> Quote:
    base = dict(
        household_id="h1",
        intent=Intent.GROCERY,
        service="instamart",
        items=[LineItem(sku="atta5", name="Atta 5kg", quantity=1, unit_price=Decimal("280"))],
        address_id="home",
        payment_method=PaymentMethod.COD,
        total=Decimal("300"),
        created_at=NOW,
    )
    base.update(kw)
    return Quote(**base)


def ride(**kw) -> Quote:
    base = dict(
        household_id="h1",
        intent=Intent.RIDE,
        service="rapido",
        ride_type="auto",
        pickup="home",
        drop="temple",
        payment_method=PaymentMethod.COD,
        total=Decimal("120"),
        created_at=NOW,
    )
    base.update(kw)
    return Quote(**base)


# ---------------------------------------------------------------- evaluate()
@rule("PE-17")
def test_happy_cart_ok():
    assert engine().evaluate(cart(), CTX, NOW).verdict == Verdict.OK


@pytest.mark.parametrize("pm", [PaymentMethod.UPI, PaymentMethod.CARD, PaymentMethod.PAY_LATER])
@rule("PE-01")
def test_non_cod_payment_denied(pm):
    d = engine().evaluate(cart(payment_method=pm), CTX, NOW)
    assert d.verdict == Verdict.DENY


@rule("PE-01")
def test_wallet_allowed():
    assert engine().evaluate(cart(payment_method=PaymentMethod.WALLET), CTX, NOW).allowed


@rule("PE-02")
def test_paused_denies_everything():
    ctx = PolicyContext(paused=True, spent_today=Decimal(0), spent_month=Decimal(0))
    assert engine().evaluate(cart(), ctx, NOW).verdict == Verdict.DENY


@rule("PE-03")
def test_unknown_address_denied():
    assert engine().evaluate(cart(address_id="elsewhere"), CTX, NOW).verdict == Verdict.DENY


@rule("PE-03")
def test_unconfigured_address_allowlist_denies():
    e = PolicyEngine(
        PolicyConfig(Decimal("700"), Decimal("2200"), Decimal("9000")), SECRET
    )
    assert e.evaluate(cart(), CTX, NOW).verdict == Verdict.DENY


@rule("PE-04")
def test_blocked_category_denied():
    item = LineItem(sku="b", name="Beer", quantity=1, unit_price=Decimal("150"), category="alcohol")
    assert engine().evaluate(cart(items=[item], total=Decimal("150")), CTX, NOW).verdict == Verdict.DENY


@rule("PE-05")
def test_total_below_item_sum_denied():
    assert engine().evaluate(cart(total=Decimal("100")), CTX, NOW).verdict == Verdict.DENY


@rule("PE-05")
def test_empty_cart_denied():
    assert engine().evaluate(cart(items=[], total=Decimal("0")), CTX, NOW).verdict == Verdict.DENY


@rule("PE-06", "PE-15")
def test_over_order_cap_needs_second_confirmation():
    item = LineItem(sku="m", name="Mixer", quantity=1, unit_price=Decimal("900"))
    d = engine().evaluate(cart(items=[item], total=Decimal("900")), CTX, NOW)
    assert d.verdict == Verdict.NEEDS_SECOND_CONFIRMATION


@rule("PE-06")
def test_day_cap_needs_second_confirmation():
    ctx = PolicyContext(paused=False, spent_today=Decimal("2000"), spent_month=Decimal("2000"))
    assert engine().evaluate(cart(), ctx, NOW).verdict == Verdict.NEEDS_SECOND_CONFIRMATION


@rule("PE-06")
def test_monthly_soft_cap_only_alerts():
    ctx = PolicyContext(paused=False, spent_today=Decimal("0"), spent_month=Decimal("8800"))
    d = engine().evaluate(cart(), ctx, NOW)
    assert d.verdict == Verdict.OK and d.alerts


@rule("PE-07")
def test_electronics_over_threshold_needs_confirmation():
    item = LineItem(sku="e", name="Earphones", quantity=1, unit_price=Decimal("600"), category="electronics")
    e = engine(second_confirm_categories={"electronics": Decimal("500")})
    d = e.evaluate(cart(items=[item], total=Decimal("600")), CTX, NOW)
    assert d.verdict == Verdict.NEEDS_SECOND_CONFIRMATION


@rule("PE-08")
def test_duplicate_within_window_flagged():
    ctx = PolicyContext(
        paused=False, spent_today=Decimal(0), spent_month=Decimal(0),
        recent_orders=(RecentOrder("instamart", frozenset({"atta5"}), NOW - timedelta(minutes=30)),),
    )
    assert engine().evaluate(cart(), ctx, NOW).verdict == Verdict.NEEDS_SECOND_CONFIRMATION


@rule("PE-08")
def test_duplicate_outside_window_ok():
    ctx = PolicyContext(
        paused=False, spent_today=Decimal(0), spent_month=Decimal(0),
        recent_orders=(RecentOrder("instamart", frozenset({"atta5"}), NOW - timedelta(hours=3)),),
    )
    assert engine().evaluate(cart(), ctx, NOW).verdict == Verdict.OK


@rule("PE-09")
def test_ride_missing_fields_denied():
    assert engine().evaluate(ride(drop=None), CTX, NOW).verdict == Verdict.DENY


@rule("PE-03")
def test_ride_does_not_need_address_allowlist():
    assert engine().evaluate(ride(), CTX, NOW).verdict == Verdict.OK


@rule("PE-10")
def test_short_secret_rejected():
    with pytest.raises(ValueError):
        PolicyEngine(PolicyConfig(Decimal("700"), Decimal("2200"), Decimal("9000")), b"short")


# ---------------------------------------------------------- authorize_commit()
def _approve(q: Quote, **kw):
    return issue_approval(q, approver_id="a1", approved_at=NOW, secret=SECRET, **kw)


@rule("PE-31")
def test_config_cannot_widen_payment_methods():
    """PL-36: a setting that allows every method still denies all but COD and wallet."""
    eng = engine(allowed_payment_methods=frozenset(PaymentMethod))
    outside = set(PaymentMethod) - {PaymentMethod.COD, PaymentMethod.WALLET}
    assert outside
    for pm in outside:
        q = cart(payment_method=pm)
        d = eng.evaluate(q, CTX, NOW)
        assert d.verdict == Verdict.DENY, pm
        assert any("payment method" in r for r in d.reasons)
        auth = eng.authorize_commit(approved_quote=q, live_quote=q, token=_approve(q), ctx=CTX, now=NOW)
        assert not auth.allowed, pm
    for pm in (PaymentMethod.COD, PaymentMethod.WALLET):
        assert eng.evaluate(cart(payment_method=pm), CTX, NOW).allowed


@rule("PE-17")
def test_commit_happy_path():
    q = cart()
    auth = engine().authorize_commit(
        approved_quote=q, live_quote=q.model_copy(), token=_approve(q), ctx=CTX, now=NOW + timedelta(minutes=1)
    )
    assert auth.allowed, auth.reasons


@rule("PE-11")
def test_commit_rejects_forged_signature():
    q = cart()
    tok = _approve(q).model_copy(update={"signature": "0" * 64})
    auth = engine().authorize_commit(approved_quote=q, live_quote=q, token=tok, ctx=CTX, now=NOW)
    assert not auth.allowed


@rule("PE-11")
def test_commit_rejects_token_signed_with_other_secret():
    q = cart()
    tok = issue_approval(q, approver_id="a1", approved_at=NOW, secret=b"y" * 32)
    assert not engine().authorize_commit(approved_quote=q, live_quote=q, token=tok, ctx=CTX, now=NOW).allowed


@rule("PE-12")
def test_commit_rejects_modified_approved_quote():
    q = cart()
    tok = _approve(q)
    # Stay below spending caps: only the token hash must reject this modification.
    tampered = q.model_copy(update={"total": Decimal("320")})
    auth = engine().authorize_commit(approved_quote=tampered, live_quote=tampered, token=tok, ctx=CTX, now=NOW)
    assert not auth.allowed
    assert auth.reasons == ["approved quote was modified after approval"], auth.reasons


@rule("PE-13")
def test_commit_rejects_live_price_change():
    q = cart()
    live = q.model_copy(update={"total": Decimal("320")})
    auth = engine().authorize_commit(approved_quote=q, live_quote=live, token=_approve(q), ctx=CTX, now=NOW)
    assert not auth.allowed


@rule("PE-01")
def test_commit_rejects_live_payment_switch_to_upi():
    q = cart()
    live = q.model_copy(update={"payment_method": PaymentMethod.UPI})
    assert not engine().authorize_commit(approved_quote=q, live_quote=live, token=_approve(q), ctx=CTX, now=NOW).allowed


@rule("PE-14")
def test_commit_rejects_expired_cart():
    q = cart()
    auth = engine().authorize_commit(
        approved_quote=q, live_quote=q, token=_approve(q), ctx=CTX, now=NOW + timedelta(minutes=31)
    )
    assert not auth.allowed


@rule("PE-02")
def test_commit_rejects_when_paused_after_approval():
    q = cart()
    ctx = PolicyContext(paused=True, spent_today=Decimal(0), spent_month=Decimal(0))
    assert not engine().authorize_commit(approved_quote=q, live_quote=q, token=_approve(q), ctx=ctx, now=NOW).allowed


@rule("PE-15")
def test_commit_requires_second_confirmation_token():
    item = LineItem(sku="m", name="Mixer", quantity=1, unit_price=Decimal("900"))
    q = cart(items=[item], total=Decimal("900"))
    e = engine()
    assert not e.authorize_commit(approved_quote=q, live_quote=q, token=_approve(q), ctx=CTX, now=NOW).allowed
    tok2 = _approve(q, second_confirmation=True)
    assert e.authorize_commit(approved_quote=q, live_quote=q, token=tok2, ctx=CTX, now=NOW).allowed


@rule("PE-16")
def test_ride_fare_within_tolerance_ok():
    q = ride(total=Decimal("120"))
    live = q.model_copy(update={"total": Decimal("140")})  # +20 abs allowed (10% = 12)
    assert engine().authorize_commit(approved_quote=q, live_quote=live, token=_approve(q), ctx=CTX, now=NOW).allowed


@rule("PE-16")
def test_ride_fare_beyond_tolerance_rejected():
    q = ride(total=Decimal("120"))
    live = q.model_copy(update={"total": Decimal("141")})
    assert not engine().authorize_commit(approved_quote=q, live_quote=live, token=_approve(q), ctx=CTX, now=NOW).allowed


@rule("PE-16")
def test_ride_route_change_rejected():
    q = ride()
    live = q.model_copy(update={"drop": "airport"})
    assert not engine().authorize_commit(approved_quote=q, live_quote=live, token=_approve(q), ctx=CTX, now=NOW).allowed


@rule("PE-14")
def test_ride_quote_expires_fast():
    q = ride()
    auth = engine().authorize_commit(
        approved_quote=q, live_quote=q, token=_approve(q), ctx=CTX, now=NOW + timedelta(minutes=4)
    )
    assert not auth.allowed


@rule("PE-12")
def test_hash_ignores_item_order_and_ids():
    a = LineItem(sku="a", name="A", quantity=1, unit_price=Decimal("10"))
    b = LineItem(sku="b", name="B", quantity=2, unit_price=Decimal("5"))
    q1 = cart(items=[a, b], total=Decimal("20"))
    q2 = cart(items=[b, a], total=Decimal("20.00"))
    assert quote_hash(q1) == quote_hash(q2)


def test_rule_markers_name_existing_rules():
    """Every @rule marker in the suite names a rule of scripts/rules.toml (RQ-63)."""
    root = Path(__file__).resolve().parent.parent
    known = {r["id"] for r in tomllib.loads((root / "scripts" / "rules.toml").read_text())["rule"]}
    markers = collect_markers(root / "tests")
    assert markers, "no @rule markers found"
    assert set(markers) <= known
    policy = {t.split("::")[1] for tests in markers.values() for t in tests if t.startswith("test_policy.py")}
    assert len(policy) == 33
