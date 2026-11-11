"""Subprocess entry points for the crash tests (LT-08): the edge and the worker as separate processes.

``python crash_proc.py edge <port>`` serves the real edge composition on 127.0.0.1.
``python crash_proc.py worker`` runs the real worker with recording fakes for the platforms and models, until no inbox
row of the household is waiting (exit 0) or the time is up (exit 3).
``python crash_proc.py alerts`` runs the real alert service for `CRASH_ALERT_SECONDS` seconds (exit 0).
The fakes keep a journal of every effect in a file, so a restarted process sees the keys the killed one already sent
and the channel refuses a second send with the same key. The journal records the refused sends too (R16-2), so a test
can show that a retry with the same key happened and was refused. Everything is synthetic; the settings come from the
environment the test sets.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from fakes import (
    FakeASR,
    FakeAudioCodec,
    FakeAudit,
    FakeCaregiverChannel,
    FakeElderChannel,
    FakeMediaStore,
    FakeVoiceStore,
)

from saathi.clock import SystemClock
from saathi.config import get_settings
from saathi.errors import SendRejected
from saathi.ids import HouseholdId
from saathi.store import Role, Store, create_pool
from saathi.store.keys import KeyRing
from saathi.worker import Worker, WorkerConfig, WorkerDeps

QUIET_TIMEOUT_S = 40.0
LEASE_S = 2  # a killed worker's lease runs out quickly, so the restart does not wait a minute


class Journal:
    """One JSON line per send that reached a fake platform, `sent`, or that it refused as a repeat, `refused`. The file
    is the platform's memory across restarts; only a `sent` line makes a key known."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def keys(self) -> set[str]:
        if not self.path.exists():
            return set()
        lines = (json.loads(line) for line in self.path.read_text().splitlines() if line)
        return {e["key"] for e in lines if e.get("outcome", "sent") == "sent"}

    def add(self, to: str, method: str, key: object, outcome: str = "sent") -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"to": to, "method": method, "key": str(key), "outcome": outcome}) + "\n")
            f.flush()
            os.fsync(f.fileno())


class JournalElder(FakeElderChannel):
    def __init__(self, journal: Journal) -> None:
        super().__init__()
        self._journal = journal

    def _send(self, method, to, payload, key, *, free_form):  # type: ignore[no-untyped-def]
        if str(key) in self._journal.keys():
            self._enter(method, to, payload, key=key)
            self._journal.add("elder", method, key, "refused")
            raise SendRejected("duplicate_key")
        receipt = super()._send(method, to, payload, key, free_form=free_form)
        self._journal.add("elder", method, key)
        return receipt


class JournalCaregiver(FakeCaregiverChannel):
    def __init__(self, journal: Journal) -> None:
        super().__init__()
        self._journal = journal

    async def send_card(self, to, card, *, key):  # type: ignore[no-untyped-def]
        if str(key) in self._journal.keys():
            self._enter("send_card", to, card, key=key)
            self._journal.add("caregiver", "send_card", key, "refused")
            raise SendRejected("duplicate_key")
        ref = await super().send_card(to, card, key=key)
        self._journal.add("caregiver", "send_card", key)
        return ref

    def _send(self, method, to, payload, key):  # type: ignore[no-untyped-def]
        if str(key) in self._journal.keys():
            self._enter(method, to, payload, key=key)
            self._journal.add("caregiver", method, key, "refused")
            raise SendRejected("duplicate_key")
        receipt = super()._send(method, to, payload, key)
        self._journal.add("caregiver", method, key)
        return receipt


async def _waiting(store: Store, household: str) -> int:
    async with store.tx(household=HouseholdId(household)) as tx:
        rows = await tx.fetch("SELECT count(*) AS n FROM inbox WHERE state IN ('received', 'leased')")
    return int(rows[0]["n"])


async def run_worker() -> int:
    clock = SystemClock()
    journal = Journal(os.environ["CRASH_JOURNAL"])
    household = os.environ["CRASH_HOUSEHOLD"]
    store = Store(await create_pool(get_settings().db_url_core), Role.CORE, clock)

    async def owner_alert(_what: str) -> None:
        return None

    deps = WorkerDeps(
        store=store, keys=KeyRing(bytes.fromhex(os.environ["CRASH_KEK_HEX"])), elder=JournalElder(journal),
        caregiver=JournalCaregiver(journal), voice=FakeVoiceStore(), asr=FakeASR(), codec=FakeAudioCodec(),
        media=FakeMediaStore(), audit=FakeAudit(), clock=clock, owner_alert=owner_alert,
        cfg=WorkerConfig(lease_s=LEASE_S),
    )  # fmt: skip
    worker = Worker(store, deps, clock)
    deadline = time.monotonic() + QUIET_TIMEOUT_S
    try:
        while time.monotonic() < deadline:
            busy = await worker.run_once()
            if not busy:
                if await _waiting(store, household) == 0:
                    return 0
                await asyncio.sleep(0.3)
        return 3
    finally:
        await store.close()


async def run_alerts() -> int:
    """The real alert service on the system clock, with the journaling channels and fake stages b and c."""
    from fakes import FakeEscalator

    from saathi.alerts import AlertService, TelegramStageA
    from saathi.domain import Source
    from saathi.store.repos import SignoffRepo
    from saathi.voice.store import ReplyKey

    clock = SystemClock()
    journal = Journal(os.environ["CRASH_JOURNAL"])
    store = Store(await create_pool(get_settings().db_url_core), Role.CORE, clock)

    async def owner_alert(_what: str) -> None:
        return None

    caregiver = JournalCaregiver(journal)
    voice = FakeVoiceStore()
    for reply in (ReplyKey.EMERGENCY, ReplyKey.SCAM):
        voice.add(reply, b"synthetic-clip-" + reply.value.encode(), "hi")
    service = AlertService(
        store, caregiver, {Source.WHATSAPP: JournalElder(journal)}, voice,
        {"a": TelegramStageA(caregiver), "b": FakeEscalator("b"), "c": FakeEscalator("c")},
        FakeAudit(), SignoffRepo, clock, owner_alert=owner_alert,
    )  # fmt: skip
    deadline = time.monotonic() + float(os.environ.get("CRASH_ALERT_SECONDS", "6"))
    try:
        while time.monotonic() < deadline:
            await service.tick()
            await asyncio.sleep(0.3)
        return 0
    finally:
        await store.close()


def run_edge(port: int) -> None:
    import uvicorn

    from saathi.edge.wiring import build_edge, uvicorn_config

    async def serve() -> None:
        app, store = await build_edge(get_settings())
        try:
            await uvicorn.Server(uvicorn_config(app, host="127.0.0.1", port=port)).serve()
        finally:
            await store.close()

    asyncio.run(serve())


if __name__ == "__main__":
    if sys.argv[1] == "edge":
        run_edge(int(sys.argv[2]))
    elif sys.argv[1] == "alerts":
        sys.exit(asyncio.run(run_alerts()))
    else:
        sys.exit(asyncio.run(run_worker()))
