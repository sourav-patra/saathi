"""The inbound handler (M07; RQ-79, RQ-81, RQ-16, RQ-18).

Order: (1) size, (2) signature on the raw bytes, (3) JSON, (4) parse, (5) per message resolve the sender, (6) one
household transaction: rate state, radar, insert, alert on a hit, (7) commit, (8) 200. Nothing is parsed before the
signature holds, no 200 leaves before the commit, and no seen-set is kept: the unique key of the inbox is the dedupe.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from http import HTTPStatus

from saathi.channels import ChannelParseError
from saathi.domain import AlertOpen, InboundMessage, MediaKind, NewInbox, RawPart, Source
from saathi.edge.adapters import InboundAdapter
from saathi.edge.ratelimit import rate_state
from saathi.guards.radar import RadarResult
from saathi.ids import new_id
from saathi.ports import Clock
from saathi.store import Store
from saathi.store.keys import KeyRing, sender_hmac
from saathi.store.repos import AlertRepo, InboxRepo
from saathi.store.repos.inbox import DEFAULT_RAW_TTL_S

log = logging.getLogger(__name__)

# A webhook body is small. Anything larger is refused before it is read in full or parsed.
MAX_BODY_BYTES = 1024 * 1024

_ID_PREFIX = {Source.WHATSAPP: "wa", Source.TELEGRAM_ELDER: "te", Source.CAREGIVER_FORWARD: "cf"}


class EdgeCounters:
    """Named counts of refusals. Never exposed on a route (a count is a signal to an attacker)."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def inc(self, name: str) -> None:
        self._counts[name] += 1

    def get(self, name: str) -> int:
        return self._counts[name]

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)


@dataclass
class EdgeDeps:
    store: Store
    keys: KeyRing  # built with no key-encryption key: it can seal and nothing else
    adapters: Mapping[Source, InboundAdapter]
    radar: Callable[[str | None], RadarResult]  # the keyword scan: it never waits on a queue, ASR or a model
    clock: Clock
    sender_key: bytes
    counters: EdgeCounters = field(default_factory=EdgeCounters)
    max_body: int = MAX_BODY_BYTES


def _scan(deps: EdgeDeps, text: str | None) -> RadarResult:
    try:
        return deps.radar(text)
    except Exception:  # noqa: BLE001 - a scan that fails is a hit: the humans hear of it (RQ-16)
        log.error("radar failed at the edge; treating the message as an emergency")
        return RadarResult(emergency=True, matches=("radar_error",), rule_ids=("RD-ERR-1",))


def _parts(msg: InboundMessage) -> list[RawPart]:
    if not msg.text:
        return []
    return [RawPart(part="text" if msg.kind is MediaKind.TEXT else "caption", text=msg.text)]


async def _persist(deps: EdgeDeps, msg: InboundMessage, raw: bytes, household, elder) -> None:
    now = deps.clock.now()
    async with deps.store.tx(household=household) as tx:
        not_before = await rate_state(tx, elder, now)
        result = _scan(deps, msg.text)  # on text or caption, over the limit too (RQ-81c)
        public = await deps.keys.public(tx, "content")
        new = NewInbox(
            id=new_id(),
            household_id=household,
            elder_id=elder,
            msg=msg.model_copy(update={"provider_message_id": f"{_ID_PREFIX[msg.source]}:{msg.provider_message_id}"}),
            sender_hmac=sender_hmac(deps.sender_key, msg.source.value, msg.sender_ref),
            payload_sha256=hashlib.sha256(raw).hexdigest(),
            not_before=not_before,
            parts=_parts(msg),
        )
        expires = now + timedelta(seconds=DEFAULT_RAW_TTL_S)
        if not await InboxRepo.insert_if_new(tx, new, public_key=public, sealer=deps.keys, expires_at=expires):
            return  # a repeat of a provider id: nothing new, and still 200
        rule_ids = list(result.rule_ids)
        kinds = [k for k, hit in (("emergency", result.emergency), ("scam", result.scam)) if hit]
        for kind in kinds:
            alert = AlertOpen(kind=kind, origin="radar_edge", rule_ids=rule_ids)  # type: ignore[arg-type]
            await AlertRepo.open(tx, new.id, elder, alert, now, None)
        if msg.kind is MediaKind.AUDIO and not_before is not None:
            # Audio over the limit waits for the worker's transcript; until then it is unscanned and a human is told.
            alert = AlertOpen(kind="unscanned", origin="unscanned_edge")
            await AlertRepo.open(tx, new.id, elder, alert, now, None)


async def handle_inbound(raw: bytes, headers: Mapping[str, str], source: Source, *, deps: EdgeDeps) -> HTTPStatus:
    """Returns the status to answer. 200 only after every message of the body is committed or refused."""
    adapter = deps.adapters.get(source)
    if adapter is None:
        return HTTPStatus.NOT_FOUND
    if len(raw) > deps.max_body:
        deps.counters.inc("body_too_large")
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    lowered = {k.lower(): v for k, v in headers.items()}
    if not adapter.verify(raw, lowered):
        deps.counters.inc("bad_signature")
        return HTTPStatus.UNAUTHORIZED
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError):
        deps.counters.inc("bad_json")
        return HTTPStatus.BAD_REQUEST
    try:
        messages = adapter.parse(payload, deps.clock.now())
    except ChannelParseError:
        deps.counters.inc("bad_envelope")
        return HTTPStatus.BAD_REQUEST
    if not messages and (name := adapter.ignored(payload)):
        deps.counters.inc(name)
    try:
        for msg in messages:
            hmac_value = sender_hmac(deps.sender_key, msg.source.value, msg.sender_ref)
            who = await deps.store.scan.resolve_sender(hmac_value)
            if who is None:
                await deps.store.scan.reject_inbound(hmac_value, "unknown_sender")  # no reply, no run
                deps.counters.inc("unknown_sender")
                continue
            await _persist(deps, msg, raw, *who)
    except Exception:  # noqa: BLE001 - nothing was acknowledged: the platform retries and the unique key dedupes
        log.exception("edge insert failed")
        deps.counters.inc("insert_failed")
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.OK
