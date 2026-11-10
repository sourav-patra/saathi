"""LT-32: Saathi never holds, routes or initiates money movement (POLICY PL-36, rule PE-31).

Three checks, each shown to fail on a planted fault:
1. the Capability set has no payment member;
2. no payment SDK, UPI intent or collect-request call appears in the code tree;
3. the policy engine denies a payment method outside cash on delivery or a wallet
   inside the merchant's own flow.
"""

import ast
import re
import tomllib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from markers import rule

from saathi.domain import OUT_OF_SCOPE_INTENTS, Intent, LineItem, PaymentMethod, Quote
from saathi.policy.engine import PolicyConfig, PolicyContext, PolicyEngine, Verdict

ROOT = Path(__file__).resolve().parent.parent
CODE_TREES = [ROOT / "src", ROOT / "scripts"]
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
CTX = PolicyContext(paused=False, spent_today=Decimal("0"), spent_month=Decimal("0"))
ALLOWED_METHODS = {PaymentMethod.COD, PaymentMethod.WALLET}

# Payment SDKs and gateways, as top-level import names or distribution names.
PAYMENT_MODULES = {
    "razorpay", "stripe", "paytm", "paytmchecksum", "phonepe", "paypalrestsdk", "paypal", "braintree",
    "cashfree_pg", "cashfree_sdk", "payu", "juspay", "billdesk", "pinelabs", "instamojo", "ccavenue",
    "upi", "pyupi", "upipay", "squareup", "adyen", "mollie", "plaid", "dwolla", "wise", "coinbase",
}
# A UPI deep link, a collect request, a payment link, a transfer or a top-up created by Saathi.
PAYMENT_TEXT = re.compile(
    r"(upi://|\bupi_intent\b|\bcollect_request\b|\bcollect-request\b|\bcreate_payment_link\b|"
    r"\bpayment_link\b|\bpay\?pa=|\btop_up\b|\btopup\b|\bwallet_topup\b|\bmoney_transfer\b|"
    r"\bsend_money\b|\brefund_to\b)",
    re.I,
)
PAYMENT_WORDS = re.compile(r"(pay|upi|money|transfer|collect|topup|top_up|refund|balance|wallet|charge)", re.I)


# --------------------------------------------------------------------- helpers
def python_files(*roots: Path) -> list[Path]:
    return sorted(p for r in roots for p in r.rglob("*.py") if ".venv" not in p.parts)


def find_payment_imports(*roots: Path) -> list[str]:
    """Imports of payment SDKs and UPI/collect-request text, by file and line."""
    found: list[str] = []
    for path in python_files(*roots):
        text = path.read_text()
        for node in ast.walk(ast.parse(text)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for n in names:
                if n.split(".")[0].lower() in PAYMENT_MODULES:
                    found.append(f"{path}:{node.lineno}: import {n}")
        for n, line in enumerate(text.splitlines(), 1):
            if m := PAYMENT_TEXT.search(line):
                found.append(f"{path}:{n}: {m.group(0)}")
    return found


def capability_enums(*roots: Path) -> dict[str, list[str]]:
    """Members of every class named Capability under the given trees."""
    out: dict[str, list[str]] = {}
    for path in python_files(*roots):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef) and node.name == "Capability":
                members = [
                    t.id
                    for stmt in node.body
                    if isinstance(stmt, ast.Assign)
                    for t in stmt.targets
                    if isinstance(t, ast.Name)
                ]
                out[f"{path}:{node.lineno}"] = members
    return out


def payment_members(members: list[str]) -> list[str]:
    return [m for m in members if PAYMENT_WORDS.search(m)]


def engine(**overrides) -> PolicyEngine:
    params = dict(
        per_order_cap=Decimal("700"),
        per_day_cap=Decimal("2200"),
        monthly_soft_cap=Decimal("9000"),
        allowed_address_ids=frozenset({"home"}),
    )
    params.update(overrides)
    return PolicyEngine(PolicyConfig(**params), b"x" * 32)


def cart(pm: PaymentMethod) -> Quote:
    return Quote(
        household_id="h1",
        intent=Intent.GROCERY,
        service="instamart",
        items=[LineItem(sku="atta5", name="Atta 5kg", quantity=1, unit_price=Decimal("280"))],
        address_id="home",
        payment_method=pm,
        total=Decimal("300"),
        created_at=NOW,
    )


# ------------------------------------------------- (1) the capability set
@rule("PE-31")
def test_capability_set_has_no_payment():
    # A payment intent may exist only to be recognised and handed to the approver
    # (BILL_PAYMENT); it must stay in OUT_OF_SCOPE_INTENTS.
    for name in payment_members([i.name for i in Intent]):
        assert Intent[name] in OUT_OF_SCOPE_INTENTS, name
    # Every Capability enum in the code tree (QUOTE, PLACE, FIND_ORDER once the ports land).
    for where, members in capability_enums(*CODE_TREES).items():
        assert payment_members(members) == [], where


@rule("PE-31")
def test_capability_check_fails_on_planted_payment_member(tmp_path):
    (tmp_path / "ports.py").write_text(
        "from enum import StrEnum\n\nclass Capability(StrEnum):\n    QUOTE = 'quote'\n    PAY = 'pay'\n"
    )
    planted = capability_enums(tmp_path)
    assert [payment_members(m) for m in planted.values()] == [["PAY"]]


@rule("PE-31")
def test_clean_capability_set_passes(tmp_path):
    (tmp_path / "ports.py").write_text(
        "from enum import StrEnum\n\nclass Capability(StrEnum):\n    QUOTE = 'quote'\n"
        "    PLACE = 'place'\n    FIND_ORDER = 'find_order'\n"
    )
    assert [payment_members(m) for m in capability_enums(tmp_path).values()] == [[]]


# ------------------------------------------------- (2) dependency and import test
@rule("PE-31")
def test_code_tree_has_no_payment_import():
    assert find_payment_imports(*CODE_TREES) == []


@rule("PE-31")
def test_no_payment_sdk_in_declared_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    specs = list(pyproject["project"]["dependencies"])
    for group in pyproject["project"].get("optional-dependencies", {}).values():
        specs += group
    for group in pyproject.get("dependency-groups", {}).values():
        specs += [s for s in group if isinstance(s, str)]
    names = {re.split(r"[<>=!~\[; ]", s, maxsplit=1)[0].lower().replace("-", "_") for s in specs}
    assert names & PAYMENT_MODULES == set()


@rule("PE-31")
def test_import_test_fails_on_planted_payment_import(tmp_path):
    (tmp_path / "bad.py").write_text("import razorpay\nfrom stripe.api import Charge\n")
    found = find_payment_imports(tmp_path)
    assert len(found) == 2 and "razorpay" in found[0] and "stripe.api" in found[1]


@rule("PE-31")
@pytest.mark.parametrize(
    "line",
    [
        'link = "upi://pay?pa=someone@bank&am=10"',
        "def create_payment_link(order): ...",
        "client.collect_request(vpa, amount)",
        "wallet_topup(amount)",
    ],
)
def test_import_test_fails_on_planted_upi_or_collect_call(tmp_path, line):
    (tmp_path / "bad.py").write_text(line + "\n")
    assert find_payment_imports(tmp_path)


@rule("PE-31")
def test_import_test_passes_on_clean_tree(tmp_path):
    (tmp_path / "ok.py").write_text("import json\nfrom pathlib import Path\n")
    assert find_payment_imports(tmp_path) == []


# ------------------------------------------------- (3) payment method outside the set
@rule("PE-31")
@pytest.mark.parametrize("pm", sorted(set(PaymentMethod) - ALLOWED_METHODS))
def test_payment_method_outside_set_denied(pm):
    d = engine().evaluate(cart(pm), CTX, NOW)
    assert d.verdict == Verdict.DENY
    assert any("payment method" in r for r in d.reasons)


@rule("PE-31")
@pytest.mark.parametrize("pm", sorted(ALLOWED_METHODS))
def test_cash_on_delivery_and_merchant_wallet_allowed(pm):
    assert engine().evaluate(cart(pm), CTX, NOW).allowed


@rule("PE-31")
def test_default_payment_set_is_cod_and_wallet_only():
    cfg = engine().cfg
    assert set(cfg.allowed_payment_methods) == ALLOWED_METHODS
    assert set(PaymentMethod) - ALLOWED_METHODS >= {PaymentMethod.UPI, PaymentMethod.CARD}


@rule("PE-31")
@pytest.mark.xfail(
    strict=True,
    reason="design-issue: PolicyConfig.allowed_payment_methods is a setting and can lift PL-36",
)
def test_setting_cannot_lift_the_payment_ban():
    widened = frozenset(PaymentMethod)
    d = engine(allowed_payment_methods=widened).evaluate(cart(PaymentMethod.UPI), CTX, NOW)
    assert d.verdict == Verdict.DENY
