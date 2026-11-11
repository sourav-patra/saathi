"""The model gateway (M19): the only model client.

Rules in code, each with a test in tests/test_gateway.py:
- Sensitivity defaults to PERSONAL (RQ-40). Tier F (T2_FREE) takes only a `PublicPayload` built in a module of
  `PUBLIC_BUILDERS`, through `complete_public`; `complete` never reaches it. Personal data to tier F raises
  `PrivacyViolation` on every send, so fallback and retry paths are covered too (PL-02).
- Text is redacted before any call, any cache write and any trace; `assert_clean` runs before every hosted send
  and a `RestrictedDataLeft` becomes `PrivacyViolation` (RQ-41).
- The roster decides which tiers may see which media class; no receipt means no route (`ReceiptMissing`, RQ-50, 96).
- Fallback goes up a grade on schema failure, low confidence or outage and never down (RQ-47). A refusal is a result
  field and is never retried. One schema retry at the same tier (RQ-49). Nothing accepted raises an error.
- A paid tier is called only with a ledger reservation under a cap from config; an unset cap, a missing price or a
  missing ledger raises `BudgetExceeded` (RQ-48). The gateway holds no provider key: it reads the variable the roster
  names at the moment of a call.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from saathi.domain import Grade, MediaBlob, MediaRef, Message, ModelRequest, ModelResult, Sensitivity
from saathi.errors import (
    BudgetExceeded,
    NoAcceptedResult,
    PrivacyViolation,
    ReceiptMissing,
    RestrictedDataLeft,
    SchemaFailure,
)
from saathi.guards.pii import assert_clean, redact
from saathi.ids import RunId
from saathi.llm.roster import MediaClass, Roster, Tier, TierSpec
from saathi.llm.routing import (
    CONFIDENCE_FLOOR,
    SCHEMA_ATTEMPTS,
    ceiling_grade,
    classes_of,
    media_class_of,
    model_allowed,
    start_grade,
)

# Modules allowed to build a PublicPayload (public claim research, synthetic eval data, code tasks). A new public
# path adds its module here in the same change; tests/test_gateway.py greps the source tree against this set.
PUBLIC_BUILDERS: frozenset[str] = frozenset({"saathi.llm.gateway"})

MAX_TOKENS = 800
_CACHE_ITEMS = 512


class PublicPayload(BaseModel):
    """Text that holds no personal data. Tier F takes nothing else."""

    model_config = ConfigDict(frozen=True)
    text: str
    builder: str

    @model_validator(mode="after")
    def _allowed_builder(self) -> PublicPayload:
        if self.builder not in PUBLIC_BUILDERS:
            raise PrivacyViolation("a PublicPayload may only be built in a PUBLIC_BUILDERS module")
        return self

    @classmethod
    def build(cls, text: str, *, builder: str) -> PublicPayload:
        return cls(text=text, builder=builder)


class LedgerPort(Protocol):
    """What the gateway needs of the ledger (the Postgres ledger of LedgerRepo adapts to this)."""

    async def reserve(self, run: RunId, tier: str, worst_case_inr: Decimal, cap: Decimal | None) -> int: ...

    async def settle(self, reservation: int, actual_inr: Decimal) -> None: ...

    async def release(self, reservation: int) -> None: ...


MediaLoader = Callable[[MediaRef], Awaitable[MediaBlob]]
Auth = Callable[[TierSpec], str | None]


def env_auth(spec: TierSpec) -> str | None:
    """Reads the named variable at call time; the gateway never stores it."""
    return os.environ.get(spec.key_env) if spec.key_env else None


class _Outage(Exception):  # noqa: N818
    pass


class _BadOutput(Exception):  # noqa: N818
    pass


class _Rejected(Exception):  # noqa: N818  (low confidence)
    pass


class _Refused(Exception):  # noqa: N818
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _wrap(msg: Message) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    # External text is data, never instructions (CLAUDE.md): fence it and neutralise a closing tag inside it.
    body = c.text.replace("</data", "<\\/data")
    return f'<data origin="{c.origin}">\n{body}\n</data>'


def _extract_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if start != -1 and end > start else t


class Gateway:
    def __init__(
        self,
        roster: Roster,
        *,
        ledger: LedgerPort | None = None,
        cap_inr: Decimal | None = None,
        usd_to_inr: Decimal = Decimal(88),
        client: httpx.AsyncClient | None = None,
        auth: Auth = env_auth,
        media_loader: MediaLoader | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._roster = roster
        self._ledger = ledger
        self._cap = cap_inr
        self._usd_to_inr = usd_to_inr
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._auth = auth
        self._media_loader = media_loader
        self._cache: OrderedDict[str, tuple[BaseModel, Grade, int]] = OrderedDict()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- entry points
    async def complete[T: BaseModel](self, req: ModelRequest[T]) -> ModelResult[T]:
        return await self._run(req, public=False)

    async def complete_public[T: BaseModel](
        self, payload: PublicPayload, *, step: str, schema_: type[T], run: RunId, system: str | None = None
    ) -> ModelResult[T]:
        """The only path to tier F. Free tiers are tried first, then the paid-or-hosted grades upward."""
        # model_construct skips the validator, so the type and the builder are checked again here.
        if not isinstance(payload, PublicPayload) or payload.builder not in PUBLIC_BUILDERS:
            raise PrivacyViolation("tier F takes only a PublicPayload built in a PUBLIC_BUILDERS module")
        msgs = [Message(role="user", content=payload.text)]
        if system:
            msgs.insert(0, Message(role="system", content=system))
        req = ModelRequest(step=step, messages=msgs, schema_=schema_, run=run, sensitivity=Sensitivity.PUBLIC)
        return await self._run(req, public=True)

    # ---------------------------------------------------------------- core
    async def _run[T: BaseModel](self, req: ModelRequest[T], *, public: bool) -> ModelResult[T]:
        if req.step not in _known_steps():
            raise ValueError(f"unknown step {req.step!r}")
        # Only the enum member PUBLIC is public; a str, None or model_construct value is personal (PL-02).
        sens = Sensitivity.PUBLIC if req.sensitivity is Sensitivity.PUBLIC else Sensitivity.PERSONAL
        if public and sens is not Sensitivity.PUBLIC:
            raise PrivacyViolation("tier F is only reachable with a public payload")
        if not model_allowed(req.step):
            raise NoAcceptedResult(f"{req.step}: code only, no model call")
        lo, hi = start_grade(req.step), ceiling_grade(req.step)

        classes: set[MediaClass] = {MediaClass.TEXT}
        for m in req.media:
            needed = classes_of(m)
            if needed is None:
                raise ReceiptMissing("media class unknown: no route, hand off to the caregiver")
            classes |= needed
        media = frozenset(classes)

        wire = self._wire(req.messages)  # redaction before any call, cache write or trace
        candidates: list[TierSpec] = []
        if public and lo <= Grade.G2:
            candidates.extend(self._roster.free_tiers())
        for g in range(int(lo), int(hi) + 1):
            candidates.extend(self._roster.tiers(Grade(g), sens, media))
        if not candidates:
            if sens is Sensitivity.PERSONAL and self._roster.has_unreceipted(lo, hi, media):
                raise ReceiptMissing("no tier has a receipt for this data and media class")
            raise NoAcceptedResult("no tier is configured for this step")
        if req.media and self._media_loader is None:
            raise NoAcceptedResult("media given and no media loader")

        key = None if req.media else self._cache_key(req, wire, public, sens)
        if key and key in self._cache:
            self._cache.move_to_end(key)
            value, grade, tokens = self._cache[key]
            return ModelResult(value=value, grade=grade, tokens=tokens, cost_inr=Decimal(0), cached=True)

        cause = ""
        attempted = False
        for spec in candidates:
            try:
                value, tokens, cost = await self._try_tier(spec, req, wire, public, sens)
            except _Refused as r:
                return ModelResult(refusal=True, refusal_reason=r.reason, grade=_grade_of(spec))
            except BudgetExceeded:
                cause = "budget"
                continue
            except _Outage:
                cause, attempted = "outage", True
                continue
            except _BadOutput:
                cause, attempted = "schema", True
                continue
            except _Rejected:
                cause, attempted = "low_confidence", True
                continue
            if key:
                self._cache[key] = (value, _grade_of(spec), tokens)
                while len(self._cache) > _CACHE_ITEMS:
                    self._cache.popitem(last=False)
            return ModelResult(value=value, grade=_grade_of(spec), tokens=tokens, cost_inr=cost)

        if cause == "budget" and not attempted:
            raise BudgetExceeded("paid cap, price or ledger missing for every tier in range")
        if cause == "schema":
            raise SchemaFailure(req.step)
        raise NoAcceptedResult(f"{req.step}: {cause or 'nothing accepted'}")

    async def _try_tier[T: BaseModel](
        self, spec: TierSpec, req: ModelRequest[T], wire: list[dict[str, Any]], public: bool, sens: Sensitivity
    ) -> tuple[T, int, Decimal]:
        _may_send(spec, sens, public)  # also checked on every send below
        blobs = await self._load_media(req.media) if req.media else None
        reservation: int | None = None
        if spec.paid:
            reservation = await self._reserve(spec, req, wire, blobs)
        spent = Decimal(0)
        try:
            tokens_total = 0
            for _ in range(SCHEMA_ATTEMPTS):
                data = await self._post(spec, req, wire, public, blobs)
                tokens, cost = self._usage(spec, data)
                tokens_total += tokens
                spent += cost
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                if choice.get("finish_reason") == "content_filter" or message.get("refusal"):
                    raise _Refused(str(message.get("refusal") or "content_filter")[:64])
                try:
                    value = req.schema_.model_validate_json(_extract_json(message.get("content") or ""))
                except (ValidationError, ValueError):
                    continue  # one schema retry at the same tier (RQ-49)
                conf = getattr(value, "confidence", None)
                if isinstance(conf, int | float) and not isinstance(conf, bool) and conf < CONFIDENCE_FLOOR:
                    raise _Rejected()
                if reservation is not None:
                    open_reservation, reservation = reservation, None
                    # If settle fails the money is spent: the reservation stays open and the error rises (F7).
                    await self._ledger.settle(open_reservation, spent)  # type: ignore[union-attr]
                return value, tokens_total, spent
            raise _BadOutput()
        except BaseException as exc:
            if reservation is not None:
                if isinstance(exc, _Refused | _Rejected | _BadOutput):
                    open_reservation, reservation = reservation, None
                    await self._ledger.settle(open_reservation, spent)  # type: ignore[union-attr]  # call was made
                else:
                    await self._ledger.release(reservation)  # type: ignore[union-attr]
            raise

    # ---------------------------------------------------------------- http
    async def _post(
        self,
        spec: TierSpec,
        req: ModelRequest[Any],
        wire: list[dict[str, Any]],
        public: bool,
        blobs: list[MediaBlob] | None = None,
    ) -> dict[str, Any]:
        sens = Sensitivity.PUBLIC if req.sensitivity is Sensitivity.PUBLIC else Sensitivity.PERSONAL
        _may_send(spec, sens, public)
        if spec.hosted:
            try:
                for m in wire:
                    assert_clean(m["content"])
            except RestrictedDataLeft as exc:
                raise PrivacyViolation("restricted data left in text bound for a hosted tier") from exc
        messages = wire
        if req.media:
            loaded = blobs if blobs is not None else await self._load_media(req.media)
            messages = self._with_media(wire, req.media, loaded)
        body = {
            "model": spec.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if (key := self._auth(spec)):
            headers["Authorization"] = f"Bearer {key}"
        try:
            resp = await self._client.post(f"{spec.base_url.rstrip('/')}/chat/completions", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _Outage() from exc
        if not isinstance(data, dict):
            raise _Outage()
        return data

    async def _load_media(self, media: tuple[MediaRef, ...]) -> list[MediaBlob]:
        assert self._media_loader is not None
        return [await self._media_loader(ref) for ref in media]

    @staticmethod
    def _with_media(
        wire: list[dict[str, Any]], media: tuple[MediaRef, ...], blobs: list[MediaBlob]
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for ref, blob in zip(media, blobs, strict=True):
            b64 = base64.b64encode(blob.data).decode()
            kind = media_class_of(ref)
            if kind is MediaClass.AUDIO:
                parts.append({"type": "input_audio", "input_audio": {"data": b64, "format": blob.mime.split("/")[-1]}})
            elif kind is MediaClass.VIDEO:
                parts.append({"type": "video_url", "video_url": {"url": f"data:{blob.mime};base64,{b64}"}})
            else:
                parts.append({"type": "image_url", "image_url": {"url": f"data:{blob.mime};base64,{b64}"}})
        out = [dict(m) for m in wire]
        for m in reversed(out):
            if m["role"] == "user":
                m["content"] = [{"type": "text", "text": m["content"]}, *parts]
                break
        else:
            out.append({"role": "user", "content": parts})
        return out

    # ---------------------------------------------------------------- money
    async def _reserve(
        self, spec: TierSpec, req: ModelRequest[Any], wire: list[dict[str, Any]], blobs: list[MediaBlob] | None
    ) -> int:
        if self._ledger is None or self._cap is None or not spec.priced:
            raise BudgetExceeded("paid tier disabled: cap, price or ledger missing")
        # Upper bound on input tokens: one per character (scripts such as Devanagari run near that) plus base64
        # length / 3 for each media part (F3). A reservation must never be below what settles.
        tokens_in = sum(len(m["content"]) for m in wire) + 1
        for blob in blobs or ():
            tokens_in += (len(base64.b64encode(blob.data)) + 2) // 3
        usd = (
            Decimal(tokens_in) * spec.usd_per_m_input + Decimal(MAX_TOKENS) * spec.usd_per_m_output  # type: ignore[operator]
        ) / Decimal(1_000_000)
        worst = usd * self._usd_to_inr * SCHEMA_ATTEMPTS
        return await self._ledger.reserve(req.run, spec.tier.value, worst, self._cap)

    def _usage(self, spec: TierSpec, data: dict[str, Any]) -> tuple[int, Decimal]:
        u = data.get("usage") or {}
        tin, tout = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        if not spec.paid:
            return tin + tout, Decimal(0)
        reported = u.get("cost")
        if isinstance(reported, int | float) and not isinstance(reported, bool):
            usd = Decimal(str(reported))
        elif spec.priced:
            usd = (Decimal(tin) * spec.usd_per_m_input + Decimal(tout) * spec.usd_per_m_output) / Decimal(1_000_000)  # type: ignore[operator]
        else:
            usd = Decimal(0)
        return tin + tout, usd * self._usd_to_inr

    # ---------------------------------------------------------------- text
    @staticmethod
    def _wire(messages: list[Message]) -> list[dict[str, Any]]:
        return [{"role": m.role, "content": redact(_wrap(m)).text} for m in messages]

    @staticmethod
    def _cache_key(req: ModelRequest[Any], wire: list[dict[str, Any]], public: bool, sens: Sensitivity) -> str:
        raw = json.dumps(
            [req.step, sens.value, public, req.schema_.__qualname__, wire],
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()


def _known_steps() -> frozenset[str]:
    from saathi.llm.routing import ROUTING

    return frozenset(ROUTING)


def _grade_of(spec: TierSpec) -> Grade:
    return spec.grade if spec.grade is not None else Grade.G0  # tier F has no grade; G0 marks "no model grade"


def _may_send(spec: TierSpec, sensitivity: Sensitivity, public: bool) -> None:
    """The privacy gate (PL-02). Every send passes it, including retries and fallbacks."""
    if spec.tier is Tier.T2_FREE and (sensitivity is not Sensitivity.PUBLIC or not public):
        raise PrivacyViolation("tier F takes only a public payload")
