"""The audit write path (M17)."""

from saathi.audit.events import AuditName
from saathi.audit.sink import PgAuditSink

__all__ = ["AuditName", "PgAuditSink"]
