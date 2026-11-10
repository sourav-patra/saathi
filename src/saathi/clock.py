"""Clocks (M03). `datetime.now()` is called only here; every machine takes a `Clock`."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep_until(self, when: datetime) -> None:
        delay = (when - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)


class FakeClock:
    """A clock the test moves by hand. `advance` wakes sleepers in due order, each seeing its own due time."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start if start is not None else datetime(2026, 1, 1, tzinfo=UTC)
        if self._now.tzinfo is None:
            raise ValueError("FakeClock needs an aware start time")
        self._mono = 0.0
        self._seq = itertools.count()
        self._sleepers: list[tuple[datetime, int, asyncio.Future[None]]] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    async def sleep_until(self, when: datetime) -> None:
        if when <= self._now:
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (when, next(self._seq), fut))
        await fut

    async def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a clock does not go back")
        target = self._now + timedelta(seconds=seconds)
        target_mono = self._mono + seconds
        start_now, start_mono = self._now, self._mono
        while self._sleepers and self._sleepers[0][0] <= target:
            when, _, fut = heapq.heappop(self._sleepers)
            self._mono = start_mono + (when - start_now).total_seconds()
            self._now = when
            if not fut.done():
                fut.set_result(None)
            for _ in range(3):  # let the woken task run before the next one wakes
                await asyncio.sleep(0)
        self._now = target
        self._mono = target_mono
        await asyncio.sleep(0)
