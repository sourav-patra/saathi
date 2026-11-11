"""The request worker (M12): the poll loop, the acknowledgement pass and the sweeps (RQ-79, 74, 16).

`run_forever` polls every `cfg.poll_s` (2 s). A separate task runs `ack_pass` at the same pace, so an acknowledgement
never waits for a slow step. A step that fails leaves its lease to expire, and the row is tried again (at most 5
attempts); a row that fails the fifth time is `dead`, audited, and the owner is told. Logs hold ids and error class
names only, never elder content.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path

from saathi import replies
from saathi.audit.events import AuditName
from saathi.config import ConfigError, Settings, require_secrets
from saathi.domain import AlertOpen, AuditEvent
from saathi.health import ProbeResult, ProbeStatus, beat_loop, disk_probe, skew_probe
from saathi.hosts import require_listed_hosts
from saathi.householdcfg import ConfigMissing
from saathi.ids import ElderId, HouseholdId, InboxId, idem_key
from saathi.ports import Clock, Store
from saathi.store.repos import AlertRepo, ConfigRepo, IdentityRepo, InboxRepo
from saathi.voice.store import ReplyKey
from saathi.worker.lease import LeaseLost, LeaseRenewer, lease_next
from saathi.worker.scope import acting_for
from saathi.worker.steps import WorkerConfig, WorkerDeps, last_inbound_at, process, run_id
from saathi.worker.wiring import PendingCaregiverChannel

log = logging.getLogger(__name__)


async def ack_pass(store: Store, deps: WorkerDeps, clock: Clock) -> int:
    """Send the stored acknowledgement to rows with `acked_at` null, then mark them (RQ-74). Outside the per-elder
    order: it reads `unacked_inbox`, not the lease queue. Returns the number acknowledged."""
    acked = 0
    for household, inbox in await store.scan.unacked_inbox(clock.now(), deps.cfg.ack_limit):
        try:
            if await _ack_one(store, deps, HouseholdId(household), InboxId(inbox)):
                acked += 1
        except Exception as exc:  # noqa: BLE001  (one bad row never stops the pass)
            log.warning("ack failed for %s: %s", inbox, type(exc).__name__)
    return acked


async def _ack_one(store: Store, deps: WorkerDeps, household: HouseholdId, inbox: InboxId) -> bool:
    async with store.tx(household=household) as tx:
        rows = await tx.fetch("SELECT elder_id, source FROM inbox WHERE id = $1 AND acked_at IS NULL", inbox)
        if not rows:
            return False
        elder, source = ElderId(rows[0]["elder_id"]), str(rows[0]["source"])
        person = await IdentityRepo.person_ref(tx, elder, source)
        try:
            language = (await ConfigRepo.load(tx)).reply_language
        except ConfigMissing:
            language = replies.DEFAULT_LANGUAGE
        last = await last_inbound_at(tx, elder, source)
    deps.elder.note_inbound(person, last)  # type: ignore[attr-defined]
    with acting_for(household):
        await replies.send_reply(
            deps.elder,
            person,
            ReplyKey.ACK,
            voice=deps.voice,
            language=language,
            idem=idem_key(inbox, "ack"),
            owner_alert=deps.owner_alert,
            audit=deps.audit,
            household=household,
            run=run_id(inbox),
        )
    async with store.tx(household=household) as tx:
        await InboxRepo.mark_acked(tx, inbox, tx.now)
    return True


async def reap_dead(store: Store, deps: WorkerDeps, clock: Clock) -> int:
    """Rows whose fifth lease ran out are `dead`. Each is audited and the owner told; a row the radar never ran on
    (no `radar_at`), or a voice note with no transcript, also opens an `unscanned` alert so a human hears of it."""
    dead = await store.scan.reap_inbox(clock.now())
    for household, inbox in dead:
        run = run_id(InboxId(inbox))
        await deps.audit.append(
            AuditEvent(household=household, run_id=run, actor="system:worker", event=AuditName.INBOX_DEAD)
        )
        await _open_unscanned(store, HouseholdId(household), InboxId(inbox), dead=True)
        with contextlib.suppress(Exception):  # the owner alert is best effort; the audit row is the record
            await deps.owner_alert("inbox_dead")
    return len(dead)


async def unscanned_sweep(store: Store, clock: Clock, *, max_s: int = 120) -> int:
    """Audio with no transcript `max_s` after it arrived opens an `unscanned` alert (A11; RQ-16). Repeats are no-ops."""
    opened = 0
    for household, inbox in await store.scan.unscanned_audio(clock.now(), max_s):
        opened += await _open_unscanned(store, HouseholdId(household), InboxId(inbox), dead=False)
    return opened


async def _open_unscanned(store: Store, household: HouseholdId, inbox: InboxId, *, dead: bool) -> int:
    async with store.tx(household=household) as tx:
        rows = await tx.fetch(
            "SELECT i.elder_id, i.kind, i.radar_at, EXISTS (SELECT 1 FROM raw_text t WHERE t.inbox_id = i.id"
            " AND t.part = 'transcript') AS heard FROM inbox i WHERE i.id = $1",
            inbox,
        )
        if not rows:
            return 0
        if dead:  # a dead row nobody scanned (no radar run), or a voice note nobody heard, must reach a human
            unheard_audio = rows[0]["kind"] == "audio" and not rows[0]["heard"]
            if rows[0]["radar_at"] is not None and not unheard_audio:
                return 0
        alert = AlertOpen(kind="unscanned", origin="unscanned_overdue")
        return int(await AlertRepo.open(tx, inbox, ElderId(rows[0]["elder_id"]), alert, tx.now, None))


class Worker:
    def __init__(self, store: Store, deps: WorkerDeps, clock: Clock, cfg: WorkerConfig | None = None) -> None:
        self._store = store
        self._deps = deps
        self._clock = clock
        self._cfg = cfg or deps.cfg

    async def run_once(self, *, ack: bool = True) -> bool:
        """One round: acknowledgements, dead rows, the unscanned sweep, then at most one leased row. True when a row
        was processed (also when its step failed and will be tried again)."""
        if ack:
            await ack_pass(self._store, self._deps, self._clock)
        await reap_dead(self._store, self._deps, self._clock)
        await unscanned_sweep(self._store, self._clock, max_s=self._cfg.unscanned_after_s)
        lease = await lease_next(self._store, self._clock, lease_s=self._cfg.lease_s)
        if lease is None:
            return False
        try:
            async with LeaseRenewer(self._store, lease, self._clock, lease_s=self._cfg.lease_s):
                with acting_for(HouseholdId(lease.household_id)):
                    await process(lease, self._deps)
        except LeaseLost:
            log.warning("lease lost for %s; another worker owns the row now", lease.inbox_id)
        except Exception as exc:  # noqa: BLE001  (the lease runs out and the row is tried again)
            log.warning("step failed for %s: %s", lease.inbox_id, type(exc).__name__)
            self._deps.trace.span(
                "worker.process", inbox_id=lease.inbox_id, household_id=lease.household_id, ok=False,
                reason=type(exc).__name__,
            )  # fmt: skip
        return True

    async def _ack_loop(self) -> None:
        while True:
            try:
                await ack_pass(self._store, self._deps, self._clock)
            except Exception as exc:  # noqa: BLE001
                log.warning("ack pass failed: %s", type(exc).__name__)
            await asyncio.sleep(self._cfg.poll_s)

    async def run_forever(self) -> None:
        """Poll until cancelled. A round that found a row polls again at once; an idle round waits `poll_s`."""
        acker = asyncio.create_task(self._ack_loop())
        try:
            while True:
                try:
                    busy = await self.run_once(ack=False)
                except Exception as exc:  # noqa: BLE001  (a store outage is waited out, not fatal)
                    log.warning("worker round failed: %s", type(exc).__name__)
                    busy = False
                if not busy:
                    await asyncio.sleep(self._cfg.poll_s)
        finally:
            acker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await acker


def _probe_alerts(owner_alert: Callable[[str], Awaitable[None]]) -> Callable[[ProbeResult], Awaitable[None]]:
    """Tell the owner when a probe changes to a state that is not ok, and once when it recovers; never for the first
    ok and not at every beat."""
    last: dict[str, ProbeStatus] = {}

    async def on_result(result: ProbeResult) -> None:
        prev = last.get(result.name)
        last[result.name] = result.status
        if prev is not result.status and (result.status is not ProbeStatus.OK or prev is not None):
            await owner_alert(f"probe_{result.name}_{result.status.value}")

    return on_result


async def serve(s: Settings, build: Callable[[Settings], WorkerDeps | Awaitable[WorkerDeps]]) -> None:
    """Start-up: `require_secrets` first (the approval secret is the worker's, never the edge's), the outbound host
    table next, then compose and run. The core heartbeat beats beside the loop (RQ-104)."""
    require_secrets(s)
    require_listed_hosts(s)
    built = build(s)
    deps = await built if inspect.isawaitable(built) else built
    if isinstance(deps.caregiver, PendingCaregiverChannel):
        if s.env == "prod":
            with contextlib.suppress(Exception):
                await deps.store.close()
            raise ConfigError("caregiver channel not wired (WU-18)")
        await deps.owner_alert("caregiver_pending")
    probes = [partial(disk_probe, Path(s.media_dir)), partial(skew_probe, deps.store, deps.clock)]
    beat = asyncio.create_task(beat_loop(deps.store, deps.clock, probes, on_result=_probe_alerts(deps.owner_alert)))
    try:
        await Worker(deps.store, deps, deps.clock).run_forever()
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
