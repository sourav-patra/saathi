"""Repos are stateless classes with static async methods that take a `Tx` first (M04, DI-4)."""

from saathi.store.repos.alerts import AlertRepo
from saathi.store.repos.config import ConfigRepo
from saathi.store.repos.consents import ConsentEvent, ConsentRepo
from saathi.store.repos.flags import FlagRepo
from saathi.store.repos.identities import IdentityRepo
from saathi.store.repos.inbox import InboxRepo
from saathi.store.repos.requests import RequestRepo
from saathi.store.repos.signoffs import SignoffRepo, SignoffState
from saathi.store.repos.updates import UpdateRepo
from saathi.store.repos.voice import VOICE_SCOPE, ClipRow, VoiceRepo

__all__ = [
    "VOICE_SCOPE",
    "AlertRepo",
    "ClipRow",
    "ConfigRepo",
    "ConsentEvent",
    "ConsentRepo",
    "FlagRepo",
    "IdentityRepo",
    "InboxRepo",
    "RequestRepo",
    "SignoffRepo",
    "SignoffState",
    "UpdateRepo",
    "VoiceRepo",
]
