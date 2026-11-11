"""LT-14: the request worker, steps 7 to 13 of flow 1.1 (M12; RQ-79, 16, 36, 97). Synthetic values only.

The WU-20 review carries live here too: emergency makes no model call and raises an alert, a scam raises one with the
OTP warning, a transcript is redacted before the gateway, and a normal request sends a caregiver summary.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from conftest import PgDb
from fakes import FakeClock
from worker_helpers import CANARY_PAN, NOW, Rig, Role, add_inbox, audio, grant, open_store, provision, rows

from saathi.config import ConfigError, Settings
from saathi.domain import Card, Intent, Sensitivity
from saathi.errors import AsrUnavailable, AudioUndecodable, ChannelDown, DecodeTimeout, SendRejected
from saathi.guards import radar
from saathi.ids import ApproverId, IdemKey, PersonRef
from saathi.understand.router import RouterOutput
from saathi.worker import Worker, WorkerConfig, process, steps
from saathi.worker.loop import serve

H = "h1"
PERSON = PersonRef("ref-h1")


@asynccontextmanager
async def harness(db: PgDb, rig: Rig, keys: Any, cfg: WorkerConfig | None = None):
    clock = FakeClock(NOW)
    async with open_store(db, Role.CORE, clock=clock) as store:
        yield store, rig.deps(store, keys, clock, cfg), clock


async def one(store: Any, clock: Any):
    lease = await store.scan.lease_next(clock.now(), 60)
    assert lease is not None
    return lease


def texts(rig: Rig) -> list[str]:
    return [p for m, _to, p, _k in rig.elder.sent if m == "send_text"]  # type: ignore[misc]


async def inbox_state(db: PgDb, inbox: str) -> str:
    return (await rows(db, "SELECT state FROM inbox WHERE id = $1", inbox))[0]["state"]


async def request_of(db: PgDb, inbox: str) -> Any:
    got = await rows(db, "SELECT state, state_reason FROM requests WHERE inbox_id = $1", inbox)
    return got[0] if got else None


async def test_ends_handoff_summary_only(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="aaj mausam kaisa hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert (out.inbox_state, out.request_state) == ("processed", "handoff")
    req = await request_of(pg_db, "m1")
    assert (req["state"], req["state_reason"]) == ("handoff", "summary_only")
    assert await inbox_state(pg_db, "m1") == "processed"
    assert out.checks["radar"] == "verified"


async def test_normal_request_sends_a_caregiver_summary_without_the_words(pg_db: PgDb):
    ring, pub = await provision(pg_db, approvers=("ap1", "ap2"))
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text=f"auto chahiye, mera pan {CANARY_PAN} hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        await process(await one(store, clock), deps)
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card", "send_card"]  # primary first, then backup
    assert [to for _m, to, *_ in rig.caregiver.sent] == ["ap1h1", "ap2h1"]
    card = next(iter(rig.caregiver.cards.values()))
    flat = json.dumps(card.model_dump())
    assert "auto" not in flat and CANARY_PAN not in flat and card.buttons == []  # enums and counts only (RQ-97)
    assert len(texts(rig)) == 1 and "112" not in texts(rig)[0]  # the plain reply, no emergency numbers
    assert [k for *_x, k in rig.elder.sent][-1] == IdemKey("m1:reply:text")


async def test_no_model_called(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="auto chahiye")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        await process(await one(store, clock), deps)
    assert rig.gateway.calls == []  # Phase 0: the step never calls a model


async def test_router_on_is_personal_and_the_transcript_is_redacted_first(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.gateway.will_return(RouterOutput(intent=Intent.RIDE, confidence=0.9, summary_en="x"))
    await add_inbox(pg_db, ring, pub, H, "m1", text=f"auto chahiye, mera pan {CANARY_PAN} hai")
    async with harness(pg_db, rig, ring, WorkerConfig(understand=True)) as (store, deps, clock):
        await process(await one(store, clock), deps)
    [req] = rig.gateway.requests
    assert req.sensitivity is Sensitivity.PERSONAL
    body = json.dumps([m.model_dump() for m in req.messages])
    assert CANARY_PAN not in body and "<PAN>" in body  # the canary is absent from the outgoing body
    card = next(iter(rig.caregiver.cards.values()))
    assert "Topic: ride" in card.lines and CANARY_PAN not in json.dumps(card.model_dump())


async def test_asr_text_hit_opens_alert_worker(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.asr.script.append("mujhe seene mein dard ho raha hai")
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with harness(pg_db, rig, ring, WorkerConfig(understand=True)) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert out.emergency and not out.scam
    [alert] = await rows(pg_db, "SELECT kind, origin, rule_ids, state FROM alert_events WHERE inbox_id = 'm1'")
    assert (alert["kind"], alert["origin"], alert["state"]) == ("emergency", "radar_worker", "open")
    assert alert["rule_ids"] and all(r.startswith("RD-") for r in alert["rule_ids"])
    [part] = await rows(pg_db, "SELECT part FROM raw_text WHERE inbox_id = 'm1' AND part = 'transcript'")
    assert part["part"] == "transcript"
    assert rig.gateway.calls == []  # emergency: zero model calls, even with the router switched on
    assert "112" in texts(rig)[-1] and "108" in texts(rig)[-1]  # the reply names the emergency numbers
    assert rig.codec.calls_of("to_wav16k")[0][0][0].data == b"synthetic-opus"
    wav_in = rig.asr.calls_of("transcribe")[0][0][0]
    assert wav_in.mime == "audio/wav"  # ASR is given what the codec returned, as 16 kHz WAV


async def test_scam_opens_a_scam_alert_with_the_otp_warning(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="kisi ne otp bhejne ko kaha hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert out.scam and not out.emergency
    [alert] = await rows(pg_db, "SELECT kind, origin FROM alert_events WHERE inbox_id = 'm1'")
    assert (alert["kind"], alert["origin"]) == ("scam", "radar_worker")
    assert "OTP" in texts(rig)[-1] and rig.gateway.calls == []
    card = next(iter(rig.caregiver.cards.values()))
    assert card.title == "Possible scam" and any("OTP" in line for line in card.lines)


async def test_edge_alert_is_not_opened_twice(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="mujhe chest pain hai")
    await rows(
        pg_db,
        "INSERT INTO alert_events (id, household_id, elder_id, inbox_id, kind, origin, t0, next_due_at)"
        " VALUES ('al_m1_emergency', 'h1', 'eh1', 'm1', 'emergency', 'radar_edge', now(), now()) RETURNING id",
    )
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        await process(await one(store, clock), deps)
    assert len(await rows(pg_db, "SELECT 1 FROM alert_events WHERE inbox_id = 'm1'")) == 1


@pytest.mark.parametrize("error", [AsrUnavailable, AudioUndecodable, DecodeTimeout])
async def test_asr_failure_forwards_audio_with_template(pg_db: PgDb, error: type[Exception]):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.asr.script.append(error)
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert out.inbox_state == "processed" and out.checks["radar"] == "unavailable"  # not scanned, never "verified"
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card", "send_audio"]  # the audio is forwarded
    card = next(iter(rig.caregiver.cards.values()))
    assert any("could not be turned into text" in line for line in card.lines)
    assert any("original audio is attached" in line for line in card.lines)
    assert await rows(pg_db, "SELECT 1 FROM raw_text WHERE inbox_id = 'm1' AND part = 'transcript'") == []
    assert texts(rig)  # the templated reply to the elder still goes
    assert (await request_of(pg_db, "m1"))["state"] == "handoff"


async def test_codec_failure_forwards_audio_too(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.codec.fail("to_wav16k", AudioUndecodable)
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        await process(await one(store, clock), deps)
    assert rig.asr.calls == [] and [m for m, *_ in rig.caregiver.sent] == ["send_card", "send_audio"]


async def test_audio_without_the_audio_consent_is_not_forwarded(pg_db: PgDb):
    ring, pub = await provision(pg_db, consents=False)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await grant(su, H, "transcription", "local_models")  # transcription yes, alert audio no
    finally:
        await su.close()
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        await process(await one(store, clock), deps)
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"]


async def test_unsupported_kind_summary(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", kind="unsupported")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert out.inbox_state == "unsupported" and out.request_state is None
    assert await inbox_state(pg_db, "m1") == "unsupported"
    assert await request_of(pg_db, "m1") is None  # R01 needs a supported kind
    card = next(iter(rig.caregiver.cards.values()))
    assert any("cannot read" in line for line in card.lines)


async def test_consent_missing_blocks(pg_db: PgDb):
    ring, pub = await provision(pg_db, consents=False)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    req = await request_of(pg_db, "m1")
    assert (req["state"], req["state_reason"]) == ("blocked", "consent_missing")  # R02
    assert rig.asr.calls == [] and rig.codec.calls == [] and rig.media.blobs == {}  # nothing was processed
    assert out.inbox_state == "processed" and rig.caregiver.sent  # the summary path still tells the humans
    [(args, kwargs)] = [c for c in rig.audit.calls_of("append") if c[0][0].event == "consent_denied"]
    assert args[0].detail["use"] == "transcription" and kwargs["tx"] is None  # audited outside any transaction


async def test_consent_missing_text_still_goes_through_the_radar(pg_db: PgDb):
    ring, pub = await provision(pg_db, consents=False)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="gir gaya hoon, bachao")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert out.emergency  # a missing consent never silences a human alert
    assert len(await rows(pg_db, "SELECT 1 FROM alert_events WHERE kind = 'emergency'")) == 1


async def test_media_lost_inside_the_url_lifetime(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()  # nothing in rig.elder.media: the fetch finds the link expired
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref="9999")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert out.inbox_state == "media_lost" and await inbox_state(pg_db, "m1") == "media_lost"  # N09
    card = next(iter(rig.caregiver.cards.values()))
    assert any("could not be fetched" in line for line in card.lines)
    assert rig.asr.calls == []


async def test_not_processed_before_effects(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.caregiver.fail("send_card", ChannelDown)
    await add_inbox(pg_db, ring, pub, H, "m1", text="aaj mausam kaisa hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        with pytest.raises(ChannelDown):
            await process(await one(store, clock), deps)
        assert await inbox_state(pg_db, "m1") == "leased"  # never `processed` before its effects were sent
        assert texts(rig)  # the elder reply was still sent: the humans' error does not silence her
        await clock.advance(61)  # the lease runs out and the row is tried again
        out = await process(await one(store, clock), deps)
    assert out.inbox_state == "processed"
    assert len(rig.caregiver.sent) == 1  # one summary in the end
    assert len([1 for m, *_ in rig.elder.sent if m == "send_text"]) == 1  # the repeat key sent nothing twice


async def test_duplicate_key_counts_as_delivered(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="aaj mausam kaisa hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        await rig.elder.send_text(PERSON, "earlier", key=IdemKey("m1:reply:text"))  # sent before a crash
        await rig.caregiver.send_card(ApproverId("ap1h1"), Card(title="t"), key=IdemKey("m1:summary:ap1h1"))
        out = await process(await one(store, clock), deps)
    assert out.inbox_state == "processed"  # SendRejected("duplicate_key") did not fail the step


async def test_other_send_errors_still_fail_the_step(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.elder.fail("send_text", SendRejected("bad_number"))
    await add_inbox(pg_db, ring, pub, H, "m1", text="aaj mausam kaisa hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        with pytest.raises(SendRejected):
            await process(await one(store, clock), deps)
    assert await inbox_state(pg_db, "m1") == "leased"


class WindowChannel:
    """Records `note_inbound` calls, as the WhatsApp channel does."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.noted: list[tuple[PersonRef, Any]] = []

    def note_inbound(self, person: PersonRef, at: Any) -> None:
        self.noted.append((person, at))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


async def test_worker_passes_the_last_inbox_received_at_to_note_inbound(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    window = WindowChannel(rig.elder)
    later = NOW + timedelta(minutes=5)
    await add_inbox(pg_db, ring, pub, H, "m1", text="pehla", received_at=NOW)
    await add_inbox(pg_db, ring, pub, H, "m2", text="doosra", received_at=later)
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        deps.elder = window  # type: ignore[assignment]
        deps.__post_init__()  # wrap again, as construction does
        await process(await one(store, clock), deps)
    assert window.noted and all(p == PERSON and at == later for p, at in window.noted)


async def test_run_once_acks_then_processes(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="aaj mausam kaisa hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        worker = Worker(store, deps, clock)
        assert await worker.run_once() is True
        assert await worker.run_once() is False  # nothing left
    keys = [k for *_x, k in rig.elder.sent]
    assert keys == [IdemKey("m1:ack:text"), IdemKey("m1:reply:text")]
    assert (await rows(pg_db, "SELECT acked_at, state FROM inbox WHERE id = 'm1'"))[0]["acked_at"] is not None


async def test_a_step_that_fails_leaves_the_row_for_the_next_try(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.caregiver.fail("send_card", ChannelDown)
    await add_inbox(pg_db, ring, pub, H, "m1", text="aaj mausam kaisa hai")
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        worker = Worker(store, deps, clock)
        assert await worker.run_once() is True  # failed, not raised out of the loop
        assert await inbox_state(pg_db, "m1") == "leased"
        await clock.advance(61)
        assert await worker.run_once() is True
    assert await inbox_state(pg_db, "m1") == "processed"


async def test_start_up_calls_require_secrets_before_anything_is_built():
    built: list[Any] = []

    def build(_s: Settings) -> Any:
        built.append(1)

    with pytest.raises(ConfigError):
        await serve(Settings(_env_file=None, approval_secret="x" * 31), build)  # type: ignore[arg-type]
    assert built == []


def test_worker_does_not_import_a_platform_or_the_legacy_types():
    from pathlib import Path

    for path in (Path(__file__).resolve().parent.parent / "src/saathi/worker").glob("*.py"):
        text = path.read_text()
        assert "saathi.legacy" not in text and "saathi.channels" not in text, path.name


def test_the_canary_is_a_pan_the_radar_does_not_flag():
    assert not radar.scan(f"auto chahiye, mera pan {CANARY_PAN} hai").triggered


# ---- review fixes (F1 to F4) ----


def _crash(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("scan crashed")


async def test_a_crashing_radar_on_an_emergency_text_still_alerts_the_owner_and_replies(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch
):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="mujhe chest pain hai")
    monkeypatch.setattr(radar, "scan", _crash)
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        out = await process(await one(store, clock), deps)
    assert out.inbox_state == "processed" and out.checks["radar"] == "unavailable"
    [alert] = await rows(pg_db, "SELECT kind, origin FROM alert_events WHERE inbox_id = 'm1'")
    assert (alert["kind"], alert["origin"]) == ("unscanned", "unscanned_overdue")  # a human hears of it
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"] and len(texts(rig)) == 1
    assert (await rows(pg_db, "SELECT radar_at FROM inbox WHERE id = 'm1'"))[0]["radar_at"] is None  # not scanned


async def test_a_dead_text_row_with_no_radar_run_opens_an_unscanned_alert(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="mujhe chest pain hai")
    await rows(
        pg_db,
        "UPDATE inbox SET state = 'leased', attempts = 5, lease_until = $1 WHERE id = 'm1' RETURNING id",
        NOW - timedelta(seconds=5),
    )
    await add_inbox(pg_db, ring, pub, H, "m2", text="aaj mausam kaisa hai")  # one the radar did run on
    await rows(
        pg_db,
        "UPDATE inbox SET state = 'leased', attempts = 5, lease_until = $1, radar_at = $2 WHERE id = 'm2' RETURNING id",
        NOW - timedelta(seconds=5),
        NOW - timedelta(seconds=9),
    )
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        assert await Worker(store, deps, clock).run_once(ack=False) is False
    assert [e.event for e in rig.audit.events if e.event == "inbox_dead"] == ["inbox_dead", "inbox_dead"]
    got = await rows(pg_db, "SELECT inbox_id, kind FROM alert_events")
    assert [(r["inbox_id"], r["kind"]) for r in got] == [("m1", "unscanned")]


async def _hang(*_a: Any, **_k: Any) -> Any:
    await asyncio.sleep(3600)


async def test_a_hanging_caregiver_send_does_not_hold_the_elder_reply(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="mujhe chest pain hai")
    monkeypatch.setattr(rig.caregiver, "send_card", _hang)
    monkeypatch.setattr(steps, "CAREGIVER_SEND_TIMEOUT_S", 0.05)
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        with pytest.raises(TimeoutError):  # raised afterwards so the row is tried again
            await process(await one(store, clock), deps)
    assert "112" in texts(rig)[-1]  # the emergency reply went out
    assert await inbox_state(pg_db, "m1") == "leased"


async def test_a_cancel_during_a_caregiver_send_still_sends_the_elder_reply(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch
):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", text="mujhe chest pain hai")
    entered = asyncio.Event()

    async def hang_once(*_a: Any, **_k: Any) -> Any:
        entered.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(rig.caregiver, "send_card", hang_once)
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        task = asyncio.create_task(process(await one(store, clock), deps))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(texts(rig)) == 1 and "112" in texts(rig)[0]


async def test_a_missing_alert_audio_consent_is_audited_for_an_audio_alert(pg_db: PgDb):
    ring, pub = await provision(pg_db, consents=False)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await grant(su, H, "transcription", "local_models")
    finally:
        await su.close()
    rig = Rig()
    await add_inbox(pg_db, ring, pub, H, "m1", kind="audio", media_ref=audio(rig))
    async with harness(pg_db, rig, ring) as (store, deps, clock):
        await process(await one(store, clock), deps)
    denied = [e for e in rig.audit.events if e.event == "consent_denied"]
    assert [e.detail["use"] for e in denied] == ["alert_audio"]
    assert [m for m, *_ in rig.caregiver.sent] == ["send_card"]


async def test_probe_alerts_fire_on_a_change_to_not_ok_and_on_recovery_only():
    from saathi.health import ProbeResult, ProbeStatus
    from saathi.worker.loop import _probe_alerts

    sent: list[str] = []

    async def alert(what: str) -> None:
        sent.append(what)

    on = _probe_alerts(alert)
    for status in ("ok", "ok", "fail", "fail", "ok", "ok"):
        await on(ProbeResult("disk", ProbeStatus(status)))
    assert sent == ["probe_disk_fail", "probe_disk_ok"]  # the first ok is silent; a recovery is told once
