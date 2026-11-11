"""The alert service (M14; RQ-18, 95a; A01 to A13): escalation state in Postgres, resumed by `next_due_at`.

`step` does three things, each its own transaction: (1) lock the event and write the attempt rows the plan makes due,
(2) send each due attempt outside any transaction, (3) mark the attempts and set the next due time. A crash between
(2) and (3) repeats a send with the same key. The service reads no model, no speech to text and no worker state; stage a
needs only the caregiver channel, and stage c only its escalator.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Literal

from saathi import replies
from saathi.alerts.plan import (
    FALSE_ALARM_WINDOW,
    FOLLOWUP_S,
    SIGNOFF_NOTICE_DAYS,
    STAGE_B_AT_S,
    AlertConfig,
    clip_due,
    counts_toward_budget,
    false_alarm_count,
    plan,
    review_opens,
)
from saathi.audit.events import AuditName
from saathi.consents import gate
from saathi.delivered import DeliveredCaregiverChannel, DeliveredElderChannel
from saathi.domain import (
    AlertEventRef,
    AlertView,
    AttemptResult,
    AudioBlob,
    AuditEvent,
    ChannelHealth,
    MediaBlob,
    MediaRef,
    Source,
    VoiceCallback,
)
from saathi.errors import AuditUnavailable, CallbackRejected
from saathi.faults import ALERT_BETWEEN_SEND_AND_MARK, fault
from saathi.householdcfg import ConfigMissing
from saathi.ids import ApproverId, EventId, HouseholdId, IdemKey, PersonRef, RunId, derive_id, idem_key
from saathi.ports import AuditSink, CaregiverChannel, Clock, ElderChannel, Escalator, VoiceStore
from saathi.store import Store
from saathi.store.repos import AlertRepo, ConfigRepo, IdentityRepo, SignoffRepo
from saathi.voice.store import ReplyKey

log = logging.getLogger(__name__)

TICK_S = 2.0  # how often the service looks for due events
RETRY_S = 30  # a send that could not be tried (audit down, clip channel down) is tried again after this
AUDIO_USE = ("alert_audio", "telegram_caregiver")  # the original audio reaches a caregiver only with this consent
AUDIO_CAPTION = "Voice note from the elder"
CHANNEL = {"a": "telegram", "b": "whatsapp_template", "c": "voice_call"}
ACTOR = "system:alerts"
_RESULT_STATUS = {
    AttemptResult.DELIVERED: "delivered",
    AttemptResult.FAILED: "failed",
    AttemptResult.ANSWERED_NO_ACK: "answered_no_ack",
    AttemptResult.VOICEMAIL: "voicemail",
}

FOLLOWUP_TEXT = (
    "You acknowledged an alert for the elder 15 minutes ago. Is it resolved? "
    "If you are not sure the elder is safe, please call them now."
)
FOLLOWUP_GIVE_UP_S = 3600  # a follow-up that cannot be sent is tried until this long after it was due, then left

OwnerAlert = Callable[[str], Awaitable[None]]


def attempt_key(event: str, stage: str, approver: str, n: int) -> IdemKey:
    """`alert:<event>:<stage>:<approver>:<n>` (data part, 003)."""
    return idem_key("alert", event, stage, approver, str(n))


def notice_key(event: str, what: str) -> IdemKey:
    """The durable marker that the owner (or the audit) has been told `what` for this event: a cancelled attempt row
    with channel `notice`, never sent and never planned."""
    return idem_key("alert", event, "notice", what)


def _n_of(key: str) -> int:
    return int(key.rsplit(":", 1)[1])


class AlertService:
    def __init__(
        self,
        store: Store,
        caregivers: CaregiverChannel,
        elders: Mapping[Source, ElderChannel],
        voice: VoiceStore,
        stages: Mapping[Literal["a", "b", "c"], Escalator],
        audit: AuditSink,
        signoffs: type[SignoffRepo],
        clock: Clock,
        cfg: AlertConfig | None = None,
        *,
        owner_alert: OwnerAlert,
    ) -> None:
        self._store = store
        self._caregivers = DeliveredCaregiverChannel(caregivers)
        self._elders = {s: DeliveredElderChannel(ch) for s, ch in elders.items()}
        self._voice = voice
        self._stages = dict(stages)
        self._audit = audit
        self._signoffs = signoffs
        self._clock = clock
        self._cfg = cfg  # None: each household's config row decides
        self._owner = owner_alert
        self._noticed: set[tuple[str, int, str]] = set()
        self._no_config_told: set[str] = set()

    # ------------------------------------------------------------------ loop
    async def run_forever(self) -> None:
        """Tick every two seconds until cancelled. A failing tick is logged and the next one runs."""
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001  (a store outage is waited out; the rows are still due)
                log.warning("alert tick failed: %s", type(exc).__name__)
            await self._clock.sleep_until(self._clock.now() + timedelta(seconds=TICK_S))

    async def tick(self) -> int:
        """Step every due event once. One event failing does not stop the others. Returns how many were stepped."""
        stepped = 0
        for household, event in await self._store.scan.due_alerts(self._clock.now()):
            try:
                await self.step(HouseholdId(household), EventId(event))
                stepped += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("alert step failed for %s: %s", event, type(exc).__name__)
        return stepped

    # ------------------------------------------------------------------ step
    async def _whatsapp_down(self) -> bool:
        ch = self._elders.get(Source.WHATSAPP)
        if ch is None:
            return True
        try:
            return await ch.health() is ChannelHealth.DOWN
        except Exception:  # noqa: BLE001  (a probe that cannot run is not "up")
            return True

    async def _tell_owner(self, what: str) -> bool:
        """Tell the owner. A failing alert is logged at error level, never raised, and reported as False."""
        try:
            await self._owner(what)
        except Exception as exc:  # noqa: BLE001  (the alert path goes on; the caller decides about a retry)
            log.error("owner alert %s failed: %s", what, type(exc).__name__)
            return False
        return True

    async def _tell_owner_once(
        self,
        household: HouseholdId,
        event: EventId,
        stage: Literal["a", "b", "c"],
        who: ApproverId,
        what: str,
        marker: str,
    ) -> bool:
        """Tell the owner `what` once per event: a marker row says it was done. The marker is written after the alert
        went out, so an alert that failed is tried again, and a crash in between repeats it at most once."""
        key = str(notice_key(event, marker))
        async with self._store.tx(household=household) as tx:
            if await AlertRepo.attempt_status(tx, key) is not None:
                return True
        if not await self._tell_owner(what):
            return False
        async with self._store.tx(household=household) as tx:
            await AlertRepo.add_attempt(tx, event, stage, who, "notice", key, tx.now, status="cancelled")
        return True

    async def _send_failed(self, household: HouseholdId, event: EventId, stage: str, who: ApproverId) -> None:
        """The first failed send of a stage tells the owner (F1): b and c are unavailable until Phase 1 and Telegram
        can be down, and the owner must not learn of it only when every cap is spent."""
        row_stage: Literal["a", "b", "c"] = stage if stage in ("b", "c") else "a"  # type: ignore[assignment]
        await self._tell_owner_once(
            household, event, row_stage, who, f"alert_send_failed:{stage}", f"send_failed_{stage}"
        )

    async def step(self, household: HouseholdId, event: EventId) -> None:
        try:
            await self._step(household, event)
        except ConfigMissing:  # nobody can be reached without the approvers; the owner hears once per process
            if str(household) not in self._no_config_told and await self._tell_owner("alert_no_config"):
                self._no_config_told.add(str(household))
            raise

    async def _step(self, household: HouseholdId, event: EventId) -> None:
        run = RunId(derive_id("run", event))
        async with self._store.tx(household=household) as tx:
            seen = await AlertRepo.view(tx, event)
            now0 = tx.now
        if seen is not None and seen.state == "acknowledged":  # A06, A12: the follow-up at 15 minutes
            if seen.followup_due_at is not None and seen.followup_due_at <= now0:
                await self._followup(household, seen)
            return
        wa_down = await self._whatsapp_down()

        # (1) lock the event, write what the plan makes due, advance the stage
        async with self._store.tx(household=household) as tx:
            ev = await AlertRepo.lock(tx, event)
            if ev is None or ev.state != "open":
                return
            try:
                hh = await ConfigRepo.load(tx)
            except ConfigMissing:
                log.error("alert %s: no household config, so no approver to tell", event)
                raise
            acfg = self._cfg or AlertConfig.from_household(hh)
            approvers = [ApproverId(a.approver_id) for a in sorted(hh.approvers, key=lambda a: a.role != "primary")]
            now = tx.now
            so1 = await self._signoffs.valid(tx, "SO-1", now)
            due, next_due = plan(ev, approvers, now, so1_valid=so1.valid, whatsapp_down=wa_down, cfg=acfg)
            for p in due:  # one row per key; a repeat of a tick inserts nothing
                await AlertRepo.add_attempt(
                    tx, ev.id, p.stage, p.approver, CHANNEL[p.stage],
                    attempt_key(ev.id, p.stage, p.approver, p.n), p.due_at,
                )  # fmt: skip
            for stage in ("c", "b"):
                if any(p.stage == stage for p in due):
                    await AlertRepo.set_stage(tx, ev.id, stage)  # type: ignore[arg-type]
                    break
            owner_told = await AlertRepo.attempt_status(tx, str(notice_key(ev.id, "signoff_expired"))) is not None
            audit_done = await AlertRepo.attempt_status(tx, str(notice_key(ev.id, "signoff_expired_audit"))) is not None
            so1_notice = (
                bool(approvers)
                and ev.kind == "emergency"
                and not so1.valid
                and now >= ev.t0 + timedelta(seconds=STAGE_B_AT_S)
                and not (owner_told and audit_done)
            )
            clip = clip_due(ev, now)
            src = await AlertRepo.inbox_source(tx, ev.inbox_id)
            person: PersonRef | None = None
            if clip and src is not None:
                try:
                    person = await IdentityRepo.person_ref(tx, ev.elder_id, str(src["source"]))
                except LookupError:
                    log.error("alert %s: the elder has no active identity for the clip", event)
            audio_ref = ev.audio_ref or (str(src["media_ref"]) if src and src["kind"] == "audio" else None)
            audio_ok = bool(audio_ref) and await gate(tx, *AUDIO_USE)
            audio_denied = bool(audio_ref) and not audio_ok
            attempts = await AlertRepo.due_attempts(tx, ev.id)
            told = await AlertRepo.reached(tx, ev.id, "a")  # approvers who already have their card (and the audio)
        approvers_ref = approvers
        language = hh.reply_language

        # a sign-off that is missing or expired: stages b and c send nothing and the owner hears of it once (A05)
        if so1_notice:
            await self._so1_notice(household, ev, run, approvers_ref[0], owner_told=owner_told, audit_done=audit_done)

        # (2) send, outside any transaction: the humans first
        marks: list[tuple[str, str]] = []
        first_a = any(str(r["stage"]) == "a" and ApproverId(r["approver_id"]) not in told for r in attempts)
        blob = await self._fetch_audio(src, audio_ref) if audio_ok and first_a else None  # the first card has it
        ref = AlertEventRef(household_id=household, event_id=ev.id, kind=ev.kind)
        newest: dict[tuple[str, str], int] = {}
        for row in attempts:  # a restart or an outage finds old slots: only the newest of each stage and approver goes
            group = (str(row["stage"]), str(row["approver_id"]))
            newest[group] = max(newest.get(group, 0), _n_of(str(row["idem_key"])))
        for row in attempts:
            key, stage, who = str(row["idem_key"]), str(row["stage"]), ApproverId(row["approver_id"])
            if _n_of(key) < newest[(stage, str(who))]:
                marks.append((key, "cancelled"))
                continue
            if stage != "a" and not so1.valid:
                marks.append((key, "cancelled"))  # never b or c without a valid SO-1, even for a row planned earlier
                continue
            if not await self._still_due(household, key):
                continue  # acknowledged, or cancelled, since the plan
            if stage != "a":
                try:
                    await self._audit.append(
                        AuditEvent(
                            household=household, run_id=run, actor=ACTOR, event=AuditName.SIGNOFF_USED,
                            subject=str(so1.id), detail={"signoff_id": so1.id or 0, "stage": stage, "n": _n_of(key)},
                        ),
                        tx=None,
                    )  # fmt: skip
                except AuditUnavailable:
                    log.warning("alert %s: sign-off use not audited, stage %s attempt waits", event, stage)
                    continue  # fail closed: no use of SO-1 without its audit row; the attempt stays due
            status = await self._send_attempt(household, ev, run, stage, who, key, ref)
            marks.append((key, status))
            if status == "failed":
                await self._send_failed(household, ev.id, stage, who)
            if stage == "a" and who not in told:  # the approver's first stage a send carries the original audio
                if status == "delivered" and blob is not None:
                    await self._send_audio(ev, who, blob)
                    told = told | {who}
                if audio_denied:
                    await self._audit_denied(household, run)
        clip_sent, clip_retry = False, False
        if clip and person is not None and src is not None:
            clip_sent = await self._send_clip(household, ev, run, src, person, language, clip)
            # a channel that is down is tried again for a while, never so long that the event cannot end
            clip_retry = not clip_sent and self._clock.now() < ev.t0 + timedelta(seconds=FOLLOWUP_S)

        fault(ALERT_BETWEEN_SEND_AND_MARK)  # test hook: every send is out, nothing is marked yet

        # (3) mark, then either set the next due time or end the event
        block = False
        async with self._store.tx(household=household) as tx:
            for key, status in marks:
                await AlertRepo.mark_attempt(tx, key, status, sent_at=tx.now if status != "cancelled" else None)
            if clip_sent:
                await AlertRepo.set_last_clip(tx, ev.id, tx.now)
            now = tx.now
            waiting = bool(await AlertRepo.due_attempts(tx, ev.id)) or clip_retry
            if next_due is None and not waiting:
                block = True  # every cap is spent with no acknowledgement (A07)
            else:
                again = now + timedelta(seconds=RETRY_S)
                when = (again if next_due is None else min(next_due, again)) if waiting else next_due
                await AlertRepo.set_next_due(tx, ev.id, when)
        if block:
            await self._block(household, ev, run)

    async def _followup(self, household: HouseholdId, ev: AlertView) -> None:
        """One text to the approver who acknowledged, 15 minutes after (A06, A12; T-33). The key is fixed, so a repeat
        after a crash is refused by the channel. A failure is tried again every RETRY_S for an hour."""
        who = ev.acked_by
        sent = False
        if who is not None:
            try:
                await self._caregivers.send_text(who, FOLLOWUP_TEXT, key=idem_key("alert", ev.id, "followup"))
                sent = True
            except Exception as exc:  # noqa: BLE001  (a down channel: the owner hears once, the send is tried again)
                log.warning("alert %s follow-up failed: %s", ev.id, type(exc).__name__)
                await self._send_failed(household, ev.id, "followup", who)
        async with self._store.tx(household=household) as tx:
            gave_up = ev.followup_due_at is not None and tx.now >= ev.followup_due_at + timedelta(
                seconds=FOLLOWUP_GIVE_UP_S
            )
            if sent or who is None or gave_up:
                await AlertRepo.set_followup_due(tx, ev.id, None)
            else:
                await AlertRepo.set_followup_due(tx, ev.id, tx.now + timedelta(seconds=RETRY_S))

    async def _still_due(self, household: HouseholdId, key: str) -> bool:
        async with self._store.tx(household=household) as tx:
            return await AlertRepo.attempt_status(tx, key) == "due"

    async def _send_attempt(
        self,
        household: HouseholdId,
        ev: AlertView,
        run: RunId,
        stage: str,
        who: ApproverId,
        key: str,
        ref: AlertEventRef,
    ) -> str:
        """One send through the stage's escalator. Any error marks the attempt `failed` and the cadence goes on."""
        esc = self._stages.get(stage)  # type: ignore[call-overload]
        if esc is None:
            return "failed"
        try:
            result = await esc.notify(ref, who, key=IdemKey(key))
        except Exception as exc:  # noqa: BLE001  (a down channel, a refusal or a bug: the next slot tries again)
            log.warning("alert %s stage %s send failed: %s", ev.id, stage, type(exc).__name__)
            result = AttemptResult.FAILED
        if stage == "c" and result is not AttemptResult.DELIVERED:
            await self._audit_best_effort(
                AuditEvent(
                    household=household, run_id=run, actor=ACTOR, event=AuditName.CALL_NO_ACK,
                    subject=str(who), detail={"result": result.value, "n": _n_of(key)},
                )
            )  # fmt: skip
        return _RESULT_STATUS[result]

    async def _audit_best_effort(self, ev: AuditEvent) -> None:
        """A record that must not stop an alert: its failure is logged and the alert goes on (tx=None)."""
        try:
            await self._audit.append(ev, tx=None)
        except AuditUnavailable:
            log.warning("audit row %s not written", ev.event)

    async def _audit_denied(self, household: HouseholdId, run: RunId) -> None:
        await self._audit_best_effort(
            AuditEvent(
                household=household, run_id=run, actor=ACTOR, event=AuditName.CONSENT_DENIED,
                detail={"use": AUDIO_USE[0], "recipient": AUDIO_USE[1]},
            )
        )  # fmt: skip

    async def _fetch_audio(self, src: Mapping[str, object] | None, audio_ref: str | None) -> MediaBlob | None:
        """The original audio, once, when consent allows it. A lost or unreachable media returns None: the card goes."""
        if src is None or not audio_ref:
            return None
        try:
            source = Source(str(src["source"]))
            ch = self._elders.get(source)
            if ch is None:
                return None
            mime = str(src["media_mime"]) if src["media_mime"] else None
            return await ch.fetch_media(MediaRef(source=source, handle=audio_ref, mime=mime))
        except Exception as exc:  # noqa: BLE001  (media expired or the channel down: the card goes without it)
            log.info("alert audio not fetched: %s", type(exc).__name__)
            return None

    async def _send_audio(self, ev: AlertView, who: ApproverId, blob: MediaBlob) -> None:
        try:
            await self._caregivers.send_audio(
                who, AudioBlob(data=blob.data, mime=blob.mime), AUDIO_CAPTION, key=idem_key(ev.id, "audio", who)
            )
        except Exception as exc:  # noqa: BLE001  (the card already went; a failed audio is not an alert failure)
            log.info("alert audio send failed: %s", type(exc).__name__)

    async def _send_clip(
        self,
        household: HouseholdId,
        ev: AlertView,
        run: RunId,
        src: Mapping[str, object],
        person: PersonRef,
        language: str,
        which: Literal["first", "repeat"],
    ) -> bool:
        """The stored 108 and 112 clip (emergency) or the scam clip. The first one uses the worker's key, so the elder
        hears it once; the repeat at t0 + 10 min has its own."""
        ch = self._elders.get(Source(str(src["source"])))
        if ch is None:
            return False
        idem = replies.reply_idem(ev.inbox_id) if which == "first" else idem_key(ev.inbox_id, "reply", "repeat")
        key = ReplyKey.EMERGENCY if ev.kind == "emergency" else ReplyKey.SCAM
        try:
            await replies.send_reply(
                ch, person, key, voice=self._voice, language=language, idem=idem, owner_alert=self._owner,
                audit=self._audit, household=household, run=run,
            )  # fmt: skip
        except Exception as exc:  # noqa: BLE001  (a channel down: tried again after RETRY_S, the humans are told anyway)
            log.warning("alert %s elder clip failed: %s", ev.id, type(exc).__name__)
            return False
        return True

    async def _so1_notice(
        self,
        household: HouseholdId,
        ev: AlertView,
        run: RunId,
        primary: ApproverId,
        *,
        owner_told: bool,
        audit_done: bool,
    ) -> None:
        """A05, once per event each: tell the owner first (best effort, so a down audit cannot keep the owner in the
        dark), then audit `signoff_expired`. Each has its own marker; the one that did not happen is tried again."""
        if not owner_told:
            await self._tell_owner_once(household, ev.id, "b", primary, "signoff_expired", "signoff_expired")
        if audit_done:
            return
        try:
            await self._audit.append(
                AuditEvent(
                    household=household, run_id=run, actor=ACTOR, event=AuditName.SIGNOFF_EXPIRED,
                    detail={"scope": "SO-1", "event": ev.id},
                ),
                tx=None,
            )  # fmt: skip
        except AuditUnavailable:
            return  # not recorded: the next tick tries again, stage a runs on meanwhile
        async with self._store.tx(household=household) as tx:
            await AlertRepo.add_attempt(
                tx,
                ev.id,
                "b",
                primary,
                "notice",
                str(notice_key(ev.id, "signoff_expired_audit")),
                tx.now,
                status="cancelled",
            )

    async def _block(self, household: HouseholdId, ev: AlertView, run: RunId) -> None:
        """A07: every cap spent, no acknowledgement. Audit first (tx=None); the event ends `blocked` after the row."""
        try:
            await self._audit.append(
                AuditEvent(
                    household=household, run_id=run, actor=ACTOR, event=AuditName.ALERT_BLOCKED,
                    detail={"reason": "no_human_reached", "event": ev.id},
                ),
                tx=None,
            )  # fmt: skip
        except AuditUnavailable:
            async with self._store.tx(household=household) as tx:
                await AlertRepo.set_next_due(tx, ev.id, tx.now + timedelta(seconds=RETRY_S))
            return
        async with self._store.tx(household=household) as tx:
            done = await AlertRepo.block(tx, ev.id, "no_human_reached")
        if done:
            for _ in range(3):  # the ops chat record; the event has ended, so nothing else would try again
                if await self._tell_owner("no_human_reached"):
                    break

    # ------------------------------------------------------------------ acknowledgement
    async def _registered(self, household: HouseholdId, approver: ApproverId) -> bool:
        async with self._store.tx(household=household) as tx:
            hh = await ConfigRepo.load(tx)
        return any(a.approver_id == approver for a in hh.approvers)

    async def _deny(self, household: HouseholdId, event: EventId, who: str, reason: str) -> None:
        await self._audit_best_effort(
            AuditEvent(
                household=household, run_id=RunId(derive_id("run", event)), actor=ACTOR, event=AuditName.DENY,
                subject=who, detail={"reason": reason, "use": "alert_ack"},
            )
        )  # fmt: skip

    async def acknowledge(
        self, household: HouseholdId, event: EventId, approver: ApproverId, via: Literal["tap", "dtmf"]
    ) -> bool:
        """One compare-and-set on `state = 'open'` (A06, A12). Only a registered approver stops an alert. The audit row
        names the approver and is written in the same transaction. False when the event was not open or not ready."""
        try:
            registered = await self._registered(household, approver)
        except ConfigMissing:
            registered = False
        if not registered:
            await self._deny(household, event, str(approver), "not_registered")
            return False
        name = AuditName.ACK_TAP if via == "tap" else AuditName.ACK_VOICE
        run = RunId(derive_id("run", event))
        async with self._store.tx(household=household) as tx:
            won = await AlertRepo.ack(tx, event, approver, via)
            if won:
                await self._audit.append(
                    AuditEvent(
                        household=household, run_id=run, actor=f"approver:{approver}", event=name,
                        subject=str(event), detail={"via": via},
                    ),
                    tx=tx,
                )  # fmt: skip
        if not won:
            async with self._store.tx(household=household) as tx:
                ev = await AlertRepo.view(tx, event)
            if ev is not None and ev.state != "open":  # a later acknowledgement is logged (RQ-18); nothing changes
                await self._audit_best_effort(
                    AuditEvent(
                        household=household, run_id=run, actor=f"approver:{approver}", event=name,
                        subject=str(event), detail={"via": via, "late": True},
                    )
                )  # fmt: skip
            else:  # an open event the tap could not stop (a key press before stage c, or an unknown event)
                await self._deny(household, event, str(approver), "not_ready")
        return won

    async def on_voice_callback(self, household: HouseholdId, event: EventId, cb: VoiceCallback) -> bool:
        """A provider callback from a stage c call. The escalator verifies it (signature, call id, single use, digit 1)
        and names the approver of the attempt that was called; only then is the event acknowledged."""
        esc = self._stages.get("c")
        if esc is None:
            return False
        try:
            result = await esc.on_dtmf(cb)
        except CallbackRejected:
            await self._deny(household, event, "callback", "callback_rejected")
            return False
        return await self.acknowledge(household, event, result.approver_id, "dtmf")

    async def false_alarm(self, household: HouseholdId, event: EventId, approver: ApproverId) -> bool:
        """A08: flag an acknowledged event. It counts toward the approver's budget when stage b or c had started; the
        crossing of the budget opens one review (PE-30). The state, the keywords and the stages do not change."""
        try:
            registered = await self._registered(household, approver)
        except ConfigMissing:
            registered = False
        if not registered:
            await self._deny(household, event, str(approver), "not_registered")
            return False
        run = RunId(derive_id("run", event))
        review = False
        async with self._store.tx(household=household) as tx:
            if not await AlertRepo.false_alarm(tx, event):
                return False
            ev = await AlertRepo.view(tx, event)
            acfg = self._cfg or AlertConfig.from_household(await ConfigRepo.load(tx))
            counted = ev is not None and counts_toward_budget(ev)
            count = 0
            if ev is not None and ev.acked_by is not None and counted:
                recent = await AlertRepo.recent_false_alarms(tx, ev.acked_by, tx.now - FALSE_ALARM_WINDOW)
                count = false_alarm_count(recent, ev.acked_by, tx.now)
                review = review_opens(count, acfg)
            await self._audit.append(
                AuditEvent(
                    household=household, run_id=run, actor=f"approver:{approver}", event=AuditName.FALSE_ALARM,
                    subject=str(event), detail={"counted": counted, "count": count, "review": review},
                ),
                tx=tx,
            )  # fmt: skip
        if review:
            await self._tell_owner("false_alarm_review")  # a threshold review of the radar classifier is opened
        return True

    # ------------------------------------------------------------------ sign-off watch (RQ-95a)
    async def signoff_check(self, household: HouseholdId) -> Literal["ok", "expiring", "expired"]:
        """Daily check of SO-1: the owner hears 14 days ahead, and at once when it is missing, expired or revoked. A
        notice is sent once per sign-off in this process."""
        async with self._store.tx(household=household) as tx:
            st = await self._signoffs.valid(tx, "SO-1", tx.now)
            now = tx.now
        sid = st.id or 0
        if not st.valid:
            state: Literal["ok", "expiring", "expired"] = "expired"
        elif st.expires_at is not None and st.expires_at - now <= timedelta(days=SIGNOFF_NOTICE_DAYS):
            state = "expiring"
        else:
            return "ok"
        mark = (str(household), sid, state)
        if mark not in self._noticed:
            if await self._tell_owner(f"signoff_{state}"):
                self._noticed.add(mark)  # a notice that failed is tried again at the next check
        return state


__all__ = ["AlertService", "attempt_key"]
