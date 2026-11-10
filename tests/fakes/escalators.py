"""Scripted escalator (LLD tests part 1, rule 3): delivered, failed, answered_no_ack, voicemail, key press 1, forged."""

from __future__ import annotations

from collections import deque
from typing import Literal

from fakes.base import Failable
from saathi.domain import AlertEventRef, AttemptResult, VoiceAckResult, VoiceCallback
from saathi.errors import CallbackRejected, SignoffMissing
from saathi.ids import ApproverId, IdemKey

VALID_SIGNATURE = "valid"


class FakeEscalator(Failable):
    def __init__(self, stage: Literal["a", "b", "c"] = "a") -> None:
        super().__init__()
        self.stage = stage
        self.signoff_valid = True  # stages b and c refuse before any send when False (RQ-95a)
        self.script: deque[AttemptResult] = deque()
        self.notified: list[tuple[AlertEventRef, ApproverId, IdemKey]] = []
        self._by_call: dict[str, ApproverId] = {}
        self._seen_callbacks: set[tuple[str, str]] = set()
        self._results: dict[IdemKey, AttemptResult] = {}

    def will_return(self, *results: AttemptResult) -> None:
        self.script.extend(results)

    async def notify(self, event: AlertEventRef, approver: ApproverId, *, key: IdemKey) -> AttemptResult:
        self._enter("notify", event, approver, key=key)
        if key in self._results:
            return self._results[key]
        if self.stage != "a" and not self.signoff_valid:
            raise SignoffMissing()
        result = self.script.popleft() if self.script else AttemptResult.DELIVERED
        self._results[key] = result
        self.notified.append((event, approver, key))
        if self.stage == "c":
            self._by_call[f"call-{len(self.notified)}"] = approver
        return result

    async def on_dtmf(self, cb: VoiceCallback) -> VoiceAckResult:
        self._enter("on_dtmf", cb)
        if cb.signature != VALID_SIGNATURE or cb.call_id not in self._by_call:
            raise CallbackRejected()  # forged, unknown call id
        if (cb.call_id, cb.digit) in self._seen_callbacks:
            raise CallbackRejected()  # replay
        self._seen_callbacks.add((cb.call_id, cb.digit))
        if cb.digit != "1":
            raise CallbackRejected()  # only digit 1 acknowledges (A12)
        return VoiceAckResult(approver_id=self._by_call[cb.call_id])
