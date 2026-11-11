"""LT-17: the state tables (M21; RQ-35, RQ-36, RQ-39).

Data driven from the TRD states part sections 2 to 6 and the SPEC requests state table: every TRD row
must be in `states/tables.py` with the same from and to states and guard, every table row has a case,
every pair absent from a table is refused, and a terminal state is never rewritten.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from saathi.states import ALERT, INBOX, INTENT, REQUEST, TABLES, TERMINAL, TOKEN, Machine, Row

DESIGN = Path(__file__).resolve().parents[1] / "docs/design"
TRD = (DESIGN / "TRD-saathi-states-2026-11-09.md").read_text()
SPEC = (DESIGN / "SPEC-saathi-requests-2026-11-09.md").read_text()

SECTION_OF = {  # TRD heading prefix -> table name
    "## 2. Request machine": "request",
    "## 3. commit_intent machine": "intent",
    "## 4. Alert event machine": "alert",
    "## 5. Inbox machine": "inbox",
    "## 6. Approval token machine": "token",
}
BEFORE_STEP_1 = {"received", "clarifying", "quoting", "awaiting_approval", "approved"}
FROM_ALIASES = {
    "any state before step 1": BEFORE_STEP_1,  # R34
    "any non-terminal": {"open"},  # A10: the only non-terminal alert state
    "open, stage c": {"open"},  # A12, A13: `stage` is a field, not a state
}
PHASE_0_IDS = {f"N{i:02d}" for i in range(1, 11)} | {f"A{i:02d}" for i in range(1, 14)} | {"R01", "R05", "R06"}


def _plain(text: str) -> str:
    return text.replace("`", "").strip()


def _split_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def trd_rows() -> dict[str, dict[str, tuple[set[str], set[str], str, set[str]]]]:
    """table -> row id -> (from states, to states, guard, backticked reasons)."""
    out: dict[str, dict[str, tuple[set[str], set[str], str, set[str]]]] = {n: {} for n in TABLES}
    table: str | None = None
    for line in TRD.splitlines():
        if line.startswith("## "):
            table = next((n for h, n in SECTION_OF.items() if line.startswith(h)), None)
            continue
        m = re.match(r"^\| ([RIANK]\d\d) \|", line)
        if table is None or not m:
            continue
        cells = _split_cells(line)
        id_, from_cell, to_cell, guard = cells[0], cells[1], cells[3], cells[4]
        froms = FROM_ALIASES.get(from_cell) or {f.strip() for f in from_cell.split(",")}
        reasons = set(re.findall(r"`([^`]+)`", to_cell))
        head = _plain(to_cell)
        if head.startswith("unchanged"):
            tos = {"unchanged"}
        elif head == "no row":
            tos = {"no row"}
        elif " or " in head and head.split(" or ")[0] in {r.to for r in TABLES[table]}:
            tos = {t.strip() for t in head.split(" or ")}  # I12
        else:
            tos = {re.split(r"[ ,]", head)[0]}
        out[table][id_] = (froms, tos, _plain(guard), reasons)
    return out


TRD_ROWS = trd_rows()
ALL_ROWS = [(name, row) for name, rows in TABLES.items() for row in rows]


def table_diff(name: str, rows: tuple[Row, ...]) -> list[str]:
    """Differences between a table and the TRD: missing ids, extra ids and wrong from/to pairs."""
    want = TRD_ROWS[name]
    diffs: list[str] = []
    have_ids = {r.id for r in rows}
    diffs += [f"missing {i}" for i in sorted(set(want) - have_ids)]
    diffs += [f"extra {i}" for i in sorted(have_ids - set(want))]
    for id_ in sorted(have_ids & set(want)):
        froms, tos, _, _ = want[id_]
        pairs = {(r.from_, r.to) for r in rows if r.id == id_}
        expect = {(f, f if t == "unchanged" else t) for f in froms for t in tos}
        if pairs != expect:
            diffs.append(f"{id_}: {sorted(pairs)} != {sorted(expect)}")
    return diffs


# --------------------------------------------------------------------------- #
# the tables equal the TRD
# --------------------------------------------------------------------------- #
def test_trd_parse_found_every_table() -> None:
    counts = {n: len(r) for n, r in TRD_ROWS.items()}
    assert counts == {"request": 39, "intent": 12, "alert": 13, "inbox": 10, "token": 6}


@pytest.mark.parametrize("name", list(TABLES))
def test_table_matches_trd(name: str) -> None:
    assert table_diff(name, TABLES[name]) == []


# Rows whose TRD To cell names reasons without backticks, or backticks a word that is not a reason code.
NOT_REASONS = {"unscanned", "confirm2"}  # A11 (event kind) and K02 (token action)
REASONS_BY_HAND = {  # typed from the TRD To cells of R12, R23, R26 and R36
    "R12": {"no_access", "no_find_order", "phone_offline", "macro_failed"},
    "R23": {"paused", "policy_denied", "consent_missing"},
    "R26": {"verify_failed", "paused"},
    "R36": {"channel_down", "window_closed", "budget", "refusal"},
}


@pytest.mark.parametrize("name", list(TABLES))
def test_guard_and_reason_match_trd(name: str) -> None:
    for row in TABLES[name]:
        _, _, guard, backticked = TRD_ROWS[name][row.id]
        # A11 and K02 add the event kind and the token action, which the TRD puts in the To cell
        assert _plain(row.guard).startswith(guard), row.id
        want = REASONS_BY_HAND.get(row.id, backticked - NOT_REASONS)
        assert set(row.reasons) == want, f"{row.id}: {row.reasons} != {sorted(want)}"
        assert len(set(row.reasons)) == len(row.reasons)
        assert all(re.fullmatch(r"[a-z_]+", r) for r in row.reasons), f"{row.id}: a reason is a bare code"


def test_stage_fields_match_the_trd_alert_table() -> None:
    """A01, A11 set stage a; A03 b; A04 c. A12 and A13 apply only to a stage c event (TRD section 4)."""
    by_id = {r.id: r for r in ALERT}
    assert {i: (by_id[i].stage, by_id[i].from_stage) for i in ("A01", "A03", "A04", "A11", "A12", "A13")} == {
        "A01": ("a", None),
        "A03": ("b", None),
        "A04": ("c", None),
        "A11": ("a", None),
        "A12": (None, "c"),
        "A13": ("c", "c"),
    }
    assert all(r.stage is None and r.from_stage is None for rows in TABLES.values() for r in rows if r.id not in by_id)


def test_deleting_one_row_fails_the_table_check() -> None:
    """TQ-04: the check is shown to fail when a row goes missing."""
    for name, rows in TABLES.items():
        victim = rows[len(rows) // 2].id
        cut = tuple(r for r in rows if r.id != victim)
        assert table_diff(name, cut) == [f"missing {victim}"]
    r23 = tuple(replace(r, to="done") if r.id == "R23" else r for r in REQUEST)
    assert any(d.startswith("R23") for d in table_diff("request", r23))


def test_phase_marks() -> None:
    zero = {r.id for rows in TABLES.values() for r in rows if r.phase == 0}
    assert zero == PHASE_0_IDS
    assert {r.phase for rows in TABLES.values() for r in rows} == {0, 1}


# --------------------------------------------------------------------------- #
# one case per row
# --------------------------------------------------------------------------- #
# Typed by hand from the TRD states tables (sections 2 to 6), one line per TRD row: id, from states, event.
# The event names are this build's keys for the TRD's prose. Do not generate this from tables.py.
GOLDEN_TEXT = {
    "request": """
        R01 new lease_creates_request
        R02 received consent_missing
        R03 received router_outcome_a
        R04 received router_outcome_b
        R05 received router_outcome_c
        R06 received no_commit_reply_sent
        R07 clarifying answer_complete
        R08 clarifying answer_incomplete
        R09 clarifying answer_incomplete_final
        R10 quoting facts_in_evaluate_ok
        R11 quoting facts_in_evaluate_deny
        R12 quoting no_access_or_offline_or_macro_failed
        R13 awaiting_approval approve_tap
        R14 awaiting_approval approve_tap_over_cap
        R15 awaiting_approval valid_second_code
        R16 awaiting_approval reject_tap
        R17 awaiting_approval edit_tap
        R18 awaiting_approval timer_fired
        R19 awaiting_approval amendment_targets_this
        R20 received,clarifying,quoting,awaiting_approval request_expiry
        R21 awaiting_approval urgent_refresh_max
        R22 approved step1_commits
        R23 approved step1_denies
        R24 committing intent_committed
        R25 committing intent_rejected_by_service
        R26 committing intent_aborted_no_call
        R27 committing intent_timeout
        R28 committing reaper_no_step3
        R29 timeout,commit_unknown find_order_found
        R30 timeout,commit_unknown find_order_not_found
        R31 timeout,commit_unknown find_order_errors
        R32 received,clarifying,quoting,awaiting_approval,approved cancel_phrase
        R33 committing cancel_phrase
        R34 received,clarifying,quoting,awaiting_approval,approved withdrawal_confirmed
        R35 committing,timeout,commit_unknown withdrawal_confirmed
        R36 received,clarifying,quoting,awaiting_approval rule_blocks
        R37 timeout,commit_unknown late_step3_reference
        R38 timeout,commit_unknown late_step3_refusal
        R39 timeout,commit_unknown find_order_unsupported_or_owner_timeout
    """,
    "intent": """
        I01 new step1_commits
        I02 attempting final_check_fails
        I03 attempting step3_reference
        I04 attempting step3_refusal
        I05 attempting call_time_cap
        I06 attempting reaper_no_step3
        I07 timeout,commit_unknown find_order_found
        I08 timeout,commit_unknown find_order_not_found
        I09 timeout,commit_unknown late_step3_reference
        I10 timeout,commit_unknown late_step3_refusal
        I11 timeout,commit_unknown find_order_unsupported_or_owner_timeout
        I12 timeout,commit_unknown owner_records_placed
        I12 timeout,commit_unknown owner_records_not_placed
    """,
    "alert": """
        A01 new radar_flag_persisted
        A02 open cadence_due
        A03 open t0_plus_2_min
        A04 open t0_plus_4_min
        A05 open stage_bc_signoff_missing
        A06 open acknowledge_tap
        A07 open every_cap_spent
        A08 acknowledged false_alarm_tap
        A09 open t0_plus_10_min
        A10 open restart
        A11 new audio_row_unscanned
        A12 open key_press_1
        A13 open call_no_ack
    """,
    "inbox": """
        N01 new valid_webhook
        N02 new invalid_signature
        N03 new unregistered_sender
        N04 new sender_over_rate_limit
        N05 received worker_leases
        N06 leased lease_expired_or_attempt_failed
        N07 leased effects_sent
        N08 leased kind_unsupported
        N09 leased media_fetch_failed
        N10 leased fifth_failed_attempt
    """,
    "token": """
        K01 new approve_tap
        K02 new valid_second_code
        K03 issued commit_step1
        K04 issued void
        K05 issued clock_passes_expires_at
        K06 spent,voided,expired any_use
    """,
}


def _golden(name: str) -> list[tuple[str, str, str]]:
    out = []
    for line in GOLDEN_TEXT[name].strip().splitlines():
        id_, froms, event = line.split()
        out += [(id_, f, event) for f in froms.split(",")]
    return out


@pytest.mark.parametrize("name", list(TABLES))
def test_event_binding_equals_the_golden_list(name: str) -> None:
    """Which event belongs to which row. Swapping two events (R07 and R09, say) must fail here."""
    have = sorted((r.id, r.from_, r.event) for r in TABLES[name])
    assert have == sorted(_golden(name))


@pytest.mark.parametrize("name", list(TABLES))
def test_every_golden_row_is_found_by_lookup(name: str) -> None:
    for id_, from_, event in _golden(name):
        row = next(r for r in TABLES[name] if r.id == id_ and r.from_ == from_)
        machine = Machine(TABLES[name], phase=row.phase)
        found = machine.lookup(from_, event)
        assert found is not None and found.id == id_, (id_, from_, event)
        assert machine.lookup(from_, event + "_x") is None


@pytest.mark.parametrize("name", list(TABLES))
def test_lookup_outside_table_is_none(name: str) -> None:
    rows = TABLES[name]
    machine = Machine(rows, phase=1)
    states = {r.from_ for r in rows} | {r.to for r in rows}
    events = {r.event for r in rows}
    present = {(r.from_, r.event) for r in rows}
    refused = 0
    for state in states:
        for event in events:
            if (state, event) not in present:
                assert machine.lookup(state, event) is None, (state, event)
                refused += 1
    assert refused > 0
    assert machine.lookup("no_such_state", next(iter(events))) is None
    assert machine.lookup(rows[0].from_, "no_such_event") is None


def test_sample_of_named_illegal_transitions() -> None:
    m = Machine(REQUEST, phase=1)
    assert m.lookup("done", "approve_tap") is None  # a terminal request takes no event
    assert m.lookup("received", "approve_tap") is None  # approval before a card exists
    assert m.lookup("awaiting_approval", "step1_commits") is None  # no commit without an approved tap
    assert m.lookup("quoting", "step1_commits") is None
    # a cancel after step 1 is a handoff, never `cancelled`
    assert m.lookup("committing", "cancel_phrase").to == "handoff"
    assert Machine(INBOX, phase=0).lookup("processed", "worker_leases") is None
    assert Machine(INBOX, phase=0).lookup("dead", "lease_expired_or_attempt_failed") is None
    assert Machine(ALERT, phase=0).lookup("acknowledged", "cadence_due") is None
    assert Machine(INTENT, phase=1).lookup("committed", "find_order_not_found") is None
    assert Machine(TOKEN, phase=1).lookup("spent", "any_use").to == "spent"  # a second spend changes nothing


def test_duplicate_pair_is_refused() -> None:
    dup = (*INBOX, INBOX[0])
    with pytest.raises(ValueError):
        Machine(dup, phase=0)


def test_pairs_are_unique_in_every_table() -> None:
    for rows in TABLES.values():
        Machine(rows, phase=1)  # raises on a duplicate (from, event)


# --------------------------------------------------------------------------- #
# terminal states and the SPEC state table
# --------------------------------------------------------------------------- #
def _spec_states() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """level -> all state names, level -> terminal state names, from SPEC-requests section 3."""
    level_name = {"request": "request", "commit_intent": "intent", "inbox": "inbox", "alert_event": "alert"}
    section = SPEC.split("## 3. State table")[1].split("## 4.")[0]
    states: dict[str, set[str]] = {n: set() for n in level_name.values()}
    terminal: dict[str, set[str]] = {n: set() for n in level_name.values()}
    for line in section.splitlines():
        cells = _split_cells(line)
        if len(cells) != 5 or not cells[0].startswith("`") or cells[1] not in level_name:
            continue
        name = re.match(r"`([^`]+)`", cells[0]).group(1)
        states[level_name[cells[1]]].add(name)
        if cells[4] == "yes":
            terminal[level_name[cells[1]]].add(name)
    return states, terminal


def test_terminal_sets_equal_the_spec() -> None:
    _, spec_terminal = _spec_states()
    for level in ("request", "intent", "inbox", "alert"):
        assert set(TERMINAL[level]) == spec_terminal[level], level


def test_every_table_state_is_a_spec_state() -> None:
    spec_states, _ = _spec_states()
    for level in ("request", "intent", "inbox", "alert"):
        used = {s for r in TABLES[level] for s in (r.from_, r.to)} - {"new", "no row"}
        assert used <= spec_states[level], (level, used - spec_states[level])


def test_terminal_state_has_no_outgoing_row() -> None:
    """A terminal state is never rewritten: no row leaves it for another state.

    The rows that start in one are self loops that change nothing: A08 (the false-alarm flag) and K06 (a
    spent, voided or expired token used again).
    """
    self_loops = set()
    for name, rows in TABLES.items():
        for row in rows:
            if row.from_ in TERMINAL[name]:
                assert row.to == row.from_, f"{row.id} rewrites terminal {row.from_}"
                self_loops.add(row.id)
    assert self_loops == {"A08", "K06"}


def test_terminal_states_are_reachable_ends() -> None:
    for name, terminal in TERMINAL.items():
        targets = {r.to for r in TABLES[name]}
        assert terminal <= targets, (name, terminal - targets)


# --------------------------------------------------------------------------- #
# later phases
# --------------------------------------------------------------------------- #
def test_later_phase_row_not_callable() -> None:
    phase0 = {n: Machine(rows, phase=0) for n, rows in TABLES.items()}
    phase1 = {n: Machine(rows, phase=1) for n, rows in TABLES.items()}
    later = [(n, r) for n, r in ALL_ROWS if r.phase == 1]
    assert later
    for name, row in later:
        assert phase0[name].lookup(row.from_, row.event) is None, row.id
        assert phase1[name].lookup(row.from_, row.event) is row, row.id
    for name, row in ALL_ROWS:
        if row.phase == 0:
            assert phase0[name].lookup(row.from_, row.event) is row


def test_phase_0_request_machine_has_only_the_no_commit_rows() -> None:
    m = Machine(REQUEST, phase=0)
    assert m.lookup("new", "lease_creates_request").to == "received"
    assert m.lookup("received", "router_outcome_c").to == "handoff"
    assert m.lookup("received", "no_commit_reply_sent").to == "done"
    assert m.events_from("received") == {"router_outcome_c", "no_commit_reply_sent"}
    assert m.events_from("awaiting_approval") == frozenset()  # no approval state is reachable
    assert m.lookup("approved", "step1_commits") is None  # nothing commits in Phase 0 (PE rules, CLAUDE.md)
