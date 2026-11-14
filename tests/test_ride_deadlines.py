"""Ride database deadlines include rollback/reset stalls without delaying the elder reply. No database required."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from time import monotonic
from types import SimpleNamespace

import pytest

from saathi.domain import MediaKind
from saathi.guards.radar import RadarResult
from saathi.rides.place import Place
from saathi.rides.repo import PlaceRepo
from saathi.worker import steps


@pytest.mark.parametrize("operation", ["all", "count_once"])
@pytest.mark.parametrize("cleanup", ["rollback", "reset"])
async def test_stalled_cleanup_does_not_delay_reply(monkeypatch, caplog, operation, cleanup):
    events = []
    release = asyncio.Event()
    cleaning = asyncio.Event()
    tasks = []

    class Store:
        @asynccontextmanager
        async def tx(self, **_kwargs):
            try:
                yield object()
            except asyncio.CancelledError:
                if cleanup == "rollback":
                    cleaning.set()
                    await release.wait()
                raise
            finally:
                if cleanup == "reset" and asyncio.current_task().cancelling():
                    cleaning.set()
                    await release.wait()

    class Caregiver:
        async def send_card(self, _who, _card, *, key):
            events.append("summary" if ":summary:" in key else "ride")

    async def all_places(*_args, **_kwargs):
        return [Place("p1", "Clinic A", "2 Test Road, Sample Town")]

    async def stalled(*_args, **_kwargs):
        tasks.append(asyncio.current_task())
        await asyncio.Event().wait()

    async def held(*_args):
        pass

    async def reply(*_args, **_kwargs):
        events.append("reply")

    monkeypatch.setattr(steps, "RIDE_DB_TIMEOUT_S", 0.02)
    monkeypatch.setattr(steps, "holds", held)
    monkeypatch.setattr(steps.replies, "send_reply", reply)
    monkeypatch.setattr(PlaceRepo, "all", all_places)
    monkeypatch.setattr(PlaceRepo, operation, stalled)
    ctx = SimpleNamespace(
        household="h1", inbox="m1", kind=MediaKind.TEXT, consent=True, audio_consent=False,
        cfg=SimpleNamespace(approvers=[SimpleNamespace(role="primary", approver_id="ap1")], reply_language="hi"),
        person=None, last_inbound=None,
    )  # fmt: skip
    deps = SimpleNamespace(
        store=Store(), caregiver=Caregiver(), elder=SimpleNamespace(note_inbound=lambda *_args: None),
        voice=None, owner_alert=None, audit=None,
    )  # fmt: skip

    async def deliver():
        ride = await steps._ride_step(ctx, deps, "cab to Clinic A", RadarResult(), True)
        await steps._send_effects(ctx, deps, "r1", RadarResult(), "none", "unknown", None, False, None, ride)

    try:
        started = monotonic()
        await asyncio.wait_for(deliver(), 1.0)
        assert monotonic() - started < 0.22  # 20 ms deadline plus 200 ms scheduling margin
        await asyncio.wait_for(cleaning.wait(), 0.2)
        assert not release.is_set() and any(not task.done() for task in tasks)
        assert events[0] == "summary" and events[-1] == "reply"
        stage = "step" if operation == "all" else "count"
        assert f"ride {stage} failed for m1: TimeoutError" in caplog.text
        assert "Clinic A" not in caplog.text and "Test Road" not in caplog.text
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 0.5)
