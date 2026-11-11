"""The three escalators (M14; D-76): stage a is real, stages b and c are unavailable until Phase 1.

Phase 0 builds stage a over the caregiver channel. Stages b (a WhatsApp template) and c (a voice call) run against the
fakes in `tests/fakes/escalators.py`; in a deployment they are `UnavailableEscalator`, which raises `ChannelDown`, so no
message or call leaves and the service marks the attempt `failed`. Nothing here depends on a model, speech to text,
WhatsApp (stages a and c) or the worker.
"""

from __future__ import annotations

from typing import Literal

from saathi.domain import AlertEventRef, AttemptResult, Button, Card, VoiceAckResult, VoiceCallback
from saathi.errors import CallbackRejected, ChannelDown, SendRejected
from saathi.ids import ApproverId, IdemKey
from saathi.ports import CaregiverChannel

DUPLICATE = "duplicate_key"

_TITLE = {
    "emergency": "Emergency alert",
    "scam": "Scam alert",
    "unscanned": "Voice note not checked",
}
_LINES = {
    "emergency": ["A message from the elder looks like an emergency.", "Please call them now."],
    "scam": ["A message from the elder looks like a scam.", "Please call them before they act on it."],
    "unscanned": ["A voice note could not be checked in time.", "Please listen to it and call them if needed."],
}


def alert_card(ref: AlertEventRef) -> Card:
    """Slots only, never a transcript (RQ-97): the kind, a fixed sentence and the two buttons of the callback router
    (`ack:<event>`, `fa:<event>`)."""
    return Card(
        title=_TITLE[ref.kind],
        lines=list(_LINES[ref.kind]),
        buttons=[
            Button(text="Acknowledge", data=f"ack:{ref.event_id}"),
            Button(text="False alarm", data=f"fa:{ref.event_id}"),
        ],
    )


class TelegramStageA:
    """A real `Escalator` over the caregiver channel. A repeat of a key counts as delivered."""

    stage: Literal["a", "b", "c"] = "a"

    def __init__(self, caregivers: CaregiverChannel) -> None:
        self._caregivers = caregivers

    async def notify(self, event: AlertEventRef, approver: ApproverId, *, key: IdemKey) -> AttemptResult:
        try:
            await self._caregivers.send_card(approver, alert_card(event), key=key)
        except SendRejected as exc:
            if exc.code != DUPLICATE:
                raise
        return AttemptResult.DELIVERED

    async def on_dtmf(self, cb: VoiceCallback) -> VoiceAckResult:
        raise CallbackRejected()  # stage a has no call


class UnavailableEscalator:
    """Stages b and c in production until Phase 1: it sends nothing and says so."""

    def __init__(self, stage: Literal["b", "c"]) -> None:
        self.stage: Literal["a", "b", "c"] = stage

    async def notify(self, event: AlertEventRef, approver: ApproverId, *, key: IdemKey) -> AttemptResult:
        raise ChannelDown()

    async def on_dtmf(self, cb: VoiceCallback) -> VoiceAckResult:
        raise CallbackRejected()
