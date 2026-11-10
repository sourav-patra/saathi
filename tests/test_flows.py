"""LT-47: end-to-end flows. Flow 1.1: a voice note comes in at the edge, the worker handles it, a caregiver gets the
summary and the elder gets an acknowledgement (M07, M12; RQ-79). Real Postgres, fakes for the platforms and models.

The other flows of LT-47 (emergency, heartbeat) belong to the work units that build their parts (WU-17, WU-22).
Synthetic values only.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
from conftest import PgDb
from edge_helpers import ELDER_NUMBER, SENDER_KEY, pg_edge, wa_body, wa_msg, wa_sign, wa_url
from fakes import FakeClock
from worker_helpers import NOW, Rig, Role, audio, open_store, provision, rows

from saathi.ids import HouseholdId, idem_key
from saathi.store.keys import sender_hmac
from saathi.store.repos._util import aad
from saathi.voice.store import ReplyKey
from saathi.worker import Worker, WorkerConfig

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
