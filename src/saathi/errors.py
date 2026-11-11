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


class ClipUnreviewed(ClipMissing):
    """M16: a clip whose review columns are null is not usable; callers that handle ClipMissing handle this too."""


# Vision
class VisionUnavailable(SaathiError): ...


# Gateway
class PrivacyViolation(SaathiError): ...


class BudgetExceeded(SaathiError): ...


class SchemaFailure(SaathiError): ...


class NoAcceptedResult(SaathiError): ...


# Guards (M11): not a port error; the gateway maps it to PrivacyViolation
class RestrictedDataLeft(SaathiError):  # noqa: N818
    """Text still matches a restricted pattern, or the input is not text."""


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


def _errors_of() -> dict[type, tuple[type[SaathiError], ...]]:
    from saathi import ports  # late: ports imports the domain, never this module

    store = PORT_ERRORS["Store, KeyRing"]
    voice = PORT_ERRORS["TTS, VoiceStore"]
    asr = PORT_ERRORS["ASR, AudioCodec"]
    return {
        ports.ElderChannel: PORT_ERRORS["ElderChannel"],
        ports.CaregiverChannel: PORT_ERRORS["CaregiverChannel"],
        ports.Escalator: PORT_ERRORS["Escalator"],
        ports.ASR: asr,
        ports.AudioCodec: asr,
        ports.TTS: voice,
        ports.VoiceStore: voice,
        ports.Vision: PORT_ERRORS["Vision"],
        ports.Gateway: PORT_ERRORS["Gateway"],
        ports.AuditSink: PORT_ERRORS["AuditSink"],
        ports.Store: store,
        ports.Tx: store,
        ports.Sealer: store,
        ports.Opener: store,
        # no listed errors: a clock, a classifier, a trace exporter and the media store raise none of the TRD's
        ports.Clock: (),
        ports.Classifier: (),
        ports.TraceExporter: (),
        ports.MediaStore: (),
    }


# The same table keyed by Protocol class (a shared TRD row lists several ports). Built on first use.
ERRORS_OF: dict[type, tuple[type[SaathiError], ...]] = _errors_of()
