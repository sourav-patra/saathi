"""Errors raised by the ports (TRD ports section 3). A port raises only its listed errors (rule 2).

Every error can be built with no arguments so that fakes can raise it on demand.
"""

from __future__ import annotations


class SaathiError(Exception):
    """Base of every error a port may raise."""


# ElderChannel and CaregiverChannel
class ProactiveNotAllowed(SaathiError):  # noqa: N818  (name fixed by the TRD)
    def __init__(self, reason: str = "window_closed") -> None:
        super().__init__(reason)
        self.reason = reason


class TemplateMissing(SaathiError): ...


class ChannelDown(SaathiError): ...


class MediaExpired(SaathiError): ...


class RateLimited(SaathiError):
    def __init__(self, retry_after: float = 0.0) -> None:
        super().__init__(f"retry after {retry_after}")
        self.retry_after = retry_after


class SendRejected(SaathiError):
    def __init__(self, code: str = "") -> None:
        super().__init__(code)
        self.code = code


# Escalator
class SignoffMissing(SaathiError): ...


class ProviderRejected(SaathiError): ...


class CallbackRejected(SaathiError): ...


# ASR and AudioCodec
class AsrUnavailable(SaathiError): ...


class AudioUndecodable(SaathiError): ...


class DecodeTimeout(SaathiError): ...


# TTS and VoiceStore
class TtsUnavailable(SaathiError): ...


class TtsTimeout(SaathiError): ...


class ReceiptMissing(SaathiError): ...


class ClipMissing(SaathiError): ...


class ClipHashMismatch(SaathiError): ...


# Vision
class VisionUnavailable(SaathiError): ...


# Gateway
class PrivacyViolation(SaathiError): ...


class BudgetExceeded(SaathiError): ...


class SchemaFailure(SaathiError): ...


class NoAcceptedResult(SaathiError): ...


# CommerceAdapter (Phase 1 port; the errors are shared now)
class AdapterUnavailable(SaathiError): ...


class UnsupportedCapability(SaathiError): ...


class ServiceRejected(SaathiError): ...


class CallTimeout(SaathiError): ...


class LookupFailed(SaathiError): ...


class StaleQuote(SaathiError): ...


# PhoneDevice (Phase 1 port)
class DeviceOffline(SaathiError): ...


class StaleDump(SaathiError): ...


class AmbiguousSelector(SaathiError): ...


class GrantRequired(SaathiError): ...


class PackageDrift(SaathiError): ...


# AuditSink
class AuditUnavailable(SaathiError): ...


# Store and KeyRing
class StoreUnavailable(SaathiError): ...


class ConflictLost(SaathiError): ...


class FlagUnreadable(SaathiError): ...


class TenantNotSet(SaathiError): ...


class KeyMissing(SaathiError): ...


# The TRD ports section 3 table, port by port.
PORT_ERRORS: dict[str, tuple[type[SaathiError], ...]] = {
    "ElderChannel": (ProactiveNotAllowed, TemplateMissing, ChannelDown, MediaExpired, RateLimited, SendRejected),
    "CaregiverChannel": (ChannelDown, RateLimited, SendRejected),
    "Escalator": (SignoffMissing, ChannelDown, ProviderRejected, CallbackRejected),
    "ASR, AudioCodec": (AsrUnavailable, AudioUndecodable, DecodeTimeout),
    "TTS, VoiceStore": (TtsUnavailable, TtsTimeout, ReceiptMissing, ClipMissing, ClipHashMismatch),
    "Vision": (ReceiptMissing, VisionUnavailable),
    "Gateway": (PrivacyViolation, BudgetExceeded, ReceiptMissing, SchemaFailure, NoAcceptedResult),
    "CommerceAdapter": (
        AdapterUnavailable,
        UnsupportedCapability,
        ServiceRejected,
        CallTimeout,
        LookupFailed,
        StaleQuote,
    ),
    "PhoneDevice": (DeviceOffline, StaleDump, AmbiguousSelector, GrantRequired, PackageDrift),
    "AuditSink": (AuditUnavailable,),
    "Store, KeyRing": (StoreUnavailable, ConflictLost, FlagUnreadable, TenantNotSet, KeyMissing),
}
