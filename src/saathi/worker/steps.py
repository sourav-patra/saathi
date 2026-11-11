"""One leased inbox row, steps 7 to 13 of flow 1.1 (M12; RQ-79, 36, 16, 97).

Phase 0 follows the flow in order and never calls a model unless `WorkerConfig.understand` is turned on (it is off, and
an emergency or scam hit never reaches it). Every transaction first checks the lease (`holds`), so a worker that lost
its lease writes nothing. Every send carries a key built from the inbox id, and the row is settled `processed` only
after its effects were sent. Elder content is external text: it is redacted before any model and appears in no summary.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from saathi import replies
from saathi.audit.events import AuditName
from saathi.consents import gate
from saathi.domain import (
    AlertOpen,
    AudioBlob,
    AuditEvent,
    Intent,
    Lease,
    MediaBlob,
    MediaKind,
    MediaRef,
    Source,
    SummaryFlag,
)
from saathi.errors import MediaExpired
from saathi.faults import WORKER_BEFORE_SETTLE, fault
from saathi.guards import pii, radar
from saathi.guards.radar import RadarResult
from saathi.householdcfg import HouseholdConfig
from saathi.ids import ApproverId, ElderId, HouseholdId, InboxId, PersonRef, RequestId, RunId, derive_id, idem_key
from saathi.ports import (
    ASR,
    AudioCodec,
    AuditSink,
    CaregiverChannel,
    Classifier,
    Clock,
    ElderChannel,
    Gateway,
    MediaStore,
    TraceExporter,
    Tx,
    VoiceStore,
)
from saathi.states import REQUEST, Machine
from saathi.store import Store
from saathi.store.keys import KeyRing
from saathi.store.repos import AlertRepo, ConfigRepo, IdentityRepo, InboxRepo, RequestRepo
from saathi.store.repos._util import aad
from saathi.trace import NullExporter
from saathi.understand.router import understand
from saathi.voice.store import ReplyKey
from saathi.worker.effects import DeliveredCaregiverChannel, DeliveredElderChannel
from saathi.worker.lease import holds
from saathi.worker.summaries import render_card, summary_slots

log = logging.getLogger(__name__)

TRANSCRIBE_USE = ("transcription", "local_models")  # the R01 guard (PL-05)
AUDIO_USE = ("alert_audio", "telegram_caregiver")  # the original audio goes to a caregiver only with this consent
_MEDIA_KINDS = frozenset({MediaKind.AUDIO, MediaKind.IMAGE, MediaKind.VIDEO, MediaKind.DOCUMENT})
_PHASE_1 = Machine(REQUEST, phase=1)  # R02 is a phase 1 row; the worker takes it now (LLD intake M12)
AUDIO_CAPTION = "Voice note from the elder"
CAREGIVER_SEND_TIMEOUT_S = 10.0  # one caregiver send never holds the elder reply longer than this


@dataclass(frozen=True)
class WorkerConfig:
    poll_s: float = 2.0  # RQ-74: the acknowledgement pass runs at this pace too
    lease_s: int = 60
    ack_limit: int = 50
    understand: bool = False  # Phase 0 calls no model (M12)
    unscanned_after_s: int = 120  # an audio row with no transcript this long opens an `unscanned` alert (A11)


@dataclass
class WorkerDeps:
    store: Store
    keys: KeyRing
    elder: ElderChannel
    caregiver: CaregiverChannel
    voice: VoiceStore
    asr: ASR
    codec: AudioCodec
    media: MediaStore
    audit: AuditSink
    clock: Clock
    owner_alert: Callable[[str], Awaitable[None]]
    classifier: Classifier | None = None
    gateway: Gateway | None = None
    cfg: WorkerConfig = field(default_factory=WorkerConfig)
    trace: TraceExporter = field(default_factory=NullExporter)

    def __post_init__(self) -> None:  # a repeated key is a delivered effect (WU-08 rulings)
        if not isinstance(self.elder, DeliveredElderChannel):
            self.elder = DeliveredElderChannel(self.elder)  # type: ignore[assignment]
        if not isinstance(self.caregiver, DeliveredCaregiverChannel):
            self.caregiver = DeliveredCaregiverChannel(self.caregiver)  # type: ignore[assignment]


@dataclass(frozen=True)
class RunProduced:
    """What a run made. All zero: the request ends `failed`, reason `no_result` (RQ-36)."""

    quotes: int = 0
    sources: int = 0
    steps: int = 0
    effects: int = 0

    @property
    def empty(self) -> bool:
        return not (self.quotes or self.sources or self.steps or self.effects)


CheckState = Literal["verified", "invalid", "unavailable"]


def check_state(ran: bool, passed: bool | None = None) -> CheckState:
    """RQ-36: a check that did not run is `unavailable`, never `verified`; one that ran and failed is `invalid`."""
    if not ran:
        return "unavailable"
    return "invalid" if passed is False else "verified"


async def end_run(tx: Tx, request: RequestId, produced: RunProduced) -> bool:
    """A run that produced nothing ends its request `failed`, reason `no_result` (RQ-36). True when it did so."""
    return produced.empty and await RequestRepo.fail_no_result(tx, request)


@dataclass(frozen=True)
class Outcome:
    inbox_state: str
    request_state: str | None
    emergency: bool = False
    scam: bool = False
    effects: int = 0
    checks: Mapping[str, CheckState] = field(default_factory=dict)


@dataclass(frozen=True)
class _Ctx:
    household: HouseholdId
    elder: ElderId
    inbox: InboxId
    source: str
    kind: MediaKind
    media: MediaRef | None
    last_inbound: datetime
    cfg: HouseholdConfig
    person: PersonRef
    consent: bool
    audio_consent: bool
    parts: Mapping[str, str]
    request: RequestId | None
    run: RunId


def run_id(inbox: InboxId) -> RunId:
    return RunId(derive_id("run", inbox))


async def last_inbound_at(tx: Tx, elder: ElderId, source: str) -> datetime:
    rows = await tx.fetch("SELECT max(received_at) AS at FROM inbox WHERE elder_id = $1 AND source = $2", elder, source)
    return rows[0]["at"]


async def _load(lease: Lease, deps: WorkerDeps, run: RunId) -> _Ctx:
    """TX-W1: the row, its decrypted parts, the household config, the consent gate and the request (R01)."""
    household = HouseholdId(lease.household_id)
    async with deps.store.tx(household=household) as tx:
        row = await holds(tx, lease)
        source, kind = str(row["source"]), MediaKind(row["kind"])
        parts: dict[str, str] = {}
        for p in await tx.fetch("SELECT part, body_sealed, key_id FROM raw_text WHERE inbox_id = $1", lease.inbox_id):
            blob = await deps.keys.open(
                tx, bytes(p["body_sealed"]), str(p["key_id"]), aad=aad(household, lease.inbox_id, str(p["part"]))
            )
            parts[str(p["part"])] = blob.decode()
        cfg = await ConfigRepo.load(tx)
        consent = await gate(tx, *TRANSCRIBE_USE)
        audio_consent = await gate(tx, *AUDIO_USE)
        request: RequestId | None = None
        if kind is not MediaKind.UNSUPPORTED:  # R01 guard: the kind is supported
            request = await RequestRepo.create_first(tx, lease.inbox_id, lease.elder_id, None)
        person = await IdentityRepo.person_ref(tx, lease.elder_id, source)
        media = None
        if row["media_ref"]:
            media = MediaRef(source=Source(source), handle=str(row["media_ref"]), mime=row["media_mime"])
        return _Ctx(
            household=household,
            elder=lease.elder_id,
            inbox=lease.inbox_id,
            source=source,
            kind=kind,
            media=media,
            last_inbound=await last_inbound_at(tx, lease.elder_id, source),
            cfg=cfg,
            person=person,
            consent=consent,
            audio_consent=audio_consent,
            parts=parts,
            request=request,
            run=run,
        )


async def _transcribe(ctx: _Ctx, deps: WorkerDeps, blob: MediaBlob) -> str | None:
    """Decode and transcribe. A failure returns None: the audio is forwarded unscanned (RQ-16)."""
    try:
        wav = await deps.codec.to_wav16k(
            AudioBlob(data=blob.data, mime=blob.mime), max_seconds=ctx.cfg.radar_audio_max_s
        )
        heard = await deps.asr.transcribe(AudioBlob(data=wav.data, mime="audio/wav"), hint=ctx.cfg.asr_hint)
    except Exception as exc:  # noqa: BLE001  (never drop a voice note because speech to text failed)
        log.warning("asr failed for %s: %s", ctx.inbox, type(exc).__name__)
        return None
    return heard.text.strip() or None


def _origin(result: RadarResult) -> str:
    return "classifier" if result.matches and all(m.startswith("clf:") for m in result.matches) else "radar_worker"


async def process(lease: Lease, deps: WorkerDeps) -> Outcome:
    run = run_id(lease.inbox_id)
    ctx = await _load(lease, deps, run)
    cfg = deps.cfg
    unsupported = ctx.kind is MediaKind.UNSUPPORTED
    if not unsupported and not ctx.consent:
        # A denial is audited outside any transaction, so it survives a retry; the audit failing fails the step.
        await deps.audit.append(
            AuditEvent(
                household=ctx.household,
                run_id=run,
                actor="system:worker",
                event=AuditName.CONSENT_DENIED,
                subject=None,
                detail={"use": TRANSCRIBE_USE[0], "recipient": TRANSCRIBE_USE[1]},
            )
        )

    if ctx.kind is MediaKind.AUDIO and ctx.consent and not ctx.audio_consent:  # LT-06: a closed alert audio gate too
        await deps.audit.append(
            AuditEvent(
                household=ctx.household,
                run_id=run,
                actor="system:worker",
                event=AuditName.CONSENT_DENIED,
                subject=None,
                detail={"use": AUDIO_USE[0], "recipient": AUDIO_USE[1]},
            )
        )

    flag: SummaryFlag = "unsupported" if unsupported else ("consent_missing" if not ctx.consent else "none")
    blob: MediaBlob | None = None
    transcript = ctx.parts.get("transcript")
    new_transcript: str | None = None
    if ctx.consent and ctx.kind in _MEDIA_KINDS and ctx.media is not None:
        try:
            blob = await deps.elder.fetch_media(ctx.media)
            await deps.media.put(ctx.media, blob, household=ctx.household)
        except MediaExpired:  # a failure inside the URL lifetime (N09)
            if transcript is None:
                flag = "media_lost"
        if ctx.kind is MediaKind.AUDIO and blob is not None and transcript is None:
            new_transcript = await _transcribe(ctx, deps, blob)
            transcript = new_transcript
            if transcript is None:
                flag = "asr_failed"

    text = "\n".join(t for t in (ctx.parts.get("text"), ctx.parts.get("caption"), transcript) if t)
    radar_ok = True
    try:
        hit = await radar.combine(text or None, deps.classifier, run=run)  # keyword OR classifier, with its timeout
    except Exception as exc:  # noqa: BLE001  (a radar crash must never lose the emergency path: a human is told)
        log.error("radar failed for %s: %s", ctx.inbox, type(exc).__name__)
        hit, radar_ok = RadarResult(), False

    intent = Intent.UNKNOWN.value
    if cfg.understand and deps.gateway is not None and ctx.consent and text and not hit.triggered:
        try:  # elder content is personal (Sensitivity.PERSONAL in the router) and redacted first
            intent = (await understand(deps.gateway, pii.redact(text).text, run=run)).intent.value
        except Exception as exc:  # noqa: BLE001  (a failed model never blocks the summary)
            log.info("router failed for %s: %s", ctx.inbox, type(exc).__name__)

    # TX-W2: the transcript (sealed), the radar mark, and an alert on a hit.
    async with deps.store.tx(household=ctx.household) as tx:
        await holds(tx, lease)
        if new_transcript is not None:
            await InboxRepo.add_transcript(tx, lease, new_transcript, deps.keys, await deps.keys.public(tx, "content"))
        if radar_ok:
            await InboxRepo.set_radar_at(tx, lease.inbox_id, tx.now)
        else:  # the row was not scanned: an `unscanned` alert, and `radar_at` stays null for the sweeps
            alert = AlertOpen(kind="unscanned", origin="unscanned_overdue")
            await AlertRepo.open(tx, lease.inbox_id, lease.elder_id, alert, tx.now, None)
        for kind, hit_flag in (("emergency", hit.emergency), ("scam", hit.scam)):
            if hit_flag:
                alert = AlertOpen(kind=kind, origin=_origin(hit), rule_ids=list(hit.rule_ids))  # type: ignore[arg-type]
                await AlertRepo.open(tx, lease.inbox_id, lease.elder_id, alert, tx.now, None)

    # TX-W3: no triage in Phase 0, so the fixed outcome is a handoff with the summary (R05); a closed gate is R02.
    request_state: str | None = None
    if ctx.request is not None:
        async with deps.store.tx(household=ctx.household) as tx:
            await holds(tx, lease)
            if flag == "consent_missing":
                await RequestRepo.transition(
                    tx, ctx.request, "received", "consent_missing", "blocked", "consent_missing", machine=_PHASE_1
                )
                request_state = "blocked"
            else:
                await RequestRepo.transition(tx, ctx.request, "received", "router_outcome_c", "handoff", "summary_only")
                request_state = "handoff"

    effects = await _send_effects(ctx, deps, run, hit, flag, intent, blob, transcript is not None)

    fault(WORKER_BEFORE_SETTLE)  # test hook: every effect is sent, the row is not settled yet
    state = {"unsupported": "unsupported", "media_lost": "media_lost"}.get(flag, "processed")
    async with deps.store.tx(household=ctx.household) as tx:  # TX-W4: only after every effect was sent
        settled = await InboxRepo.settle(tx, lease, state)
    if not settled:
        log.warning("lease lost before settle for %s; the next worker repeats the sends with the same keys", ctx.inbox)
    # audio with no transcript, or a radar that crashed, was not scanned
    checks: dict[str, CheckState] = {"radar": check_state(bool(text) and radar_ok)}
    deps.trace.span(
        "worker.process", run_id=run, inbox_id=ctx.inbox, household_id=ctx.household, state=state,
        intent=intent, rule_ids=hit.rule_ids, count=effects, ok=True,
    )  # fmt: skip
    return Outcome(state, request_state, hit.emergency, hit.scam, effects, checks)


async def _send_effects(
    ctx: _Ctx,
    deps: WorkerDeps,
    run: RunId,
    hit: RadarResult,
    flag: SummaryFlag,
    intent: str,
    blob: MediaBlob | None,
    transcribed: bool,
) -> int:
    """The caregiver summary (and the audio when allowed), then the elder reply. The humans are told first, each send
    bounded by a timeout; an error or a cancel in their send does not stop the elder reply, and an error is raised
    afterwards so the row is tried again."""
    attach = ctx.kind is MediaKind.AUDIO and blob is not None and ctx.audio_consent
    slots = summary_slots(
        ctx.kind, hit.rule_ids, intent, emergency=hit.emergency, scam=hit.scam, flag=flag,
        transcribed=transcribed, audio_attached=attach,
    )  # fmt: skip
    card = render_card(slots)
    sent = 0
    failure: Exception | None = None
    try:
        for approver in sorted(ctx.cfg.approvers, key=lambda a: a.role != "primary"):
            who = ApproverId(approver.approver_id)
            try:
                async with asyncio.timeout(CAREGIVER_SEND_TIMEOUT_S):
                    await deps.caregiver.send_card(who, card, key=idem_key(ctx.inbox, "summary", approver.approver_id))
                sent += 1
                if attach and blob is not None:
                    audio = AudioBlob(data=blob.data, mime=blob.mime)
                    async with asyncio.timeout(CAREGIVER_SEND_TIMEOUT_S):
                        await deps.caregiver.send_audio(
                            who, audio, AUDIO_CAPTION, key=idem_key(ctx.inbox, "audio", approver.approver_id)
                        )
                    sent += 1
            except Exception as exc:  # noqa: BLE001  (a timeout is one too)
                failure = failure or exc
    finally:  # also on a cancel: the elder reply is never skipped because a caregiver send was in flight
        if hit.emergency:
            reply = ReplyKey.EMERGENCY
        elif hit.scam:
            reply = ReplyKey.SCAM
        elif flag == "media_lost":
            reply = ReplyKey.NOT_UNDERSTOOD
        else:
            reply = ReplyKey.TOLD_CAREGIVER
        try:
            deps.elder.note_inbound(ctx.person, ctx.last_inbound)  # type: ignore[attr-defined]
            await replies.send_reply(
                deps.elder,
                ctx.person,
                reply,
                voice=deps.voice,
                language=ctx.cfg.reply_language,
                idem=replies.reply_idem(ctx.inbox),
                owner_alert=deps.owner_alert,
                audit=deps.audit,
                household=ctx.household,
                run=run,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            failure = failure or exc
    if failure is not None:
        raise failure
    return sent


