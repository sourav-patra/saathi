"""LT-16: ASR and the audio codec ports, the media store and summaries (M13, M12; RQ-16, 97; TQ-12). Synthetic data."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import struct
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fakes import FakeASR, FakeAudioCodec, FakeClock, FakeStore
from worker_helpers import NOW, Rig, Role, add_inbox, audio, open_store, provision, rows

from saathi.domain import AudioBlob, MediaBlob, MediaKind, MediaRef, Source, Transcript, WavBlob
from saathi.errors import AsrUnavailable, AudioUndecodable, DecodeTimeout, MediaExpired
from saathi.ids import HouseholdId
from saathi.media import LocalMediaStore
from saathi.perception.asr import LocalAsrClient
from saathi.voice.codec import OpusCodec, wav_seconds
from saathi.worker import Worker, summary_slots, unscanned_sweep
from saathi.worker.steps import WorkerConfig, _Ctx, _transcribe
from saathi.worker.summaries import render_card

from conftest import PgDb  # isort: skip

ROOT = Path(__file__).resolve().parent.parent


def wav(seconds: float, rate: int = 16000) -> bytes:
    n = int(seconds * rate) * 2
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", n) + b"\x00" * n


# ---- the ASR port and its fake -------------------------------------------------------------------------------------
async def test_fake_asr_scripted_text():
    asr = FakeASR(["pehla", Transcript(text="doosra", language="hi")])
    assert (await asr.transcribe(AudioBlob(data=b"x"))).text == "pehla"
    assert (await asr.transcribe(AudioBlob(data=b"x"))).text == "doosra"
    assert (await asr.transcribe(AudioBlob(data=b"x"))).text == "synthetic transcript"  # then the default


def ctx(cfg_hint: str = "hi") -> _Ctx:
    from worker_helpers import household_config

    cfg = household_config("ap1").model_copy(update={"asr_hint": cfg_hint, "radar_audio_max_s": 120})
    return SimpleNamespace(inbox="m1", cfg=cfg)  # type: ignore[return-value]


def deps(asr: Any, codec: Any) -> Any:
    return SimpleNamespace(asr=asr, codec=codec)


@pytest.mark.parametrize("error", [AsrUnavailable, AudioUndecodable, DecodeTimeout])
async def test_asr_unavailable_forwards_audio(error: type[Exception]):
    asr, codec = FakeASR([error]), FakeAudioCodec()
    assert await _transcribe(ctx(), deps(asr, codec), MediaBlob(data=b"x", mime="audio/ogg")) is None  # no transcript


async def test_undecodable_forwards_audio():
    codec = FakeAudioCodec()
    codec.fail("to_wav16k", AudioUndecodable)
    asr = FakeASR()
    assert await _transcribe(ctx(), deps(asr, codec), MediaBlob(data=b"x", mime="audio/ogg")) is None
    assert asr.calls == []  # a note that cannot be decoded never reaches ASR


async def test_decode_timeout_forwards_audio():
    codec = FakeAudioCodec()
    codec.fail("to_wav16k", DecodeTimeout)
    assert await _transcribe(ctx(), deps(FakeASR(), codec), MediaBlob(data=b"x", mime="audio/ogg")) is None


async def test_an_unexpected_error_still_forwards_the_audio():
    blob = MediaBlob(data=b"x", mime="a")
    assert await _transcribe(ctx(), deps(FakeASR([RuntimeError]), FakeAudioCodec()), blob) is None


async def test_asr_gets_the_wav_the_codec_made_and_the_household_hint():
    asr, codec = FakeASR(["text"]), FakeAudioCodec()
    got = await _transcribe(ctx("en"), deps(asr, codec), MediaBlob(data=b"opus-bytes", mime="audio/ogg"))
    assert got == "text"
    [(args, kwargs)] = asr.calls_of("transcribe")
    assert args[0].mime == "audio/wav" and kwargs["hint"] == "en"
    assert codec.calls_of("to_wav16k")[0][1]["max_seconds"] == 120


async def test_empty_transcript_counts_as_no_transcript():
    assert await _transcribe(ctx(), deps(FakeASR(["   "]), FakeAudioCodec()), MediaBlob(data=b"x", mime="a")) is None


# ---- the unscanned alert -------------------------------------------------------------------------------------------
async def test_sweep_asks_for_audio_unscanned_after_120s():
    seen: list[Any] = []

    class Scan:
        async def unscanned_audio(self, now: Any, max_s: int) -> list:
            seen.append((now, max_s))
            return []

    store = SimpleNamespace(scan=Scan(), tx=FakeStore().tx)
    clock = FakeClock(NOW)
    assert await unscanned_sweep(store, clock) == 0  # type: ignore[arg-type]
    assert seen == [(NOW, 120)]


async def test_audio_row_unscanned_alert_after_120s(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    await add_inbox(pg_db, ring, pub, "h1", "m1", kind="audio", media_ref=audio(rig), received_at=NOW)
    await add_inbox(pg_db, ring, pub, "h1", "m2", kind="audio", media_ref=audio(rig, "1002"), received_at=NOW)
    clock = FakeClock(NOW)
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        await clock.advance(119)
        assert await unscanned_sweep(store, clock) == 0  # not yet
        await clock.advance(2)
        assert await unscanned_sweep(store, clock) == 2  # past 120 s with no transcript
        assert await unscanned_sweep(store, clock) == 0  # a repeat opens nothing
    got = await rows(pg_db, "SELECT kind, origin, state FROM alert_events ORDER BY inbox_id")
    assert [(r["kind"], r["origin"], r["state"]) for r in got] == [("unscanned", "unscanned_overdue", "open")] * 2


async def test_a_transcribed_row_is_not_unscanned(pg_db: PgDb):
    ring, pub = await provision(pg_db)
    rig = Rig()
    rig.asr.script.append("kuch nahi")
    await add_inbox(pg_db, ring, pub, "h1", "m1", kind="audio", media_ref=audio(rig), received_at=NOW)
    clock = FakeClock(NOW)
    async with open_store(pg_db, Role.CORE, clock=clock) as store:
        assert await Worker(store, rig.deps(store, ring, clock, WorkerConfig()), clock).run_once() is True
        await clock.advance(300)
        assert await unscanned_sweep(store, clock) == 0
    assert await rows(pg_db, "SELECT 1 FROM alert_events") == []


# ---- the real client, over a fake transport ------------------------------------------------------------------------
async def test_local_asr_client_posts_the_wav_it_is_given():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"text": " namaste "})

    client = LocalAsrClient("http://asr.test:8080/", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    got = await client.transcribe(AudioBlob(data=wav(0.1), mime="audio/wav"), hint="hi")
    assert got.text == "namaste" and got.language == "hi"
    [req] = seen
    assert req.url.path == "/inference" and b'filename="note.wav"' in req.content and b"RIFF" in req.content


BAD = [httpx.Response(500), httpx.Response(200, text="not json"), httpx.Response(200, json={})]


@pytest.mark.parametrize("response", BAD)
async def test_local_asr_client_errors_are_asr_unavailable(response: httpx.Response):
    transport = httpx.MockTransport(lambda r: response)
    client = LocalAsrClient("http://asr.test", client=httpx.AsyncClient(transport=transport))
    with pytest.raises(AsrUnavailable):
        await client.transcribe(AudioBlob(data=b"x"))


async def test_local_asr_client_transport_error_is_asr_unavailable():
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = LocalAsrClient("http://asr.test", client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(AsrUnavailable):
        await client.transcribe(AudioBlob(data=b"x"))


def test_the_asr_module_no_longer_talks_about_ffmpeg():
    text = (ROOT / "src/saathi/perception/asr.py").read_text()
    assert "ffmpeg" not in text.lower() and "--convert" not in text and "opusdec" in text


def test_no_module_calls_ffmpeg():
    for path in (ROOT / "src/saathi").rglob("*.py"):
        assert "ffmpeg" not in path.read_text().lower(), path


# ---- the codec ------------------------------------------------------------------------------------------------------
async def read_text(path: str) -> str:
    return await asyncio.to_thread(Path(path).read_text)


async def read_json(path: str) -> Any:
    return json.loads(await read_text(path))


async def exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


def program(tmp_path: Path, body: str, name: str = "fake") -> str:
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\nimport sys, pathlib, json, time\nargs = sys.argv[1:]\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


DECODER_OK = """
pathlib.Path(sys.argv[0] + '.argv').write_text(json.dumps(args))
n = int(16000 * SECONDS) * 2
hdr = b'RIFF' + (36 + n).to_bytes(4, 'little') + b'WAVEfmt ' + (16).to_bytes(4, 'little') + (1).to_bytes(2, 'little') \\
    + (1).to_bytes(2, 'little') + (16000).to_bytes(4, 'little') + (32000).to_bytes(4, 'little') \\
    + (2).to_bytes(2, 'little') + (16).to_bytes(2, 'little') + b'data' + n.to_bytes(4, 'little')
pathlib.Path(args[-1]).write_bytes(hdr + b'\\x00' * n)
"""


async def test_opus_codec_decodes_with_opusdec_to_16k_wav(tmp_path: Path):
    exe = program(tmp_path, DECODER_OK.replace("SECONDS", "1.5"))
    out = await OpusCodec(decoder=exe).to_wav16k(AudioBlob(data=b"synthetic-opus"), max_seconds=30)
    assert out.data[:4] == b"RIFF" and wav_seconds(out.data) == pytest.approx(1.5)
    argv = await read_json(exe + ".argv")
    assert argv[:4] == ["--quiet", "--rate", "16000", "--force-wav"] and len(argv) == 6  # no shell, no ffmpeg


async def test_opus_codec_too_long_is_undecodable(tmp_path: Path):
    exe = program(tmp_path, DECODER_OK.replace("SECONDS", "3"))
    with pytest.raises(AudioUndecodable):
        await OpusCodec(decoder=exe).to_wav16k(AudioBlob(data=b"x"), max_seconds=2)


async def test_opus_codec_bad_exit_is_undecodable(tmp_path: Path):
    with pytest.raises(AudioUndecodable):
        await OpusCodec(decoder=program(tmp_path, "sys.exit(1)")).to_wav16k(AudioBlob(data=b"x"), max_seconds=30)


async def test_opus_codec_output_that_is_not_wav_is_undecodable(tmp_path: Path):
    exe = program(tmp_path, "pathlib.Path(args[-1]).write_bytes(b'not a wav file' * 5)")
    with pytest.raises(AudioUndecodable):
        await OpusCodec(decoder=exe).to_wav16k(AudioBlob(data=b"x"), max_seconds=30)


async def test_opus_codec_timeout_kills_the_process(tmp_path: Path):
    body = "import os\npathlib.Path(sys.argv[0] + '.pid').write_text(str(os.getpid()))\ntime.sleep(30)"
    exe = program(tmp_path, body)
    with pytest.raises(DecodeTimeout):
        await OpusCodec(decoder=exe, timeout_s=0.5).to_wav16k(AudioBlob(data=b"x"), max_seconds=30)
    pid = int(await read_text(exe + ".pid"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the decoder is gone


async def test_opus_codec_missing_program_is_asr_unavailable(tmp_path: Path):
    with pytest.raises(AsrUnavailable):
        await OpusCodec(decoder=str(tmp_path / "absent")).to_wav16k(AudioBlob(data=b"x"), max_seconds=30)


async def test_opus_codec_caps_the_input(tmp_path: Path):
    with pytest.raises(AudioUndecodable):
        small = OpusCodec(decoder=program(tmp_path, "pass"), max_bytes=10)
        await small.to_wav16k(AudioBlob(data=b"x" * 11), max_seconds=30)
    with pytest.raises(AudioUndecodable):
        await OpusCodec(decoder=program(tmp_path, "pass")).to_wav16k(AudioBlob(data=b""), max_seconds=30)


async def test_opus_codec_encodes_a_clip(tmp_path: Path):
    exe = program(tmp_path, "pathlib.Path(args[-1]).write_bytes(b'OggS' + pathlib.Path(args[-2]).read_bytes()[:8])")
    clip = await OpusCodec(encoder=exe).encode_clip(WavBlob(data=wav(0.1)))
    assert clip.data.startswith(b"OggS") and len(clip.sha256) == 64


async def test_opus_codec_runs_with_no_stdin_and_no_shell(tmp_path: Path):
    exe = program(tmp_path, "assert sys.stdin.read() == ''\n" + DECODER_OK.replace("SECONDS", "0.5"))
    await OpusCodec(decoder=exe).to_wav16k(AudioBlob(data=b"x; touch pwned"), max_seconds=30)
    assert not await exists(tmp_path / "pwned") and not await exists(Path("pwned"))


# ---- the media store ------------------------------------------------------------------------------------------------
REF = MediaRef(source=Source.WHATSAPP, handle="1001")


async def test_media_store_files_are_owner_only(tmp_path: Path):
    store = LocalMediaStore(tmp_path / "media")
    key = await store.put(REF, MediaBlob(data=b"voice", mime="audio/ogg"), household=HouseholdId("h1"))
    path = tmp_path / "media" / key
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert await store.get(key) == MediaBlob(data=b"voice", mime="audio/ogg")
    assert "1001" not in key  # the provider handle is not in the file name


async def test_media_store_missing_or_foreign_key_is_expired(tmp_path: Path):
    store = LocalMediaStore(tmp_path / "media")
    (tmp_path / "secret").write_text("x")
    for key in ("h1/absent", "../secret", "/etc/passwd"):
        with pytest.raises(MediaExpired):
            await store.get(key)


async def test_media_store_refuses_a_household_that_names_a_path(tmp_path: Path):
    store = LocalMediaStore(tmp_path / "media")
    for bad in ("", "..", "a/b", ".hidden"):
        with pytest.raises(ValueError):
            await store.put(REF, MediaBlob(data=b"x", mime="a"), household=HouseholdId(bad))


async def test_media_store_prunes_after_30_days(tmp_path: Path):
    store = LocalMediaStore(tmp_path / "media")
    old = await store.put(REF, MediaBlob(data=b"old", mime="audio/ogg"), household=HouseholdId("h1"))
    new = await store.put(MediaRef(source=Source.WHATSAPP, handle="1002"), MediaBlob(data=b"new", mime="a"),
                          household=HouseholdId("h1"))  # fmt: skip
    long_ago = (NOW - timedelta(days=31)).timestamp()
    for suffix in ("", ".mime"):
        os.utime(tmp_path / "media" / (old + suffix), (long_ago, long_ago))
    assert store.prune(now=NOW.timestamp()) == 1
    with pytest.raises(MediaExpired):
        await store.get(old)
    assert (await store.get(new)).data == b"new"


# ---- summaries ------------------------------------------------------------------------------------------------------
def test_summary_slots_hold_enums_and_rule_ids_only():
    ids = ["RD-EMR-3", "RD-EMR-3", "free text with PAN ABCDE1234F"]
    s = summary_slots(MediaKind.AUDIO, ids, "ride", emergency=True)
    assert s.rule_ids == ("RD-EMR-3",) and s.intent == "ride"
    assert summary_slots(MediaKind.TEXT, [], "whatever the model said").intent == "unknown"
    fields = {"kind", "intent", "emergency", "scam", "rule_ids", "transcribed", "audio_attached", "flag"}
    assert set(type(s).model_fields) == fields


def test_summary_card_never_carries_transcript_text():
    s = summary_slots(MediaKind.AUDIO, ["RD-SCR-1"], "unknown", scam=True, transcribed=True)
    card = render_card(s)
    assert card.title == "Possible scam" and "Rules: RD-SCR-1" in card.lines and card.buttons == []
    with pytest.raises(ValueError):
        type(s)(kind=MediaKind.TEXT, transcript="canary words")  # type: ignore[call-arg]


async def test_opus_codec_caps_the_input_by_the_longest_clip_it_would_accept(tmp_path: Path):
    ran = tmp_path / "ran"
    exe = program(tmp_path, f"pathlib.Path({str(ran)!r}).write_text('x')")
    with pytest.raises(AudioUndecodable):  # a 2 s clip can not be more than 2 * 8000 bytes of Opus
        await OpusCodec(decoder=exe).to_wav16k(AudioBlob(data=b"x" * (2 * 8000 + 1)), max_seconds=2)
    assert not await exists(ran)  # the decoder never ran
