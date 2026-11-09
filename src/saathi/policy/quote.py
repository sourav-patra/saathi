"""Quote hashing and HMAC-signed approval tokens.

An approval is bound to the *exact* commit-relevant content of a quote. If the
price, items, address or payment method change between the approver's tap and
the commit, the hash no longer matches and the commit is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from saathi.domain import Quote


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def canonical_quote_payload(quote: Quote, *, include_total: bool = True) -> dict:
    """The commit-relevant fields only (no ids or timestamps)."""
    payload = {
        "household_id": quote.household_id,
        "intent": quote.intent.value,
        "service": quote.service,
        "items": sorted(
            (
                {
                    "sku": i.sku,
                    "quantity": i.quantity,
                    "unit_price": _money(i.unit_price),
                    "category": i.category,
                }
                for i in quote.items
            ),
            key=lambda d: (d["sku"], d["quantity"], d["unit_price"]),
        ),
        "ride_type": quote.ride_type,
        "pickup": quote.pickup,
        "drop": quote.drop,
        "address_id": quote.address_id,
        "payment_method": quote.payment_method.value,
        "currency": quote.currency,
    }
    if include_total:
        payload["total"] = _money(quote.total)
    return payload


def _digest(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def quote_hash(quote: Quote) -> str:
    return _digest(canonical_quote_payload(quote))


def quote_hash_without_total(quote: Quote) -> str:
    """Used for rides, where the fare may drift within a tolerance."""
    return _digest(canonical_quote_payload(quote, include_total=False))


class ApprovalToken(BaseModel):
    quote_id: str
    quote_hash: str
    approver_id: str
    approved_at: datetime
    second_confirmation: bool = False
    signature: str = ""

    def _message(self) -> bytes:
        return "|".join(
            [
                self.quote_id,
                self.quote_hash,
                self.approver_id,
                self.approved_at.isoformat(),
                "2" if self.second_confirmation else "1",
            ]
        ).encode("utf-8")

    def sign(self, secret: bytes) -> ApprovalToken:
        sig = hmac.new(secret, self._message(), hashlib.sha256).hexdigest()
        return self.model_copy(update={"signature": sig})

    def verify(self, secret: bytes) -> bool:
        expected = hmac.new(secret, self._message(), hashlib.sha256).hexdigest()
        return bool(self.signature) and hmac.compare_digest(expected, self.signature)


def issue_approval(
    quote: Quote,
    *,
    approver_id: str,
    approved_at: datetime,
    secret: bytes,
    second_confirmation: bool = False,
) -> ApprovalToken:
    return ApprovalToken(
        quote_id=quote.id,
        quote_hash=quote_hash(quote),
        approver_id=approver_id,
        approved_at=approved_at,
        second_confirmation=second_confirmation,
    ).sign(secret)
