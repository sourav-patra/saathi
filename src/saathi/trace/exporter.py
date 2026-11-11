"""The JSON-lines trace exporter (M18; RQ-57, RQ-44, RQ-97).

`span` never raises and never blocks on anything but one small local append. The fields go through the allowlist
first, so a transcript, a prompt or a whole node output cannot reach a file even when a caller passes it. Counts of
what was dropped and of write errors are kept on the exporter for the health report; a trace is a debug aid and is not
the audit trail. The Langfuse client is a Phase 1 exporter behind the same port (D-74).
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from saathi.domain import SpanValue
from saathi.ports import Clock, TraceExporter
from saathi.trace.allowlist import clean_fields, clean_name
from saathi.trace.jsonl import JsonlSink

log = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
_ID_KEYS = ("run_id", "request_id", "inbox_id", "event_id", "household_id")

P = ParamSpec("P")
R = TypeVar("R")


class JsonlExporter:
    """Implements `TraceExporter`."""

    def __init__(self, directory: Path, clock: Clock, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self._clock = clock
        self._sink = JsonlSink(Path(directory), retention_days)
        self.spans = 0
        self.dropped = 0
        self.export_errors = 0

    def span(self, name: str, **fields: SpanValue) -> None:
        try:
            when = self._clock.now()
            kept, dropped = clean_fields(fields)
            self._sink.write(when, {"ts": when.isoformat(), "name": clean_name(name), **kept})
            self.spans += 1
            self.dropped += dropped
        except Exception as exc:  # noqa: BLE001  (an unwritable file or a full disk never reaches the request)
            self.export_errors += 1
            log.warning("trace export failed: %s", type(exc).__name__)


class NullExporter:
    """Implements `TraceExporter` and writes nothing: the default when tracing is not wired."""

    def span(self, name: str, **fields: SpanValue) -> None:
        return None


def build_exporter(trace_dir: str, clock: Clock, retention_days: int = DEFAULT_RETENTION_DAYS) -> TraceExporter:
    """The file exporter for a directory, or the null one when the setting is empty."""
    return JsonlExporter(Path(trace_dir), clock, retention_days) if trace_dir else NullExporter()


def traced(name: str, exporter: TraceExporter) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap a function (sync or async) in one span holding the ids passed by keyword, `duration_ms` and `ok`.

    Arguments and the return value are never recorded: only keyword arguments named like an id are, and they pass the
    allowlist like any other field.
    """

    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        def ids(kwargs: dict[str, Any]) -> dict[str, SpanValue]:
            return {k: kwargs[k] for k in _ID_KEYS if isinstance(kwargs.get(k), str)}

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                start, ok = time.perf_counter(), False
                try:
                    result = await fn(*args, **kwargs)
                    ok = True
                    return result
                finally:
                    ms = round((time.perf_counter() - start) * 1000, 3)
                    exporter.span(name, duration_ms=ms, ok=ok, **ids(kwargs))

            return awrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start, ok = time.perf_counter(), False
            try:
                result = fn(*args, **kwargs)
                ok = True
                return result
            finally:
                ms = round((time.perf_counter() - start) * 1000, 3)
                exporter.span(name, duration_ms=ms, ok=ok, **ids(kwargs))

        return wrapper

    return decorate
