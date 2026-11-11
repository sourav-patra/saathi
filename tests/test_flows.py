"""LT-47: end-to-end flows. Flow 1.1: a voice note comes in at the edge, the worker handles it, a caregiver gets the
summary and the elder gets an acknowledgement (M07, M12; RQ-79). Real Postgres, fakes for the platforms and models.

Flow 1.3, an emergency text: the edge opens the alert in the same transaction as the message, the worker sends the
summary and the safety reply, and the alert service tells both approvers on stage a, then stage b and c (fakes), until
a key press of 1 stops it (WU-17). The heartbeat flow belongs to WU-22. Synthetic values only.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import namedtuple
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest
from alert_helpers import B, P, make_rig
from conftest import PgDb
from edge_helpers import ELDER_NUMBER, SENDER_KEY, pg_edge, wa_body, wa_msg, wa_sign, wa_url
from fakes import FakeClock, FakeEscalator
from worker_helpers import NOW, Rig, Role, audio, open_store, provision, rows

from saathi.config import Settings
from saathi.ids import ApproverId, HouseholdId, idem_key
from saathi.store.keys import KeyRing, sender_hmac
from saathi.store.repos._util import aad
from saathi.voice.store import ReplyKey
from saathi.worker import Worker, WorkerConfig
from saathi.worker.loop import serve

H = "h1"
HEARD = "meri dawai khatam ho gayi hai"  # what the fake speech to text returns; it must not reach the summary
MEDIA_ID = "2002"


async def test_voice_note_to_summary_and_ack(pg_db: PgDb):
    ring, _pub = await provision(pg_db, H, approvers=("ap1", "ap2"))
    su = await asyncpg.connect(pg_db.dsn())
    try:  # the edge hashes senders with its own key: the elder's identity carries that hash
        await su.execute(
            "UPDATE elder_identities SET sender_hmac = $1 WHERE household_id = $2 AND channel = 'whatsapp'",
            sender_hmac(SENDER_KEY, "whatsapp", ELDER_NUMBER),
            H,
        )
    finally:
        await su.close()

    # 1. At the edge: the voice note is signed, persisted and answered with 200 before anything else happens.
    clock = FakeClock(NOW)
    async with pg_edge(pg_db, clock=clock) as edge:
        note = wa_msg("audio", mid="wamid.flow11", audio={"id": MEDIA_ID, "mime_type": "audio/ogg"})
        body = wa_body(note)
        assert (await edge.post(wa_url(), content=body, headers=wa_sign(body))).status_code == 200
        assert (await edge.post(wa_url(), content=body, headers=wa_sign(body))).status_code == 200  # a repeat

    [row] = await rows(pg_db, "SELECT id, kind, state, acked_at, radar_at, media_ref FROM inbox")
    inbox = str(row["id"])
    assert (row["kind"], row["state"], row["media_ref"]) == ("audio", "received", MEDIA_ID)
    assert row["acked_at"] is None and row["radar_at"] is None  # nothing has run on it yet
    assert await rows(pg_db, "SELECT 1 FROM requests") == []
    assert await rows(pg_db, "SELECT 1 FROM raw_text") == []  # audio carries no text until the worker hears it

    # 2. The worker: acknowledgement first, then the summary and the reply, then the row is settled.
    rig = Rig()
    rig.asr.text = HEARD
    for reply in (ReplyKey.ACK, ReplyKey.TOLD_CAREGIVER):  # the signed fixed clips of the two replies
        rig.voice.add(reply, b"synthetic-clip-" + reply.value.encode(), "hi")
    audio(rig, MEDIA_ID, data=b"synthetic-opus")
    await clock.advance(5)  # five seconds after it arrived
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        worker = Worker(store, rig.deps(store, ring, clock, WorkerConfig()), clock)
        assert await worker.run_once() is True
        assert await worker.run_once() is False  # quiescent: nothing is left

        # The transcript is stored sealed and opens to what the speech to text returned.
        async with store.tx(household=HouseholdId(H)) as tx:
            [t] = await tx.fetch(
                "SELECT body_sealed, key_id FROM raw_text WHERE inbox_id = $1 AND part = 'transcript'", inbox
            )
            heard = await ring.open(tx, bytes(t["body_sealed"]), str(t["key_id"]), aad=aad(H, inbox, "transcript"))
    assert heard.decode() == HEARD

    [done] = await rows(pg_db, "SELECT state, acked_at, received_at, radar_at FROM inbox WHERE id = $1", inbox)
    assert done["state"] == "processed" and done["radar_at"] is not None
    assert done["acked_at"] - done["received_at"] <= timedelta(seconds=10)  # RQ-74

    [req] = await rows(pg_db, "SELECT id, state, state_reason FROM requests")
    assert (req["id"], req["state"], req["state_reason"]) == (f"r_{inbox}", "handoff", "summary_only")
    assert await rows(pg_db, "SELECT 1 FROM alert_events") == []  # an ordinary voice note raises no alert

    # 3. Caregivers: each approver, primary first, gets one summary card and the original audio.
    cards = [(to, key, payload) for m, to, payload, key in rig.caregiver.sent if m == "send_card"]
    assert [to for to, _k, _c in cards] == ["ap1h1", "ap2h1"]
    assert [k for _to, k, _c in cards] == [idem_key(inbox, "summary", "ap1h1"), idem_key(inbox, "summary", "ap2h1")]
    for _to, _k, card in cards:
        assert card.buttons == []  # a summary asks for nothing
        assert "Transcribed: yes" in card.lines
        assert not any(word in line for line in card.lines for word in HEARD.split())  # slots, never the words (RQ-97)
    assert [m for m, *_ in rig.caregiver.sent].count("send_audio") == 2  # consent is on record for the audio

    # 4. The elder: the acknowledgement and then the reply, each as a clip and its text, each key sent once.
    sent = [(m, str(to), str(key)) for m, to, _p, key in rig.elder.sent]
    assert sent == [
        ("send_audio", "ref-h1", f"{inbox}:ack:audio"),
        ("send_text", "ref-h1", f"{inbox}:ack:text"),
        ("send_audio", "ref-h1", f"{inbox}:reply:audio"),
        ("send_text", "ref-h1", f"{inbox}:reply:text"),
    ]
    assert rig.owner_alerts == []  # no clip error, no dead row


EMERGENCY_TEXT = "mujhe chest pain hai"


async def test_emergency_to_stage_a_and_fake_b_c(pg_db: PgDb):
    rig = await make_rig(pg_db)  # two approvers, SO-1 valid, the stored emergency clip; the clock is `rig.clock`
    clock = rig.clock
    su = await asyncpg.connect(pg_db.dsn())
    try:  # the edge hashes senders with its own key
        await su.execute(
            "UPDATE elder_identities SET sender_hmac = $1 WHERE household_id = $2 AND channel = 'whatsapp'",
            sender_hmac(SENDER_KEY, "whatsapp", ELDER_NUMBER),
            H,
        )
    finally:
        await su.close()

    # 1. The edge: the text is scanned, and the alert is opened in the same transaction as the message.
    async with pg_edge(pg_db, clock=clock) as edge:
        body = wa_body(wa_msg(mid="wamid.flow13", text={"body": EMERGENCY_TEXT}))
        assert (await edge.post(wa_url(), content=body, headers=wa_sign(body))).status_code == 200
    [alert] = await rows(pg_db, "SELECT id, kind, origin, state, stage, t0, next_due_at FROM alert_events")
    assert (alert["kind"], alert["origin"], alert["state"], alert["stage"]) == ("emergency", "radar_edge", "open", "a")
    assert alert["next_due_at"] == alert["t0"]  # due at once
    event = alert["id"]
    [inbox] = await rows(pg_db, "SELECT id FROM inbox")
    inbox = str(inbox["id"])

    # 2. The worker: summary cards to the humans and the safety reply to the elder, no alert of its own (one exists).
    worker_rig = Rig()
    worker_rig.elder, worker_rig.caregiver, worker_rig.voice = rig.elder, rig.caregiver, rig.voice
    for reply in (ReplyKey.ACK, ReplyKey.TOLD_CAREGIVER):
        rig.voice.add(reply, b"synthetic-clip-" + reply.value.encode(), "hi")
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        worker = Worker(store, worker_rig.deps(store, rig.ring, clock, WorkerConfig()), clock)
        assert await worker.run_once() is True
        assert await worker.run_once() is False
        assert len(await rows(pg_db, "SELECT 1 FROM alert_events")) == 1  # the worker's open was a no-op repeat
        summaries = [k for m, _to, _p, k in rig.caregiver.sent if m == "send_card"]
        assert summaries == [idem_key(inbox, "summary", P), idem_key(inbox, "summary", B)]

        # 3. The alert service. t0: stage a to both approvers; the elder's clip was already sent by the worker.
        svc = rig.service(store)
        await svc.tick()
        alert_cards = [(str(to), str(k)) for to, _c, k in rig.cards() if str(k).startswith("alert:")]
        assert alert_cards == [(P, f"alert:{event}:a:{P}:1"), (B, f"alert:{event}:a:{B}:1")]
        for _to, card, _k in [c for c in rig.cards() if str(c[2]).startswith("alert:")]:
            assert [b.text for b in card.buttons] == ["Acknowledge", "False alarm"]  # type: ignore[attr-defined]
            assert EMERGENCY_TEXT not in " ".join(card.lines)  # type: ignore[attr-defined]

        # the elder heard the emergency clip and text once: the worker's keys, which the alert service shared
        reply_keys = [str(k) for _m, _to, _p, k in rig.elder.sent if str(k).startswith(f"{inbox}:reply")]
        assert reply_keys == [f"{inbox}:reply:audio", f"{inbox}:reply:text"]
        texts = [p for m, _to, p, _k in rig.elder.sent if m == "send_text" and str(_k).endswith("reply:text")]
        assert "112" in texts[0] and "108" in texts[0]  # type: ignore[operator]

        # stage b at 120 s and stage c at 240 s, against the fake providers
        await rig.run(svc, 125, every=5)
        assert [(str(a), str(k)) for _e, a, k in rig.stage_b.notified] == [
            (P, f"alert:{event}:b:{P}:1"), (B, f"alert:{event}:b:{B}:1"),
        ]  # fmt: skip
        assert rig.stage_c.notified == []
        await rig.run(svc, 120, every=5)
        assert [str(a) for _e, a, _k in rig.stage_c.notified] == [P]  # the primary's call at 240 s

        # 4. The primary presses 1 on the call: the event ends acknowledged and nothing more goes out.
        call = rig.stage_c.call_id_of(rig.stage_c.keys[0])
        assert await svc.on_voice_callback(HouseholdId(H), event, FakeEscalator.callback(call)) is True
        done = (await rows(pg_db, "SELECT state, acked_by, followup_due_at - acked_at AS gap FROM alert_events"))[0]
        assert (done["state"], done["acked_by"], done["gap"]) == ("acknowledged", P, timedelta(seconds=900))
        before = (len(rig.caregiver.sent), len(rig.stage_b.notified), len(rig.stage_c.notified), len(rig.elder.sent))
        await rig.run(svc, 3000, every=100)
        assert (len(rig.caregiver.sent), len(rig.stage_b.notified), len(rig.stage_c.notified),
                len(rig.elder.sent)) == (before[0] + 1, *before[1:])  # fmt: skip
        follow = [(str(to), str(k)) for m, to, _p, k in rig.caregiver.sent if m == "send_text"]
        assert follow == [(P, f"alert:{event}:followup")]  # the one new message: the 15 minute follow-up
        assert [e.actor for e in rig.audit.events if e.event == "ack_voice"] == [f"approver:{ApproverId(P)}"]


# --- flow 1.3: the heartbeat (RQ-104) ----------------------------------------------------------------------

Disk = namedtuple("Disk", "total used free")


async def _health_is(edge, code: int, *, within_s: float = 10.0):  # type: ignore[no-untyped-def]
    """Ask the public route until it answers `code`: the worker beats on real tasks, the clock is moved by hand."""
    deadline = asyncio.get_running_loop().time() + within_s
    while True:
        r = await edge.get("/health")
        if r.status_code == code or asyncio.get_running_loop().time() > deadline:
            return r
        await asyncio.sleep(0.05)


async def test_heartbeat_ok_then_503(pg_db: PgDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    free = {"pct": 90}  # free disk in percent, moved by the test
    monkeypatch.setattr("saathi.health.shutil.disk_usage", lambda _p: Disk(100, 100 - free["pct"], free["pct"]))
    clock = FakeClock(NOW)
    rig = Rig()
    cfg = Settings(_env_file=None, approval_secret="s" * 32, media_dir=str(tmp_path))  # type: ignore[arg-type]

    async with open_store(pg_db, Role.CORE, clock=clock) as store, pg_edge(pg_db, clock=clock) as edge:
        # 1. Nothing has beaten yet: the route answers 503 with no body.
        r = await edge.get("/health")
        assert (r.status_code, r.content) == (503, b"")

        # 2. The worker starts through `serve` (secrets, host table, then the loop and the core beat): 200 `ok` only.
        worker = asyncio.create_task(serve(cfg, lambda _s: rig.deps(store, KeyRing(kek=None), clock)))
        try:
            r = await _health_is(edge, 200)
            assert (r.status_code, r.text) == (200, "ok")
            assert set(r.headers) <= {"content-type", "content-length"}  # no name, count or version

            # 3. A full disk turns the route to 503 at the next beat (10 s later), and it recovers when space returns.
            free["pct"] = 5
            await clock.advance(10)
            r = await _health_is(edge, 503)
            assert (r.status_code, r.content) == (503, b"")
            free["pct"] = 90
            await clock.advance(10)
            assert (await _health_is(edge, 200)).text == "ok"
        finally:  # 4. The core stops: the last beat goes stale and the route answers 503 within 30 s.
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        await clock.advance(20)
        assert (await edge.get("/health")).status_code == 200  # a beat 20 s old is still fresh
        await clock.advance(11)
        r = await edge.get("/health")
        assert (r.status_code, r.content) == (503, b"")

    # The skew probe saw the test clock far from the database clock and told the owner once, not at every beat.
    assert rig.owner_alerts.count("probe_skew_fail") == 1
    assert [a for a in rig.owner_alerts if not a.startswith("probe_")] == []
