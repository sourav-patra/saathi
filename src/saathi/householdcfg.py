"""HouseholdConfig: the validated `values` of a `control.household_config` row (M06; RQ-110).

Household values are versioned rows, never settings or environment values. An unset cap denies (RQ-26).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from saathi.errors import SaathiError


class ConfigMissing(SaathiError):
    """The household has no config row. Callers treat it as every optional value unset, and deny."""


class Caps(BaseModel, extra="forbid"):
    order_inr: Decimal = Field(gt=0)
    day_inr: Decimal = Field(gt=0)
    monthly_soft_inr: Decimal = Field(gt=0)


class ApproverEntry(BaseModel, extra="forbid"):
    approver_id: str = Field(min_length=1)
    role: Literal["primary", "backup"]


class HouseholdConfig(BaseModel, extra="forbid"):
    version: int = Field(gt=0)
    caps: Caps | None = None  # None denies (RQ-26)
    approvers: list[ApproverEntry]
    elder_channel_active: Literal["whatsapp", "telegram"]
    channels_enabled: frozenset[str]
    helper_name: str
    reply_language: str = "hi"
    asr_hint: str = "hi"
    models_local_only: bool = True
    paid_model_cap_inr: Decimal | None = None
    false_alarm_budget_per_approver_week: int = 2
    voice_calls_per_event: int = 6
    radar_audio_max_s: int = 120
    rate_limit_msgs: int = 20
    rate_limit_window_s: int = 600
    approval_mode: Literal["every_order"] = "every_order"
    experimental: bool = False

    def order_within_caps(self, order_inr: Decimal, spent_today_inr: Decimal) -> bool:
        """False when no caps are set (an unset value denies) or either limit would be passed."""
        if self.caps is None:
            return False
        return order_inr <= self.caps.order_inr and spent_today_inr + order_inr <= self.caps.day_inr
