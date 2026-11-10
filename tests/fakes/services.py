"""Fakes for vision, the gateway, audit, the classifier, the trace exporter and the media store."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel

from fakes.base import Failable
from saathi.domain import (
    AuditEvent,
    AuditRef,
    ClassifierFlag,
    DataBlock,
    Grade,
    ImageBlob,
    MediaBlob,
    MediaRef,
    ModelRequest,
    ModelResult,
    SpanValue,
    VisionResult,
    VisionTask,
)
from saathi.errors import NoAcceptedResult
from saathi.ids import HouseholdId, RunId
from saathi.ports import Tx


class FakeVision(Failable):
    def __init__(self, text: str = "synthetic ocr text") -> None:
        super().__init__()
        self.text = text

    async def read(self, image: ImageBlob, task: VisionTask, *, run: RunId) -> VisionResult:
        self._enter("read", image, task, run=run)
        return VisionResult(ocr_text=DataBlock(text=self.text, origin="ocr"))


class FakeGateway(Failable):
    """Scripted results. With nothing scripted it raises NoAcceptedResult rather than inventing an answer."""

    def __init__(self) -> None:
        super().__init__()
        self.script: deque[ModelResult[Any]] = deque()
        self.requests: list[ModelRequest[Any]] = []

    def will_return(self, value: BaseModel | None = None, *, refusal: bool = False, reason: str | None = None) -> None:
        self.script.append(ModelResult(value=value, refusal=refusal, refusal_reason=reason, grade=Grade.G1))

    async def complete[T: BaseModel](self, req: ModelRequest[T]) -> ModelResult[T]:
        self._enter("complete", req)
        self.requests.append(req)
        if not self.script:
            raise NoAcceptedResult()
        return self.script.popleft()


class FakeAudit(Failable):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[AuditEvent] = []

    async def append(self, ev: AuditEvent, *, tx: Tx | None = None) -> AuditRef:
        self._enter("append", ev, tx=tx)
        self.events.append(ev)
        return AuditRef(id=f"audit-{len(self.events)}")


class FakeClassifier(Failable):
    """Flags by substring: `add_rule("phrase", ClassifierFlag(...))`."""

    def __init__(self) -> None:
        super().__init__()
        self.rules: list[tuple[str, ClassifierFlag]] = []

    def add_rule(self, needle: str, flag: ClassifierFlag) -> None:
        self.rules.append((needle, flag))

    async def flags(self, text: str, *, run: RunId) -> list[ClassifierFlag]:
        self._enter("flags", text, run=run)
        return [flag for needle, flag in self.rules if needle in text]


class FakeTrace:
    """Never raises and never blocks, as the port promises."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, SpanValue]]] = []

    def span(self, name: str, **fields: SpanValue) -> None:
        self.spans.append((name, fields))


class FakeMediaStore(Failable):
    def __init__(self) -> None:
        super().__init__()
        self.blobs: dict[str, MediaBlob] = {}

    async def put(self, ref: MediaRef, blob: MediaBlob, *, household: HouseholdId) -> str:
        self._enter("put", ref, blob, household=household)
        key = f"{household}/{ref.handle}"
        self.blobs[key] = blob
        return key

    async def get(self, key: str) -> MediaBlob:
        self._enter("get", key)
        return self.blobs[key]
