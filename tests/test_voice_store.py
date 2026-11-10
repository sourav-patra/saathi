"""Voice store, fixed clip job and local speech (LT-22; RQ-16, 17; D-69, D-71). Postgres tests skip without the DSN."""

from __future__ import annotations

import hashlib
from pathlib import Path

import asyncpg
import httpx
import pytest
from conftest import PgDb
from fakes.channels import FakeElderChannel
from fakes.services import FakeAudit
from fakes.voice import FakeAudioCodec, FakeTTS
from store_helpers import NOW, open_store

from saathi.errors import ClipHashMismatch, ClipMissing, ClipUnreviewed, TtsTimeout, TtsUnavailable
from saathi.ids import HouseholdId, IdemKey, PersonRef, VoiceId
from saathi.ports import TTS, VoiceStore
from saathi.replies import REPLY_TEXT, reply_text, send_reply
from saathi.store import Role
from saathi.store.repos import VOICE_SCOPE, VoiceRepo
from saathi.voice.clips import clip_filename, generate_fixed
from saathi.voice.store import PgVoiceStore, ReplyKey
from saathi.voice.tts import LocalTTS

H = HouseholdId("h1")
TO = PersonRef("ref-1")


async def make_clips(db: PgDb, out: Path, **kw):
    async with open_store(db, Role.OWNER) as owner:
        return await generate_fixed(owner, FakeTTS(), FakeAudioCodec(), out, **kw)


async def review(db: PgDb, key: ReplyKey, version: int = 1, language: str = "hi") -> None:
    async with open_store(db, Role.OWNER) as owner, owner.tx(household=VOICE_SCOPE) as tx:
        assert await VoiceRepo.review(
            tx, reply_key=key.value, version=version, language=language,
            reviewer="caregiver", at=NOW, receipt_ref="receipts/clip-review-synthetic",
        )  # fmt: skip


async def send(db: PgDb, out: Path, key: ReplyKey, ch: FakeElderChannel, audit: FakeAudit, alerts: list[str]):
    async def alert(what: str) -> None:
        alerts.append(what)

    async with open_store(db, Role.CORE) as core:
        return await send_reply(
            ch, TO, key, voice=PgVoiceStore(core, out), language="hi", idem=IdemKey("k"), owner_alert=alert,
            audit=audit, household=H, run="run-1",
        )  # fmt: skip


def test_the_store_satisfies_the_ports():
    assert isinstance(PgVoiceStore.__new__(PgVoiceStore), VoiceStore)
    assert isinstance(LocalTTS("http://speech.invalid"), TTS)


async def test_every_reply_key_has_a_clip(pg_db: PgDb, tmp_path: Path):
    made = await make_clips(pg_db, tmp_path)
    assert {c.key for c in made} == {k.value for k in ReplyKey}
    for clip in made:
        assert (tmp_path / clip_filename(ReplyKey(clip.key), 1, "hi")).read_bytes() == clip.data
        assert clip.sha256 == hashlib.sha256(clip.data).hexdigest() and clip.version == 1
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=VOICE_SCOPE) as tx:
        for key in ReplyKey:
            [row] = await VoiceRepo.versions(tx, key.value, "hi")
            assert row.text == reply_text(key, "hi") and row.reviewed is False  # the job never sets a review


async def test_no_clip_text_has_name_or_number(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=VOICE_SCOPE) as tx:
        for key in ReplyKey:
            [row] = await VoiceRepo.versions(tx, key.value, "hi")
            digits = {d for d in "".join(c if c.isdigit() else " " for c in row.text).split()}
            assert digits <= {"112", "108"}, row.text
            assert "@" not in row.text and "http" not in row.text
            assert row.text == REPLY_TEXT[key]["hi"]


async def test_unreviewed_clip_refused(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    async with open_store(pg_db, Role.CORE) as core:
        store = PgVoiceStore(core, tmp_path)
        with pytest.raises(ClipUnreviewed):
            await store.fixed(ReplyKey.EMERGENCY)
        with pytest.raises(ClipMissing):  # an unreviewed clip is also a ClipMissing for callers that handle that
            await store.fixed(ReplyKey.EMERGENCY)
        with pytest.raises(ClipMissing) as none:
            await store.fixed(ReplyKey.EMERGENCY, "en")  # no row at all
        assert not isinstance(none.value, ClipUnreviewed)
    ch, audit, alerts = FakeElderChannel(), FakeAudit(), []
    await send(pg_db, tmp_path, ReplyKey.EMERGENCY, ch, audit, alerts)
    assert [m for m, *_ in ch.sent] == ["send_text"] and alerts == ["clip_error"]
    assert audit.events[0].detail["reason"] == "unreviewed"


async def test_a_clip_needs_reviewer_time_and_receipt(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        with pytest.raises(asyncpg.CheckViolationError):  # reviewer without time is refused by the table
            await su.execute("UPDATE voice_clips SET reviewed_by = 'x' WHERE reply_key = 'ack'")
        await su.execute("UPDATE voice_clips SET reviewed_by = 'x', reviewed_at = now() WHERE reply_key = 'ack'")
    finally:
        await su.close()
    async with open_store(pg_db, Role.CORE) as core:  # no receipt reference: still refused
        with pytest.raises(ClipUnreviewed):
            await PgVoiceStore(core, tmp_path).fixed(ReplyKey.ACK)


async def test_reviewed_clip_loads_and_matches_its_hash(pg_db: PgDb, tmp_path: Path):
    made = await make_clips(pg_db, tmp_path)
    await review(pg_db, ReplyKey.ACK)
    async with open_store(pg_db, Role.CORE) as core:
        clip = await PgVoiceStore(core, tmp_path).fixed(ReplyKey.ACK)
    assert clip.data == next(c.data for c in made if c.key == "ack") and clip.version == 1 and clip.language == "hi"


async def test_hash_mismatch_sends_text_and_alerts_owner(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    await review(pg_db, ReplyKey.EMERGENCY)
    path = tmp_path / clip_filename(ReplyKey.EMERGENCY, 1, "hi")
    path.write_bytes(path.read_bytes() + b"damaged")
    async with open_store(pg_db, Role.CORE) as core:
        with pytest.raises(ClipHashMismatch):
            await PgVoiceStore(core, tmp_path).fixed(ReplyKey.EMERGENCY)
    ch, audit, alerts = FakeElderChannel(), FakeAudit(), []
    await send(pg_db, tmp_path, ReplyKey.EMERGENCY, ch, audit, alerts)
    assert [(m, p) for m, _, p, _ in ch.sent] == [("send_text", reply_text(ReplyKey.EMERGENCY, "hi"))]
    assert alerts == ["clip_error"] and audit.events[0].detail["reason"] == "hash_mismatch"


async def test_missing_file_sends_text(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    await review(pg_db, ReplyKey.SCAM)
    (tmp_path / clip_filename(ReplyKey.SCAM, 1, "hi")).unlink()
    ch, audit, alerts = FakeElderChannel(), FakeAudit(), []
    await send(pg_db, tmp_path, ReplyKey.SCAM, ch, audit, alerts)
    assert [m for m, *_ in ch.sent] == ["send_text"] and alerts == ["clip_error"]
    assert audit.events[0].detail["reason"] == "missing"


async def test_a_reviewed_clip_is_sent_with_its_text(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    await review(pg_db, ReplyKey.EMERGENCY)
    ch, audit, alerts = FakeElderChannel(), FakeAudit(), []
    await send(pg_db, tmp_path, ReplyKey.EMERGENCY, ch, audit, alerts)
    assert [m for m, *_ in ch.sent] == ["send_audio", "send_text"] and alerts == [] and audit.events == []


async def test_newest_reviewed_version_wins_and_an_unreviewed_one_is_never_used(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    await review(pg_db, ReplyKey.ACK)
    async with open_store(pg_db, Role.OWNER) as owner, owner.tx(household=VOICE_SCOPE) as tx:
        data = b"second version"
        (tmp_path / "ack.v2.ogg").write_bytes(data)
        await VoiceRepo.insert(
            tx, reply_key="ack", version=2, language="hi", text="new text",
            sha256=hashlib.sha256(data).hexdigest(), path="ack.v2.ogg",
        )  # fmt: skip
    async with open_store(pg_db, Role.CORE) as core:
        assert (await PgVoiceStore(core, tmp_path).fixed(ReplyKey.ACK)).version == 1  # v2 has no review
    await review(pg_db, ReplyKey.ACK, version=2)
    async with open_store(pg_db, Role.CORE) as core:
        clip = await PgVoiceStore(core, tmp_path).fixed(ReplyKey.ACK)
    assert (clip.version, clip.data) == (2, b"second version")


async def test_a_revoked_review_turns_the_clip_off(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    await review(pg_db, ReplyKey.ACK)
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await su.execute("UPDATE voice_clips SET reviewed_by = NULL, reviewed_at = NULL WHERE reply_key = 'ack'")
    finally:
        await su.close()
    async with open_store(pg_db, Role.CORE) as core:
        with pytest.raises(ClipUnreviewed):
            await PgVoiceStore(core, tmp_path).fixed(ReplyKey.ACK)


async def test_a_stored_path_cannot_leave_the_clip_folder(pg_db: PgDb, tmp_path: Path):
    clips = tmp_path / "clips"
    clips.mkdir()
    (tmp_path / "outside.ogg").write_bytes(b"x")
    async with open_store(pg_db, Role.OWNER) as owner, owner.tx(household=VOICE_SCOPE) as tx:
        await VoiceRepo.insert(
            tx, reply_key="ack", version=1, language="hi", text="t",
            sha256=hashlib.sha256(b"x").hexdigest(), path="../outside.ogg",
        )  # fmt: skip
    await review(pg_db, ReplyKey.ACK)
    async with open_store(pg_db, Role.CORE) as core:
        with pytest.raises(ClipMissing):
            await PgVoiceStore(core, clips).fixed(ReplyKey.ACK)


async def test_the_job_is_idempotent_and_changed_text_makes_a_new_version(pg_db: PgDb, tmp_path: Path):
    first = await make_clips(pg_db, tmp_path)
    assert len(first) == len(ReplyKey)
    assert await make_clips(pg_db, tmp_path) == []  # same text: no new version to review
    su = await asyncpg.connect(pg_db.dsn())
    try:
        await su.execute("UPDATE voice_clips SET text = 'older wording' WHERE reply_key = 'ack'")
    finally:
        await su.close()
    again = await make_clips(pg_db, tmp_path)
    assert [(c.key, c.version) for c in again] == [("ack", 2)]
    assert (tmp_path / "ack.v2.ogg").exists()


async def test_core_cannot_write_clips_and_owner_cannot_change_text(pg_db: PgDb, tmp_path: Path):
    await make_clips(pg_db, tmp_path)
    async with open_store(pg_db, Role.CORE) as core, core.tx(household=VOICE_SCOPE) as tx:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await VoiceRepo.insert(tx, reply_key="ack", version=9, language="hi", text="t", sha256="s", path="p")
    async with open_store(pg_db, Role.OWNER) as owner, owner.tx(household=VOICE_SCOPE) as tx:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await tx.execute("UPDATE voice_clips SET sha256 = 'other' WHERE reply_key = 'ack'")


# ---- LocalTTS: no network, an in-process transport ----


def _tts(handler) -> LocalTTS:
    return LocalTTS("http://speech.invalid/", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_local_tts_posts_text_to_the_local_runtime_only():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"RIFFwav")

    clip = await _tts(handler).synthesize("namaste", voice=VoiceId("v1"), max_seconds=5)
    assert clip.data == b"RIFFwav" and clip.sha256 == hashlib.sha256(b"RIFFwav").hexdigest()
    assert [str(r.url) for r in seen] == ["http://speech.invalid/synthesize"]
    assert b"namaste" in seen[0].content


async def test_speech_runtime_down_sends_text():
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def broken(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(TtsUnavailable):
        await _tts(down).synthesize("t", voice=VoiceId("v"), max_seconds=1)
    with pytest.raises(TtsTimeout):
        await _tts(slow).synthesize("t", voice=VoiceId("v"), max_seconds=1)
    with pytest.raises(TtsUnavailable):
        await _tts(broken).synthesize("t", voice=VoiceId("v"), max_seconds=1)

    from saathi.replies import send_dynamic

    ch = FakeElderChannel()
    r = await send_dynamic(
        ch, TO, "typed text", tts=_tts(down), codec=FakeAudioCodec(), voice=VoiceId("v"), idem=IdemKey("d")
    )
    assert [m for m, *_ in ch.sent] == ["send_text"] and r.key == IdemKey("d:text")
