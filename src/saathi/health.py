"""Health probes and the decision behind the external heartbeat (M20; RQ-104, TQ-66).

The core runs `beat_loop`: every few seconds it runs its probes and writes one heartbeat row through
`control.beat('core', now, status)`. The public health route (M07) calls `edge_check`, which writes the edge beat,
reads back the newest core beat and answers through `decide`. The answer is 200 with the body `ok` and nothing else,
or 503 with no body. `decide` is pure. Nothing here answers `ok` for a stale beat, a failed write, a full disk or a
probe that could not run.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from saathi.ids import HouseholdId
from saathi.ports import Clock, Store

log = logging.getLogger(__name__)

OK_BODY = "ok"
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAIL = "fail"
# The heartbeat table is tenantless; a transaction still needs a non-empty scope, and no tenant row carries this one.
SYSTEM_SCOPE = HouseholdId("-system-")


class ProbeStatus(StrEnum):
    OK = "ok"
    WARN = "warn"  # alerts the ops chat; the route stays ok
    FAIL = "fail"
    UNAVAILABLE = "unavailable"  # the probe could not run: never ok


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: ProbeStatus
    value: float | None = None  # free percent for the disk, seconds of skew for the clock
    gates_route: bool = False  # a FAIL that turns the public route to 503 (the full disk)

    @property
    def refuses(self) -> bool:
        """True when callers must refuse the guarded work (clock skew over the refuse limit, RQ-22, RQ-18)."""
        return self.status is ProbeStatus.FAIL


Probe = Callable[[], Awaitable[ProbeResult]]
OnResult = Callable[[ProbeResult], Awaitable[None]]


def decide(core_beat_at: datetime | None, core_status: str, now: datetime, max_age_s: int = 30) -> int:
    """200 only for a core beat that is fresh and whose status is `ok`; every other case is 503 (fails closed)."""
    if core_beat_at is None or core_status != STATUS_OK:
        return 503
    if core_beat_at.tzinfo is None or now.tzinfo is None:
        return 503
    age_s = (now - core_beat_at).total_seconds()
    return 200 if abs(age_s) < max_age_s else 503  # a beat from the future is as untrustworthy as an old one


def body_for(code: int) -> str:
    """The route shows nothing but `ok`, or nothing at all (no name, number, count, version or state)."""
    return OK_BODY if code == 200 else ""


def aggregate(results: Sequence[ProbeResult]) -> str:
    """The status the core writes. No probe, or a probe that could not run, is `unavailable`, never ok."""
    if not results or any(r.status is ProbeStatus.UNAVAILABLE for r in results):
        return STATUS_UNAVAILABLE
    if any(r.status is ProbeStatus.FAIL and r.gates_route for r in results):
        return STATUS_FAIL
    return STATUS_OK


async def disk_probe(path: Path, warn_free_pct: float = 20, fail_free_pct: float = 10) -> ProbeResult:
    """Free space on the data volume: under `warn_free_pct` alerts, under `fail_free_pct` turns the route to 503."""
    try:
        usage = await asyncio.to_thread(shutil.disk_usage, path)
    except OSError:
        return ProbeResult("disk", ProbeStatus.UNAVAILABLE)
    if usage.total <= 0:
        return ProbeResult("disk", ProbeStatus.UNAVAILABLE)
    free_pct = 100.0 * usage.free / usage.total
    if free_pct < fail_free_pct:
        return ProbeResult("disk", ProbeStatus.FAIL, free_pct, gates_route=True)
    if free_pct < warn_free_pct:
        return ProbeResult("disk", ProbeStatus.WARN, free_pct)
    return ProbeResult("disk", ProbeStatus.OK, free_pct)


async def skew_probe(store: Store, clock: Clock, warn_s: float = 5, refuse_s: float = 15) -> ProbeResult:
    """App clock against Postgres `now()`: over `warn_s` alerts, over `refuse_s` makes token minting refuse."""
    try:
        async with store.tx(household=SYSTEM_SCOPE) as tx:
            rows = await tx.fetch("SELECT now() AS db_now")
        app_now = clock.now()
        db_now = rows[0]["db_now"]
        skew_s = abs((app_now - db_now).total_seconds())
    except Exception:  # noqa: BLE001 - any failure to measure is "could not run"
        return ProbeResult("skew", ProbeStatus.UNAVAILABLE)
    if skew_s > refuse_s:
        return ProbeResult("skew", ProbeStatus.FAIL, skew_s)
    if skew_s > warn_s:
        return ProbeResult("skew", ProbeStatus.WARN, skew_s)
    return ProbeResult("skew", ProbeStatus.OK, skew_s)


async def _run(probe: Probe, timeout_s: float) -> ProbeResult:
    try:
        return await asyncio.wait_for(probe(), timeout_s)
    except TimeoutError:  # a probe that hangs did not run
        log.warning("probe timed out")
        return ProbeResult(getattr(probe, "__name__", "probe"), ProbeStatus.UNAVAILABLE)
    except Exception:  # noqa: BLE001 - a probe that raises did not run
        log.warning("probe raised")
        return ProbeResult(getattr(probe, "__name__", "probe"), ProbeStatus.UNAVAILABLE)


async def _write_beat(
    store: Store, component: str, now: datetime, status: str
) -> tuple[datetime | None, str]:
    async with store.tx(household=SYSTEM_SCOPE) as tx:
        rows = await tx.fetch("SELECT core_at, core_status FROM control.beat($1, $2, $3)", component, now, status)
    if not rows:
        return None, ""
    return rows[0]["core_at"], rows[0]["core_status"]


async def beat_once(
    store: Store,
    clock: Clock,
    probes: Sequence[Probe],
    on_result: OnResult | None = None,
    probe_timeout_s: float = 5,
) -> str:
    """Run the probes, then write the core beat once. Returns the status written (`unavailable` if the write failed)."""
    results = [await _run(p, probe_timeout_s) for p in probes]
    if on_result is not None:
        for r in results:
            if r.status is not ProbeStatus.OK:
                try:
                    await on_result(r)
                except Exception:  # noqa: BLE001 - an alert that fails must not stop the beat
                    log.warning("alert callback raised")
    status = aggregate(results)
    try:
        await _write_beat(store, "core", clock.now(), status)
    except Exception:  # noqa: BLE001 - no write means a stale beat, which the route reports as 503
        log.warning("core beat write failed")
        return STATUS_UNAVAILABLE
    return status


async def beat_loop(
    store: Store,
    clock: Clock,
    probes: Sequence[Probe],
    every_s: int = 10,
    on_result: OnResult | None = None,
    probe_timeout_s: float = 5,
) -> None:
    """Runs until cancelled."""
    while True:
        await beat_once(store, clock, probes, on_result, probe_timeout_s)
        await clock.sleep_until(clock.now() + timedelta(seconds=every_s))


async def edge_check(store: Store, clock: Clock, max_age_s: int = 30, timeout_s: float = 5) -> int:
    """What the public route does: write the edge beat, read the core beat, decide. Any failure is 503."""
    try:
        now = clock.now()
        core_at, core_status = await asyncio.wait_for(_write_beat(store, "edge", now, STATUS_OK), timeout_s)
    except Exception:  # noqa: BLE001 - a failed or hung write is 503, whatever the cause
        return 503
    return decide(core_at, core_status, now, max_age_s)
