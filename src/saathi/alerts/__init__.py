"""Escalation of emergency, scam and unscanned alerts to the humans (M14; RQ-18, RQ-95a).

`saathi.alerts.plan` is the pure plan module; its `plan` function is not re-exported, so the name stays the module.
"""

from saathi.alerts.plan import (
    BACKUP_CALL_DELAY_S,
    CADENCE_A_S,
    FOLLOWUP_S,
    REPEAT_CLIP_AT_S,
    STAGE_A_CAP,
    STAGE_B_AT_S,
    STAGE_B_EVERY_S,
    STAGE_B_MAX,
    STAGE_C_AT_S,
    STAGE_C_EVERY_S,
    STAGE_C_PER_APPROVER,
    THEN_EVERY_S,
    AlertConfig,
    Planned,
)
from saathi.alerts.service import AlertService, attempt_key
from saathi.alerts.stages import TelegramStageA, UnavailableEscalator, alert_card

__all__ = [
    "BACKUP_CALL_DELAY_S",
    "CADENCE_A_S",
    "FOLLOWUP_S",
    "REPEAT_CLIP_AT_S",
    "STAGE_A_CAP",
    "STAGE_B_AT_S",
    "STAGE_B_EVERY_S",
    "STAGE_B_MAX",
    "STAGE_C_AT_S",
    "STAGE_C_EVERY_S",
    "STAGE_C_PER_APPROVER",
    "THEN_EVERY_S",
    "AlertConfig",
    "AlertService",
    "Planned",
    "TelegramStageA",
    "UnavailableEscalator",
    "alert_card",
    "attempt_key",
]
