"""LT-15: an empty run is `failed`, a check that did not run is `unavailable` (M12, M21; RQ-36; TQ-42)."""

from __future__ import annotations

import pytest
from conftest import PgDb
from fakes import FakeClock
from worker_helpers import NOW, Rig, Role, add_inbox, audio, open_store, provision, rows

from saathi.ids import ElderId, InboxId
from saathi.store.repos import RequestRepo
from saathi.worker import RunProduced, check_state, end_run, process

H = "h1"


async def make_request(db: PgDb) -> tuple:
    ring, pub = await provision(db)
    await add_inbox(db, ring, pub, H, "m1", text="x")
    return ring, pub


async def test_no_quote_no_sources_no_steps_is_failed(pg_db: PgDb):
    await make_request(pg_db)
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:  # type: ignore[arg-type]
        rid = await RequestRepo.create_first(tx, InboxId("m1"), ElderId("eh1"), None)
        assert await end_run(tx, rid, RunProduced(quotes=0, sources=0, steps=0, effects=0)) is True
        got = await RequestRepo.get(tx, rid)
    assert got is not None and (got["state"], got["state_reason"]) == ("failed", "no_result")  # never `done`


@pytest.mark.parametrize("field", ["quotes", "sources", "steps", "effects"])
async def test_a_run_that_made_anything_is_not_failed(pg_db: PgDb, field: str):
    await make_request(pg_db)
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:  # type: ignore[arg-type]
        rid = await RequestRepo.create_first(tx, InboxId("m1"), ElderId("eh1"), None)
        assert await end_run(tx, rid, RunProduced(**{field: 1})) is False
        got = await RequestRepo.get(tx, rid)
    assert got is not None and got["state"] == "received"


async def test_a_terminal_request_is_never_rewritten(pg_db: PgDb):
    await make_request(pg_db)
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=H) as tx:  # type: ignore[arg-type]
        rid = await RequestRepo.create_first(tx, InboxId("m1"), ElderId("eh1"), None)
        assert await RequestRepo.transition(tx, rid, "received", "router_outcome_c", "handoff", "summary_only")
        assert await end_run(tx, rid, RunProduced()) is False
        got = await RequestRepo.get(tx, rid)
    assert got is not None and got["state"] == "handoff"


def test_unrun_check_is_unavailable():
    assert check_state(False) == "unavailable"
    assert check_state(False, True) == "unavailable"  # a result from a check that did not run is not a pass
    assert check_state(True, True) == "verified"
    assert check_state(True) == "verified"
    assert check_state(True, False) == "invalid"


async def test_a_voice_note_the_radar_could_not_scan_reports_unavailable(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.asr.script.append(RuntimeError)  # any ASR error: the radar had no text
    await add_inbox(pg_db, ring, pub, H, "m2", kind="audio", media_ref=audio(rig))
    clock = FakeClock(NOW)
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        lease = await store.scan.lease_next(clock.now(), 60)
        assert lease is not None
        out = await process(lease, rig.deps(store, ring, clock))
    assert out.checks == {"radar": "unavailable"}
    assert (await rows(pg_db, "SELECT state FROM requests WHERE inbox_id = 'm2'"))[0]["state"] == "handoff"


def test_the_worker_run_that_sent_a_summary_has_an_effect():
    assert RunProduced(effects=2).empty is False and RunProduced().empty is True
