"""Type-check target: each channel class must be assignable to the `ElderChannel` port (run by mypy)."""

from __future__ import annotations

from fakes import FakeElderChannel

from saathi.channels.telegram_elder import TelegramElderChannel
from saathi.channels.whatsapp import WhatsAppElderChannel
from saathi.ports import ElderChannel


def whatsapp(channel: WhatsAppElderChannel) -> ElderChannel:
    return channel


def telegram(channel: TelegramElderChannel) -> ElderChannel:
    return channel


def fake(channel: FakeElderChannel) -> ElderChannel:
    return channel
