"""Transition tables of TRD states sections 2 to 6, one `Row` per (id, from state) pair (M21).

A TRD row with several from states (R20, R32, R34, ...) becomes several `Row`s with the same id.
`to` is the state name; "unchanged" rows repeat the from state. `reason` carries the reason or stage
text of the TRD's To cell (`blocked` reasons, `handoff` reasons, `stage b`). `event` is a stable
snake_case name for the TRD's Event cell, unique per from state. Phase 0 runs N01 to N10, A01 to
A13, R01, R05 and R06; every other row is phase 1 and is not callable in Phase 0.
tests/test_states.py compares these rows with the TRD tables, so a drift fails the test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Row:
    id: str
    from_: str
    event: str
    to: str
    guard: str
    phase: int
    reason: str = ""


def _rows(
    id_: str, froms: str, event: str, to: str, guard: str, phase: int, reason: str = ""
) -> tuple[Row, ...]:
    """`froms` is a comma separated list of from states."""
    return tuple(Row(id_, f.strip(), event, to, guard, phase, reason) for f in froms.split(","))


def _loop(id_: str, froms: str, event: str, guard: str, phase: int) -> tuple[Row, ...]:
    return tuple(Row(id_, f.strip(), event, f.strip(), guard, phase) for f in froms.split(","))


_BEFORE_STEP1 = "received, clarifying, quoting, awaiting_approval, approved"
_OPEN_REQUEST = "received, clarifying, quoting, awaiting_approval"
_UNSETTLED = "timeout, commit_unknown"

# --------------------------------------------------------------------------- #
# Request machine (TRD states section 2, RQ-39)
# --------------------------------------------------------------------------- #
REQUEST: tuple[Row, ...] = (
    *_rows("R01", "new", "lease_creates_request", "received", "kind supported; consent row present (PL-05)", 0),
    *_rows("R02", "received", "consent_missing", "blocked", "PL-05", 1, "consent_missing"),
    *_rows("R03", "received", "router_outcome_a", "quoting", "confidence at or above the floor; required slots present", 1),
    *_rows("R04", "received", "router_outcome_b", "clarifying", "a slot missing or confidence between floors; fewer than 2 questions asked", 1),
    *_rows("R05", "received", "router_outcome_c", "handoff", "none", 0, "summary_only"),
    *_rows("R06", "received", "no_commit_reply_sent", "done", "effects sent", 0),
    *_rows("R07", "clarifying", "answer_complete", "quoting", "required slots now present", 1),
    *_rows("R08", "clarifying", "answer_incomplete", "clarifying", "still missing; fewer than 2 questions asked", 1),
    *_rows("R09", "clarifying", "answer_incomplete_final", "handoff", "still missing after 2 questions", 1, "summary_only"),
    *_rows("R10", "quoting", "facts_in_evaluate_ok", "awaiting_approval", "quote hash set; household consented", 1),
    *_rows("R11", "quoting", "facts_in_evaluate_deny", "blocked", "none", 1, "policy_denied"),
    *_rows("R12", "quoting", "no_access_or_offline_or_macro_failed", "handoff", "one of the four", 1, "no_access|no_find_order|phone_offline|macro_failed"),
    *_rows("R13", "awaiting_approval", "approve_tap", "approved", "registered approver; card hash equals current hash; no cap needs a second code", 1),
    *_rows("R14", "awaiting_approval", "approve_tap_over_cap", "awaiting_approval", "registered approver; hash matches", 1),
    *_rows("R15", "awaiting_approval", "valid_second_code", "approved", "approve token unspent and unexpired; step greater than last_step", 1),
    *_rows("R16", "awaiting_approval", "reject_tap", "rejected", "registered approver", 1),
    *_rows("R17", "awaiting_approval", "edit_tap", "quoting", "registered approver", 1),
    *_rows("R18", "awaiting_approval", "timer_fired", "quoting", "timer fired", 1),
    *_rows("R19", "awaiting_approval", "amendment_targets_this", "superseded", "before commit step 1", 1),
    *_rows("R20", _OPEN_REQUEST, "request_expiry", "expired", "class has an expiry (not an urgent ride)", 1),
    *_rows("R21", "awaiting_approval", "urgent_refresh_max", "handoff", "urgent ride", 1, "urgent_refresh_limit"),
    *_rows("R22", "approved", "step1_commits", "committing", "household lock; token verified and spent; pause false; authorize_commit allows on the live re-read; caps checked on the ceiling", 1),
    *_rows("R23", "approved", "step1_denies", "blocked", "the transaction rolled back", 1, "paused|policy_denied|consent_missing"),
    *_rows("R24", "committing", "intent_committed", "done", "none", 1),
    *_rows("R25", "committing", "intent_rejected_by_service", "failed", "none", 1, "service_rejected"),
    *_rows("R26", "committing", "intent_aborted_no_call", "blocked", "none", 1, "verify_failed|paused"),
    *_rows("R27", "committing", "intent_timeout", "timeout", "none", 1),
    *_rows("R28", "committing", "reaper_no_step3", "commit_unknown", "none", 1),
    *_rows("R29", _UNSETTLED, "find_order_found", "done", "none", 1, "reconciled"),
    *_rows("R30", _UNSETTLED, "find_order_not_found", "failed", "none", 1, "reconciled_not_placed"),
    *_rows("R37", _UNSETTLED, "late_step3_reference", "done", "intent not yet reconciled", 1, "late_result"),
    *_rows("R38", _UNSETTLED, "late_step3_refusal", "failed", "intent not yet reconciled", 1, "service_rejected"),
    *_rows("R39", _UNSETTLED, "find_order_unsupported_or_owner_timeout", "handoff", "none", 1, "manual_reconcile"),
    *_loop("R31", _UNSETTLED, "find_order_errors", "before the owner timeout of R39", 1),
    *_rows("R32", _BEFORE_STEP1, "cancel_phrase", "cancelled", "before step 1; never gated by pause, tier or budget", 1),
    *_rows("R33", "committing", "cancel_phrase", "handoff", "after step 1", 1, "cancel_after_commit_start"),
    *_rows("R34", _BEFORE_STEP1, "withdrawal_confirmed", "cancelled", "one confirmation sent", 1, "withdrawal"),
    *_loop("R35", "committing, timeout, commit_unknown", "withdrawal_confirmed", "from step 1 on", 1),
    *_rows("R36", _OPEN_REQUEST, "rule_blocks", "blocked", "the rule holds at that moment", 1, "channel_down|window_closed|budget|refusal"),
)

# --------------------------------------------------------------------------- #
# commit_intent machine (section 3, RQ-80)
# --------------------------------------------------------------------------- #
INTENT: tuple[Row, ...] = (
    *_rows("I01", "new", "step1_commits", "attempting", "R22 guards", 1),
    *_rows("I02", "attempting", "final_check_fails", "aborted_no_call", "no call and no tap happened", 1),
    *_rows("I03", "attempting", "step3_reference", "committed", "none", 1),
    *_rows("I04", "attempting", "step3_refusal", "rejected_by_service", "none", 1),
    *_rows("I05", "attempting", "call_time_cap", "timeout", "none", 1),
    *_rows("I06", "attempting", "reaper_no_step3", "commit_unknown", "none", 1),
    *_rows("I07", _UNSETTLED, "find_order_found", "reconciled_placed", "none", 1),
    *_rows("I08", _UNSETTLED, "find_order_not_found", "reconciled_not_placed", "none", 1),
    *_rows("I09", _UNSETTLED, "late_step3_reference", "committed", "compare on the old state; not yet reconciled", 1),
    *_rows("I10", _UNSETTLED, "late_step3_refusal", "rejected_by_service", "same", 1),
    *_loop("I11", _UNSETTLED, "find_order_unsupported_or_owner_timeout", "none", 1),
    *_rows("I12", _UNSETTLED, "owner_records_placed", "reconciled_placed", "an owner command writes a control row that names the owner", 1),
    *_rows("I12", _UNSETTLED, "owner_records_not_placed", "reconciled_not_placed", "an owner command writes a control row that names the owner", 1),
)

# --------------------------------------------------------------------------- #
# Alert event machine (section 4, RQ-18). `stage` is a field, not a state.
# --------------------------------------------------------------------------- #
ALERT: tuple[Row, ...] = (
    *_rows("A01", "new", "radar_flag_persisted", "open", "emergency or scam", 0, "stage a"),
    *_loop("A02", "open", "cadence_due", "no acknowledgement; stage a under 20 sends per approver", 0),
    *_rows("A03", "open", "t0_plus_2_min", "open", "emergency; no acknowledgement; SO-1 valid; WhatsApp not down", 0, "stage b"),
    *_rows("A04", "open", "t0_plus_4_min", "open", "emergency; no acknowledgement; SO-1 valid", 0, "stage c"),
    *_loop("A05", "open", "stage_bc_signoff_missing", "none", 0),
    *_rows("A06", "open", "acknowledge_tap", "acknowledged", "registered approver; update id not seen before", 0),
    *_rows("A07", "open", "every_cap_spent", "blocked", "none", 0, "no_human_reached"),
    *_loop("A08", "acknowledged", "false_alarm_tap", "registered approver", 0),
    *_loop("A09", "open", "t0_plus_10_min", "emergency", 0),
    *_loop("A10", "open", "restart", "none", 0),
    *_rows("A11", "new", "audio_row_unscanned", "open", "audio rows only", 0, "unscanned, stage a"),
    *_rows("A12", "open", "key_press_1", "acknowledged", "signature valid; call id matches an attempt row of this event; callback not seen before; the digit is 1", 0),
    *_loop("A13", "open", "call_no_ack", "none", 0),
)

# --------------------------------------------------------------------------- #
# Inbox machine (section 5, RQ-79, RQ-81)
# --------------------------------------------------------------------------- #
INBOX: tuple[Row, ...] = (
    *_rows("N01", "new", "valid_webhook", "received", "signature valid; sender registered; body within the size limit; provider_message_id unique", 0),
    *_rows("N02", "new", "invalid_signature", "no row", "none", 0),
    *_rows("N03", "new", "unregistered_sender", "no row", "none", 0),
    *_rows("N04", "new", "sender_over_rate_limit", "received", "20 messages in 10 min", 0),
    *_rows("N05", "received", "worker_leases", "leased", "oldest unprocessed row of its elder, SKIP LOCKED; not_before passed; attempts under 5", 0),
    *_rows("N06", "leased", "lease_expired_or_attempt_failed", "received", "attempts under 5", 0),
    *_rows("N07", "leased", "effects_sent", "processed", "every effect keyed by the inbox id", 0),
    *_rows("N08", "leased", "kind_unsupported", "unsupported", "sticker, contact, location", 0),
    *_rows("N09", "leased", "media_fetch_failed", "media_lost", "none", 0),
    *_rows("N10", "leased", "fifth_failed_attempt", "dead", "none", 0),
)

# --------------------------------------------------------------------------- #
# Approval token machine (section 6, RQ-22). States are read from columns.
# --------------------------------------------------------------------------- #
TOKEN: tuple[Row, ...] = (
    *_rows("K01", "new", "approve_tap", "issued", "registered approver; request in awaiting_approval", 1),
    *_rows("K02", "new", "valid_second_code", "issued", "approve token unspent", 1, "confirm2"),
    *_rows("K03", "issued", "commit_step1", "spent", "request id, quote hash, approver and action match; unexpired; not voided; one UPDATE ... WHERE spent_at IS NULL changes one row", 1),
    *_rows("K04", "issued", "void", "voided", "none", 1),
    *_rows("K05", "issued", "clock_passes_expires_at", "expired", "none", 1),
    *_loop("K06", "spent, voided, expired", "any_use", "none", 1),
)

TABLES: dict[str, tuple[Row, ...]] = {
    "request": REQUEST,
    "intent": INTENT,
    "alert": ALERT,
    "inbox": INBOX,
    "token": TOKEN,
}

# Terminal states of SPEC-requests section 3 (and the token machine). A terminal state is never rewritten:
# no row leaves it for another state. The only row that starts in one is A08, a self loop that sets a flag.
TERMINAL: dict[str, frozenset[str]] = {
    "request": frozenset({"done", "handoff", "cancelled", "superseded", "rejected", "expired", "blocked", "failed"}),
    "intent": frozenset({"committed", "rejected_by_service", "aborted_no_call", "reconciled_placed", "reconciled_not_placed"}),
    "alert": frozenset({"acknowledged", "blocked"}),
    "inbox": frozenset({"processed", "unsupported", "media_lost", "dead"}),
    "token": frozenset({"spent", "voided", "expired"}),
}
