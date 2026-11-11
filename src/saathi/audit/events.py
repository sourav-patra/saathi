"""Audit event names (M17). The sink stores the name as text; callers use these constants."""

from __future__ import annotations

from enum import StrEnum


class AuditName(StrEnum):
    DENY = "deny"
    ACK_TAP = "ack_tap"
    ACK_VOICE = "ack_voice"
    CALL_NO_ACK = "call_no_ack"
    SHOW_RAW = "show_raw"
    SIGNOFF_USED = "signoff_used"
    SIGNOFF_EXPIRED = "signoff_expired"
    CONSENT_DENIED = "consent_denied"
    INBOX_DEAD = "inbox_dead"
    CLIP_ERROR = "clip_error"
    CONFIG_LOADED = "config_loaded"
    ALERT_BLOCKED = "alert_blocked"
    FALSE_ALARM = "false_alarm"
    KEY_ROTATED = "key_rotated"
