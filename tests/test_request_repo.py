"""LT-49: RequestRepo moves a request only along rows of the machine table (M04; RQ-36, RQ-35)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import PgDb
from fakes import FakeTx
from store_helpers import NOW, open_store, seed_household, seed_inbox

from saathi.ids import ElderId, HouseholdId, InboxId, RequestId
from saathi.states import Machine
from saathi.store import Role
from saathi.store.repos import RequestRepo

H = HouseholdId("h1")
RID = RequestId("r_m1")


async def _create(store) -> RequestId:
    async with store.tx(household=H) as tx:
        return await RequestRepo.create_first(tx, InboxId("m1"), ElderId("eh1"), "grocery")


async def _setup(db: PgDb) -> None:
    await seed_household(db, "h1")
    await seed_inbox(db, "h1", "m1")


async def test_create_first_takes_r01_and_repeats_are_harmless(pg_db: PgDb):
    await _setup(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        assert await _create(store) == RID
        assert await _create(store) == RID
        async with store.tx(household=H) as tx:
            row = await RequestRepo.get(tx, RID)
    assert row is not None
    assert (row["state"], row["channel"], row["kind"], row["inbox_id"], row["intent"]) == (
        "received", "whatsapp", "text", "m1", "grocery",
    )  # fmt: skip
    assert row["slots"] == {}


async def test_pairs_outside_table_raise_row_unchanged(pg_db: PgDb):
    await _setup(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        await _create(store)
        async with store.tx(household=H) as tx:
            # not in the table; a phase 1 row; right pair, wrong target; right row, reason it does not list;
            # right row, reason missing; a reason on a row that lists none
            bad = (
                ("made_up_event", "done", None),
                ("router_outcome_a", "quoting", None),
                ("no_commit_reply_sent", "handoff", None),
                ("router_outcome_c", "handoff", "budget"),
                ("router_outcome_c", "handoff", None),
                ("no_commit_reply_sent", "done", "budget"),
            )
            for event, to, reason in bad:
                with pytest.raises(ValueError):
                    await RequestRepo.transition(tx, RID, "received", event, to, reason)
            row = await RequestRepo.get(tx, RID)
        assert row is not None and row["state"] == "received" and row["state_reason"] is None


async def test_wrong_from_state_changes_nothing(pg_db: PgDb):
    await _setup(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        await _create(store)
        async with store.tx(household=H) as tx:
            assert not await RequestRepo.transition(tx, RID, "new", "lease_creates_request", "received", None)
            assert await RequestRepo.transition(tx, RID, "received", "router_outcome_c", "handoff", "summary_only")
            row = await RequestRepo.get(tx, RID)
        assert row is not None and (row["state"], row["state_reason"]) == ("handoff", "summary_only")


async def test_terminal_state_not_rewritten(pg_db: PgDb):
    await _setup(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        await _create(store)
        async with store.tx(household=H) as tx:
            assert await RequestRepo.transition(tx, RID, "received", "no_commit_reply_sent", "done", None)
            # even a pair that exists in the table for another from state cannot start from a terminal one
            for event, to in (("no_commit_reply_sent", "done"), ("router_outcome_c", "handoff"), ("x", "received")):
                assert not await RequestRepo.transition(tx, RID, "done", event, to, None)
            # a stale caller that still thinks the row is `received` also changes nothing
            assert not await RequestRepo.transition(tx, RID, "received", "router_outcome_c", "handoff", "summary_only")
            row = await RequestRepo.get(tx, RID)
        assert row is not None and row["state"] == "done"


async def test_transition_consults_machine():
    tx = FakeTx(H, NOW)
    seen: list[tuple[str, str]] = []
    real = RequestRepo.MACHINE.lookup

    def spy(from_: str, event: str):
        seen.append((from_, event))
        return real(from_, event)

    with patch.object(RequestRepo.MACHINE, "lookup", spy):
        with pytest.raises(ValueError):
            await RequestRepo.transition(tx, RID, "received", "made_up_event", "done", None)
        assert tx.statements == []  # refused before any statement
        assert await RequestRepo.transition(tx, RID, "received", "no_commit_reply_sent", "done", None)
    assert seen == [("received", "made_up_event"), ("received", "no_commit_reply_sent")]
    assert len(tx.statements) == 1
    sql, args = tx.statements[0]
    assert "WHERE id = $1 AND state = $2" in sql and args[:3] == (RID, "received", "done")


async def test_phase_zero_machine_only():
    """The repo's machine is the phase 0 table: a phase 1 row is not callable."""
    assert isinstance(RequestRepo.MACHINE, Machine) and RequestRepo.MACHINE.phase == 0
    tx = FakeTx(H, NOW)
    with pytest.raises(ValueError):
        await RequestRepo.transition(tx, RID, "received", "router_outcome_b", "clarifying", None)
    assert tx.statements == []


async def test_create_first_needs_the_inbox_row(pg_db: PgDb):
    """No inbox row of this household: nothing is created (the insert selects from it)."""
    await _setup(pg_db)
    async with open_store(pg_db, Role.CORE) as store, store.tx(household=H) as tx:
        await RequestRepo.create_first(tx, InboxId("nope"), ElderId("eh1"), None)
        assert await RequestRepo.get(tx, RequestId("r_nope")) is None


async def test_transition_raises_for_caller_bugs_without_a_statement():
    """F-2: a pair outside the table, a wrong target and an unlisted reason are errors, not a lost race."""
    tx = FakeTx(H, NOW)
    for event, to, reason in (
        ("made_up_event", "done", None),
        ("no_commit_reply_sent", "handoff", None),
        ("router_outcome_c", "handoff", "budget"),
    ):
        with pytest.raises(ValueError):
            await RequestRepo.transition(tx, RID, "received", event, to, reason)
    assert tx.statements == []


async def test_transition_false_for_terminal_from_state_without_a_statement():
    tx = FakeTx(H, NOW)
    assert not await RequestRepo.transition(tx, RID, "done", "no_commit_reply_sent", "done", None)
    assert not await RequestRepo.transition(tx, RID, "done", "made_up_event", "received", None)  # no raise either
    assert tx.statements == []


async def test_transition_false_for_a_lost_race(pg_db: PgDb):
    """The pair is valid but the row is no longer in `from_`: False, no error."""
    await _setup(pg_db)
    async with open_store(pg_db, Role.CORE) as store:
        await _create(store)
        async with store.tx(household=H) as tx:
            assert await RequestRepo.transition(tx, RID, "received", "no_commit_reply_sent", "done", None)
        async with store.tx(household=H) as tx:
            # row is `done`; a caller that read `received` makes a valid pair and loses the compare-and-set
            assert not await RequestRepo.transition(tx, RID, "received", "no_commit_reply_sent", "done", None)
