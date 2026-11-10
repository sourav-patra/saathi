"""Repos are stateless classes with static async methods that take a `Tx` first (M04, DI-4)."""

from saathi.store.repos.alerts import AlertRepo
from saathi.store.repos.flags import FlagRepo
from saathi.store.repos.identities import IdentityRepo
from saathi.store.repos.inbox import InboxRepo
from saathi.store.repos.requests import RequestRepo
from saathi.store.repos.updates import UpdateRepo

__all__ = ["AlertRepo", "FlagRepo", "IdentityRepo", "InboxRepo", "RequestRepo", "UpdateRepo"]
