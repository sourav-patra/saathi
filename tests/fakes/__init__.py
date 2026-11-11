"""Fakes for every port in saathi.ports. Each can fail on demand with every error its port lists."""

from fakes.base import Failable
from fakes.channels import FakeCaregiverChannel, FakeElderChannel
from fakes.clock import FakeClock
from fakes.escalators import FakeEscalator
from fakes.people import FakePersonDirectory
from fakes.services import (
    FakeAudit,
    FakeClassifier,
    FakeGateway,
    FakeMediaStore,
    FakeTrace,
    FakeVision,
)
from fakes.store import FakeOpener, FakeSealer, FakeStore, FakeTx
from fakes.voice import FakeASR, FakeAudioCodec, FakeCodec, FakeTTS, FakeVoiceStore

__all__ = [
    "Failable", "FakeASR", "FakeAudioCodec", "FakeAudit", "FakeCaregiverChannel", "FakeClassifier", "FakeClock",
    "FakeCodec",
    "FakeElderChannel", "FakeEscalator", "FakeGateway", "FakeMediaStore", "FakeOpener", "FakePersonDirectory",
    "FakeSealer",
    "FakeStore", "FakeTTS", "FakeTrace", "FakeTx", "FakeVision", "FakeVoiceStore",
]  # fmt: skip
