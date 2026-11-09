"""Thin, privacy-aware model gateway (see docs/DECISIONS.md, ADR-002).

Every tier is configured as an chat-completions-compatible ``/chat/completions``
endpoint (local Ollama, hosted open-weight providers, routers and most
frontier providers expose one). That lets one
small gateway own the three things Saathi needs:

1. **Privacy gate.** Personal data (the elder's voice, images, orders) may
   never reach a tier that can train on it or log it, i.e. the free tiers.
2. **Paid budget.** A hard monthly cap for T3/T4. Over the cap, calls raise
   ``BudgetExceeded``, and the pipeline falls back to "summary to the caregiver".
3. **Cascade.** Try the cheapest tier first and escalate only when the result
   isn't acceptable.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from saathi.config import Settings
from saathi.observability import observe


class Tier(StrEnum):
    T1 = "T1"            # local model on the server: zero cost, private
    T2 = "T2"            # hosted open-weight provider with a no-training policy
    T2_FREE = "T2_FREE"  # free-tier endpoints: NON-PERSONAL data only
    T3 = "T3"            # paid multimodal API (audio/video understanding)
    T4 = "T4"            # paid frontier reasoning model: hard cases only


class PrivacyViolation(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class TierUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TierSpec:
    tier: Tier
    base_url: str
    model: str
    api_key: str = ""
    paid: bool = False
    allows_personal: bool = True
    usd_per_m_input: float = 0.0
    usd_per_m_output: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass
class LLMResult:
    content: str
    tier: Tier
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_inr: float = 0.0
    cached: bool = False

    def json(self) -> Any:
        text = self.content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") :] if "{" in text else text
        start, end = text.find("{"), text.rfind("}")
        return json.loads(text[start : end + 1] if start != -1 else text)


class BudgetLedger:
    """In-memory ledger. Phase 1 swaps in a Postgres-backed implementation."""

    def __init__(self, monthly_cap_inr: float):
        self.monthly_cap_inr = monthly_cap_inr
        self._spent: dict[str, float] = {}

    @staticmethod
    def _month(now: datetime | None = None) -> str:
        return (now or datetime.now(UTC)).strftime("%Y-%m")

    def spent_this_month(self) -> float:
        return self._spent.get(self._month(), 0.0)

    def can_spend(self) -> bool:
        return self.spent_this_month() < self.monthly_cap_inr

    def record(self, inr: float) -> None:
        key = self._month()
        self._spent[key] = self._spent.get(key, 0.0) + inr


@dataclass
class _Cache:
    max_items: int = 512
    _store: OrderedDict[str, LLMResult] = field(default_factory=OrderedDict)

    def get(self, key: str) -> LLMResult | None:
        hit = self._store.get(key)
        if hit is not None:
            self._store.move_to_end(key)
        return hit

    def put(self, key: str, value: LLMResult) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self.max_items:
            self._store.popitem(last=False)


def tiers_from_settings(s: Settings) -> dict[Tier, TierSpec]:
    return {
        Tier.T1: TierSpec(Tier.T1, s.t1_base_url, s.t1_model),
        Tier.T2: TierSpec(Tier.T2, s.t2_base_url, s.t2_model, s.t2_api_key.get_secret_value()),
        Tier.T2_FREE: TierSpec(
            Tier.T2_FREE,
            s.t2_free_base_url,
            s.t2_free_model,
            s.t2_free_api_key.get_secret_value(),
            allows_personal=False,
        ),
        Tier.T3: TierSpec(
            Tier.T3, s.t3_base_url, s.t3_model, s.t3_api_key.get_secret_value(), paid=True
        ),
        Tier.T4: TierSpec(
            Tier.T4, s.t4_base_url, s.t4_model, s.t4_api_key.get_secret_value(), paid=True
        ),
    }


class Gateway:
    def __init__(
        self,
        tiers: dict[Tier, TierSpec],
        ledger: BudgetLedger,
        *,
        usd_to_inr: float = 88.0,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 60.0,
    ):
        self.tiers = tiers
        self.ledger = ledger
        self.usd_to_inr = usd_to_inr
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._cache = _Cache()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _check(self, spec: TierSpec, personal: bool) -> None:
        if not spec.enabled:
            raise TierUnavailable(f"{spec.tier} is not configured")
        if personal and not spec.allows_personal:
            raise PrivacyViolation(f"{spec.tier} may not receive personal data")
        if spec.paid and not self.ledger.can_spend():
            raise BudgetExceeded(
                f"paid monthly cap ₹{self.ledger.monthly_cap_inr:.0f} reached"
            )

    async def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tier: Tier,
        personal: bool,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 800,
    ) -> LLMResult:
        spec = self.tiers[tier]
        self._check(spec, personal)

        body: dict[str, Any] = {
            "model": spec.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        cache_key = None
        if temperature == 0.0:
            raw = json.dumps([tier.value, body], sort_keys=True, ensure_ascii=False)
            cache_key = hashlib.sha256(raw.encode()).hexdigest()
            if (hit := self._cache.get(cache_key)) is not None:
                return LLMResult(**{**hit.__dict__, "cached": True, "cost_inr": 0.0})

        headers = {"Content-Type": "application/json"}
        if spec.api_key:
            headers["Authorization"] = f"Bearer {spec.api_key}"
        with observe(
            f"llm:{tier.value}", as_type="generation", model=spec.model,
            input=list(messages), metadata={"tier": tier.value, "personal": personal},
        ) as gen:
            resp = await self._client.post(
                f"{spec.base_url.rstrip('/')}/chat/completions", json=body, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            _usage = data.get("usage") or {}
            gen.update(
                output=(data.get("choices") or [{}])[0].get("message", {}).get("content"),
                usage_details={
                    "input": int(_usage.get("prompt_tokens") or 0),
                    "output": int(_usage.get("completion_tokens") or 0),
                },
            )

        usage = data.get("usage") or {}
        tin = int(usage.get("prompt_tokens") or 0)
        tout = int(usage.get("completion_tokens") or 0)
        cost_usd = (tin * spec.usd_per_m_input + tout * spec.usd_per_m_output) / 1e6
        if isinstance(usage.get("cost"), int | float):  # some routers report actual cost
            cost_usd = float(usage["cost"])
        cost_inr = cost_usd * self.usd_to_inr
        if spec.paid:
            self.ledger.record(cost_inr)

        result = LLMResult(
            content=data["choices"][0]["message"].get("content") or "",
            tier=tier,
            model=spec.model,
            input_tokens=tin,
            output_tokens=tout,
            cost_inr=cost_inr,
        )
        if cache_key:
            self._cache.put(cache_key, result)
        return result

    async def cascade(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tiers: Sequence[Tier],
        personal: bool,
        accept: Callable[[LLMResult], bool],
        json_mode: bool = False,
    ) -> LLMResult:
        """Try tiers in order and return the first acceptable result.

        Unavailable tiers, privacy-blocked tiers, exhausted budgets and
        transport errors are skipped. If no result is accepted, the last one
        obtained is returned so that the caller can fall back to a human.
        """
        last: LLMResult | None = None
        errors: list[str] = []
        for t in tiers:
            try:
                res = await self.chat(messages, tier=t, personal=personal, json_mode=json_mode)
            except (TierUnavailable, PrivacyViolation, BudgetExceeded, httpx.HTTPError) as e:
                errors.append(f"{t}: {e}")
                continue
            last = res
            try:
                if accept(res):
                    return res
            except Exception as e:  # malformed output counts as "not accepted"
                errors.append(f"{t}: rejected ({e})")
        if last is None:
            raise TierUnavailable("no tier produced a result: " + "; ".join(errors))
        return last
