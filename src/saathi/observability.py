"""Langfuse tracing (MIT SDK, self-hosted server) with PII masking.

- Every elder request is one trace. Pipeline nodes are spans, and model calls
  are generations carrying tokens and cost in INR.
- The SDK's ``mask`` hook runs our deterministic PII redactor over everything
  before it leaves the process, so traces never hold Aadhaar, PAN, phone
  numbers, OTPs, etc.
- If Langfuse isn't configured or is unreachable, everything degrades to
  no-ops. Tracing must never break the elder's request.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from saathi.guards.pii import redact

log = logging.getLogger(__name__)

_client: Any = None


def _mask(*, data: Any, **_: Any) -> Any:
    if isinstance(data, str):
        return redact(data).text
    if isinstance(data, dict):
        return {k: _mask(data=v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [_mask(data=v) for v in data]
    return data


def init_tracing(*, host: str, public_key: str, secret_key: str, environment: str = "dev") -> bool:
    global _client
    if not (host and public_key and secret_key):
        log.info("langfuse not configured; tracing disabled")
        return False
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            environment=environment,
            mask=_mask,
        )
        return True
    except Exception as e:  # pragma: no cover
        log.warning("langfuse init failed: %s", e)
        _client = None
        return False


class _NoopObs:
    def update(self, **_: Any) -> None: ...


@contextmanager
def observe(name: str, *, as_type: str = "span", **fields: Any) -> Iterator[Any]:
    """``with observe("router", as_type="chain", input=...) as obs: obs.update(output=...)``"""
    if _client is None:
        yield _NoopObs()
        return
    try:
        cm = _client.start_as_current_observation(name=name, as_type=as_type, **fields)
        obs = cm.__enter__()
    except Exception as e:  # pragma: no cover
        log.debug("tracing failed: %s", e)
        yield _NoopObs()
        return
    try:
        yield obs
    except BaseException as exc:
        try:
            obs.update(level="ERROR", status_message=str(exc)[:500])
        finally:
            cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        cm.__exit__(None, None, None)


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception:  # pragma: no cover
            pass
