"""The public health route (M07 with M20; RQ-104). The body is `ok` or empty and carries nothing else."""

from __future__ import annotations

from fastapi import FastAPI, Response

from saathi.health import body_for, edge_check
from saathi.ports import Clock
from saathi.store import Store


def add_health_route(app: FastAPI, path: str, store: Store, clock: Clock) -> None:
    @app.get(path)
    async def health() -> Response:
        code = await edge_check(store, clock)
        return Response(content=body_for(code), status_code=code, media_type="text/plain")
