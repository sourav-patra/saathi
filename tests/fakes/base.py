"""Shared behaviour of every fake: record calls, and fail on demand with any error its port lists."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class Failable:
    """`fail("send_text", ChannelDown)` makes the next call of that method raise; `times` repeats it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._failures: dict[str, list[BaseException]] = defaultdict(list)

    def fail(self, method: str, error: BaseException | type[BaseException], *, times: int = 1) -> None:
        for _ in range(times):
            self._failures[method].append(error() if isinstance(error, type) else error)

    def clear_failures(self) -> None:
        self._failures.clear()

    def _enter(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))
        queue = self._failures.get(method)
        if queue:
            raise queue.pop(0)

    def calls_of(self, method: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(a, k) for m, a, k in self.calls if m == method]
