"""LT-25: the trace exporter and the privacy canary (M18; RQ-44, 57, 97; PL-07; TQ-14, 15, 16).

A span holds ids, counts, hashes and durations through a field allowlist. The canary run feeds a text and a voice
note transcript holding `CANARY-<uuid>` and a synthetic phone number through the edge, the worker, the alert service
and the caregiver bot (fakes for every channel, a real database), then reads every table by `COPY ... TO STDOUT`,
every trace file, every fake channel payload and the captured logs. The canary appears only in the decrypted
`raw_text` and in the show-raw reply. Postgres tests need SAATHI_TEST_DSN; the rest run without it. Synthetic values
only.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alert_helpers import AlertRig, make_rig
from conftest import PgDb
from edge_helpers import ELDER_NUMBER, SENDER_KEY, EdgeFakeStore, client, fake_app, pg_edge, wa_body, wa_msg, wa_sign
from fakes import FakeClock
from store_helpers import open_store
from worker_helpers import NOW, Rig, Role, audio, grant

from saathi.audit import PgAuditSink
from saathi.channels.telegram import CallbackRouter, RoutedUpdate
from saathi.guards.pii import verhoeff_valid
from saathi.ids import ApproverId, HouseholdId
from saathi.ports import TraceExporter
from saathi.store.keys import generate_secret, sender_hmac
from saathi.store.repos._util import aad
from saathi.trace import ALLOWED_FIELDS, DROPPED, JsonlExporter, NullExporter, build_exporter, traced
from saathi.trace import allowlist as allowlist_mod
from saathi.worker import Worker

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "saathi"
H = "h1"
CLOCK0 = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
PHONE = "+91 90000 00005"  # synthetic
PHONE_DIGITS = "9000000005"
SHA = "ab" * 32
_CHECK = next(d for d in "0123456789" if verhoeff_valid("23456789012" + d))
AADHAAR = f"2345 6789 012{_CHECK}"  # synthetic and checksum-valid, so the redactor knows it


def exporter(tmp_path: Path, **kw: Any) -> tuple[JsonlExporter, FakeClock]:
    clock = FakeClock(CLOCK0)
    return JsonlExporter(tmp_path / "traces", clock, **kw), clock


def lines(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for p in sorted(directory.glob("trace-*.jsonl")) for line in p.read_text().splitlines()]


# --- the exporter, no database -----------------------------------------------------------------------------------
def test_spans_hold_ids_counts_hashes(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path)
    exp.span(
        "worker.process", run_id="run_1", request_id="rq_1", inbox_id="in_1", event_id="al_1", household_id="h1",
        intent="ride", confidence=0.5, flags="a,b", rule_ids=("RD-EMG-1", "RD-EMG-2"), count=3, bytes=1204,
        sha256=SHA, duration_ms=12.5, state="processed", reason="summary_only", stage="a", grade=1, attempt=2, ok=True,
    )  # fmt: skip
    [rec] = lines(tmp_path / "traces")
    assert rec["ts"] == CLOCK0.isoformat() and rec["name"] == "worker.process"
    assert {k for k in rec} - {"ts", "name"} == ALLOWED_FIELDS
    assert rec["sha256"] == SHA and rec["rule_ids"] == "RD-EMG-1,RD-EMG-2" and rec["ok"] is True
    assert exp.spans == 1 and exp.dropped == 0 and exp.export_errors == 0
    assert (tmp_path / "traces" / "trace-2026-03-01.jsonl").is_file()
    assert oct((tmp_path / "traces" / "trace-2026-03-01.jsonl").stat().st_mode & 0o777) == "0o600"


def test_unlisted_fields_dropped(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path)
    exp.span("edge.inbound", text="mera pan hai", transcript="x", prompt="y", payload={"a": 1}, count=1)  # type: ignore[arg-type]
    [rec] = lines(tmp_path / "traces")
    assert set(rec) == {"ts", "name", "count"}
    assert exp.dropped == 4
    assert allowlist_mod.clean_fields({"text": "anything at all"}) == ({}, 1)


def test_long_and_restricted_strings_become_the_marker(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path)
    long_id = "h1" + "a" * 62
    exp.span("t.x", reason="w" * 65, state=PHONE, intent=f"aadhaar {AADHAAR}", household_id=long_id)
    [rec] = lines(tmp_path / "traces")
    assert rec["reason"] == DROPPED and rec["state"] == DROPPED and rec["intent"] == DROPPED
    assert rec["household_id"] == long_id  # 64 characters is the limit, not over it
    assert exp.dropped == 3
    text = (tmp_path / "traces" / "trace-2026-03-01.jsonl").read_text()
    assert PHONE_DIGITS not in text and AADHAAR[:4] not in text


_BAD_CHECK = next(d for d in "0123456789" if not verhoeff_valid("23456789012" + d))
LEAK_PROBES = {  # synthetic; each one passed the old allowlist on a coded field
    "name": "Ravindra",
    "lowercase name": "ravindra",
    "address fragment": "12 Example Lane",
    "short word": "dawai",
    "exception text with a name": "ValueError: Ravindra not found",
    "model sentence": "Sure, I will call your son.",
    "pin": "560000",
    "spaced phone": "9 0 0 0 0 0 0 0 0 5",
    "bad-checksum aadhaar": f"2345 6789 012{_BAD_CHECK}",
    "bad-checksum aadhaar, joined": f"23456789012{_BAD_CHECK}",
}


@pytest.mark.parametrize("probe", list(LEAK_PROBES.values()), ids=list(LEAK_PROBES))
@pytest.mark.parametrize("key", ["reason", "state", "stage", "grade", "intent", "run_id", "household_id", "sha256"])
def test_short_free_text_in_a_typed_field_is_dropped(tmp_path: Path, key: str, probe: str) -> None:
    exp, _ = exporter(tmp_path)
    exp.span("t.x", **{key: probe})
    [rec] = lines(tmp_path / "traces")
    assert rec[key] == DROPPED and exp.dropped == 1


@pytest.mark.parametrize("probe", list(LEAK_PROBES.values()), ids=list(LEAK_PROBES))
def test_short_free_text_in_a_code_list_is_dropped(probe: str) -> None:
    assert allowlist_mod.clean_value("rule_ids", (probe,)) is None
    assert allowlist_mod.clean_value("flags", probe) is None


def test_typed_fields_keep_codes_ids_and_exception_classes(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path)
    exp.span(
        "t.x", reason="ValueError", state="processed", intent="unknown", stage="a", household_id="h1",
        run_id="run_" + "0f" * 16, flags="emergency,scam", rule_ids=("RD-EMG-1",),
    )  # fmt: skip
    [rec] = lines(tmp_path / "traces")
    assert DROPPED not in rec.values() and exp.dropped == 0
    exp.span("t.x", reason="NotARealErrorClass", sha256="AB" * 32, count="3")  # type: ignore[arg-type]
    assert all(v == DROPPED for k, v in lines(tmp_path / "traces")[1].items() if k not in {"ts", "name"})


def test_symlinked_trace_dir_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "traces").symlink_to(real)
    exp, _ = exporter(tmp_path)
    exp.span("t.x", count=1)
    assert exp.export_errors == 1 and list(real.iterdir()) == []


def test_symlinked_trace_file_is_not_followed(tmp_path: Path) -> None:
    d = tmp_path / "traces"
    d.mkdir()
    target = tmp_path / "victim.txt"
    target.write_text("keep\n")
    (d / "trace-2026-03-01.jsonl").symlink_to(target)
    exp, _ = exporter(tmp_path)
    exp.span("t.x", count=1)
    assert exp.export_errors == 1 and target.read_text() == "keep\n"


def test_existing_trace_dir_is_made_private(tmp_path: Path) -> None:
    d = tmp_path / "traces"
    d.mkdir(mode=0o755)
    d.chmod(0o755)
    exp, _ = exporter(tmp_path)
    exp.span("t.x", count=1)
    assert oct(d.stat().st_mode & 0o777) == "0o700" and exp.export_errors == 0


def test_short_write_is_continued(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from saathi.trace import jsonl

    real = os.write
    monkeypatch.setattr(jsonl.os, "write", lambda fd, data: real(fd, data[:5]))
    exp, _ = exporter(tmp_path)
    exp.span("t.x", count=1, household_id="h1")
    assert exp.export_errors == 0
    [rec] = lines(tmp_path / "traces")
    assert rec["count"] == 1 and rec["household_id"] == "h1"


def test_failed_write_leaves_no_partial_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from saathi.trace import jsonl

    exp, _ = exporter(tmp_path)
    exp.span("t.x", count=1)
    real, calls = os.write, []

    def flaky(fd: int, data: bytes) -> int:
        calls.append(1)
        if len(calls) > 1:
            raise OSError(28, "no space left")
        return real(fd, data[:7])

    monkeypatch.setattr(jsonl.os, "write", flaky)
    exp.span("t.x", count=2)
    assert exp.export_errors == 1
    path = tmp_path / "traces" / "trace-2026-03-01.jsonl"
    assert path.read_text().endswith("\n") and [r["count"] for r in lines(tmp_path / "traces")] == [1]


def test_retention_keeps_exactly_that_many_days(tmp_path: Path) -> None:
    d = tmp_path / "traces"
    d.mkdir()
    edge = d / "trace-2026-01-31.jsonl"  # today minus 29 days: day 30 of 30, kept
    over = d / "trace-2026-01-30.jsonl"  # today minus 30 days: day 31, pruned
    for p in (edge, over):
        p.write_text("x\n")
    exp, _ = exporter(tmp_path, retention_days=30)
    exp.span("t.x", count=1)
    assert edge.exists() and not over.exists()
    assert len(list(d.glob("trace-*.jsonl"))) == 2


def test_no_whole_node_output(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path)
    node_output = {"summary": "a long free text of the elder", "items": [1, 2, 3]}
    exp.span("t.node", state=node_output, reason=b"bytes", flags=["x"] * 40, rule_ids=[node_output], count=object())  # type: ignore[arg-type]
    [rec] = lines(tmp_path / "traces")
    assert all(rec[k] == DROPPED for k in ("state", "reason", "flags", "rule_ids", "count"))
    assert "elder" not in json.dumps(rec)


def span_problems(source: str, *, in_trace: bool = False, name: str = "x.py") -> tuple[list[str], int]:
    """(problems, number of direct `.span(` calls) for one module's source."""
    tree = ast.parse(source)
    callees = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    problems: list[str] = []
    seen = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "span" and not in_trace and id(node) not in callees:
            problems.append(f"{name}:{node.lineno} span referenced without a call")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            attr = node.args[1] if len(node.args) > 1 else None
            if not in_trace and attr is not None and not (isinstance(attr, ast.Constant) and attr.value != "span"):
                problems.append(f"{name}:{node.lineno} getattr may reach span")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "span":
            seen += 1
            if not all(kw.arg in ALLOWED_FIELDS for kw in node.keywords if kw.arg):
                problems.append(f"{name}:{node.lineno} unlisted keyword")
            if name != "exporter.py" and any(kw.arg is None for kw in node.keywords):
                problems.append(f"{name}:{node.lineno} spread keywords")  # `traced` spreads ids, still allowlisted
    return problems, seen


def test_span_calls_in_the_source_name_only_listed_fields() -> None:
    """No call site can pass a transcript or a node output: every keyword of every `.span(` call is allowlisted.

    `.span` may only be called directly: an alias (`f = x.span`), a reference passed on, or `getattr(x, "span")` would
    hide the keywords from this check, so each one fails it outside the trace package.
    """
    seen, problems = 0, []
    for path in SRC.rglob("*.py"):
        found, n = span_problems(path.read_text(), in_trace=path.parent.name == "trace", name=path.name)
        problems += found
        seen += n
    assert problems == [] and seen >= 5


@pytest.mark.parametrize(
    "source",
    [
        "f = t.span\nf('a.b', text=x)",
        "run(t.span)",
        "getattr(t, 'span')('a.b', text=x)",
        "getattr(t, name)('a.b', text=x)",
        "t.span('a.b', text=x)",
        "t.span('a.b', **fields)",
    ],
)
def test_the_span_check_catches_hidden_calls(source: str) -> None:
    assert span_problems(source)[0]


def test_span_name_must_be_a_code_constant(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path)
    exp.span("hello there, my number is 9000000005")
    exp.span("alerts.step")
    assert [r["name"] for r in lines(tmp_path / "traces")] == [DROPPED, "alerts.step"]


def test_unwritable_file_does_not_fail_request(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "traces"
    blocker.write_text("a file where the directory should be")
    exp = JsonlExporter(blocker, FakeClock(CLOCK0))
    exp.span("t.x", count=1)  # does not raise
    exp.span("t.x", count=2)
    assert exp.export_errors == 2 and exp.spans == 0
    assert "NotADirectoryError" in caplog.text or "FileExistsError" in caplog.text


async def test_unwritable_trace_does_not_fail_an_edge_request(tmp_path: Path) -> None:
    blocker = tmp_path / "traces"
    blocker.write_text("")
    exp = JsonlExporter(blocker, FakeClock(CLOCK0))
    store = EdgeFakeStore()
    app = fake_app(store)
    app.state.edge.trace = exp
    body = wa_body(wa_msg(text={"body": "namaste"}))
    async with client(app) as c:
        resp = await c.post("/wa/wa-path-x", content=body, headers=wa_sign(body))
    assert resp.status_code == 200 and exp.export_errors >= 1


def test_a_clock_failure_is_counted_not_raised(tmp_path: Path) -> None:
    class Broken:
        def now(self) -> datetime:
            raise RuntimeError("clock")

    exp = JsonlExporter(tmp_path / "t", Broken())  # type: ignore[arg-type]
    exp.span("t.x", count=1)
    assert exp.export_errors == 1


def test_files_rotate_by_clock_and_old_ones_are_pruned(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path, retention_days=30)
    d = tmp_path / "traces"
    d.mkdir()
    old = d / "trace-2026-01-20.jsonl"  # 40 days before the clock
    keep = d / "trace-2026-02-10.jsonl"  # 19 days
    other = d / "notes.txt"
    for p in (old, keep, other):
        p.write_text("x\n")
    exp.span("t.x", count=1)
    assert not old.exists() and keep.exists() and other.exists()
    next_day = JsonlExporter(d, FakeClock(CLOCK0 + timedelta(days=1)))
    next_day.span("t.x", count=2)
    assert (d / "trace-2026-03-01.jsonl").is_file() and (d / "trace-2026-03-02.jsonl").is_file()


def test_null_exporter_and_builder(tmp_path: Path) -> None:
    assert NullExporter().span("t.x", count=1) is None
    assert isinstance(build_exporter("", FakeClock(CLOCK0)), NullExporter)
    exp = build_exporter(str(tmp_path / "t"), FakeClock(CLOCK0))
    assert isinstance(exp, JsonlExporter)
    assert isinstance(exp, TraceExporter) and isinstance(NullExporter(), TraceExporter)


async def test_traced_records_ids_and_duration_only(tmp_path: Path) -> None:
    exp, _ = exporter(tmp_path)

    @traced("t.sync", exp)
    def work(text: str, *, run_id: str = "") -> str:
        return text.upper()

    @traced("t.async", exp)
    async def awork(text: str, *, inbox_id: str = "") -> str:
        raise ValueError(text)

    assert work("mera pan hai", run_id="run_9") == "MERA PAN HAI"
    with pytest.raises(ValueError):
        await awork("secret words", inbox_id="in_9")
    first, second = lines(tmp_path / "traces")
    assert first["run_id"] == "run_9" and first["ok"] is True and first["duration_ms"] >= 0
    assert second["inbox_id"] == "in_9" and second["ok"] is False
    raw = json.dumps([first, second])
    assert "pan" not in raw and "secret" not in raw and "MERA" not in raw


def test_the_old_tracing_module_is_gone() -> None:
    assert not (SRC / "observability.py").exists()
    for path in [*SRC.rglob("*.py"), *(ROOT / "tests").glob("*.py"), *(ROOT / "scripts").glob("*.py")]:
        if path.name == Path(__file__).name:
            continue
        assert "saathi.observability" not in path.read_text(), path


# --- the canary run, against Postgres ------------------------------------------------------------------------------
@dataclass
class Evidence:
    canary: str
    db: PgDb
    trace_dir: Path
    rig: AlertRig
    worker_rig: Rig
    shown: list[str] = field(default_factory=list)  # what the show-raw reply carried
    raw_plain: str = ""  # the decrypted raw_text of every row


def _needles(canary: str) -> list[str]:
    out = [canary, PHONE_DIGITS, ELDER_NUMBER.lstrip("+")]
    return out + [n.encode().hex() for n in out]


async def every_table(db: PgDb) -> dict[str, str]:
    """Every table of every schema by COPY TO STDOUT (so a bytea column shows as hex, as it would in a dump)."""
    conn = await asyncpg.connect(db.dsn())
    try:
        tables = await conn.fetch(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
        )
        out: dict[str, str] = {}
        for t in tables:
            name = f"{t['schemaname']}.{t['tablename']}"
            buf = io.BytesIO()
            await conn.copy_from_query(f'SELECT * FROM "{t["schemaname"]}"."{t["tablename"]}"', output=buf)
            out[name] = buf.getvalue().decode("utf-8", "replace")
        return out
    finally:
        await conn.close()


def _payload(sent: list[tuple[Any, ...]]) -> str:
    return "\n".join(repr(p) for _m, _to, p, _k in sent)


async def leaks(ev: Evidence, caplog: pytest.LogCaptureFixture) -> list[str]:
    """Where a needle appears outside `raw_text` and the show-raw reply. Empty means the run is clean."""
    places: dict[str, str] = {
        f"table {name}": text for name, text in (await every_table(ev.db)).items() if name != "public.raw_text"
    }
    places["trace files"] = "\n".join(p.read_text() for p in ev.trace_dir.glob("trace-*.jsonl"))
    places["caregiver payloads"] = _payload(ev.rig.caregiver.sent)
    places["worker caregiver payloads"] = _payload(ev.worker_rig.caregiver.sent)
    places["elder payloads"] = _payload(ev.rig.elder.sent) + _payload(ev.worker_rig.elder.sent)
    places["audit events"] = repr(ev.rig.audit.events) + repr(ev.worker_rig.audit.events)
    places["logs"] = caplog.text
    return sorted(
        f"{where}: {n[:12]}" for where, text in places.items() for n in _needles(ev.canary) if n in text
    )


async def run_paths(
    db: PgDb, trace_dir: Path, trace: TraceExporter, monkeypatch: pytest.MonkeyPatch, canary: str
) -> Evidence:
    """A text, a voice note and an emergency text, each holding the canary, through edge, worker, alerts and the bot."""
    rig = await make_rig(db)
    rig.trace = trace
    su = await asyncpg.connect(db.dsn())
    try:  # the edge finds the elder by the keyed hash of the number
        await su.execute(
            "UPDATE elder_identities SET sender_hmac = $1 WHERE household_id = 'h1' AND channel = 'whatsapp'",
            sender_hmac(SENDER_KEY, "whatsapp", ELDER_NUMBER),
        )
    finally:
        await su.close()
    ev = Evidence(canary=canary, db=db, trace_dir=trace_dir, rig=rig, worker_rig=Rig())
    ev.worker_rig.audit = rig.audit
    ev.worker_rig.asr.script.append(f"{canary} mera number {PHONE} hai")
    handle = audio(ev.worker_rig, "1001")

    body = [
        wa_msg(mid="wamid.1", text={"body": f"kal subah milna {canary} call {PHONE}"}),
        wa_msg("audio", mid="wamid.2", audio={"id": handle, "mime_type": "audio/ogg"}),
        wa_msg(mid="wamid.3", text={"body": f"seene mein dard {canary}"}),
    ]
    async with pg_edge(db, clock=rig.clock) as c:
        c.app.state.edge.trace = trace  # type: ignore[attr-defined]
        for n, msg in enumerate(body):
            raw = wa_body(msg)
            resp = await c.post("/wa/wa-path-x", content=raw, headers=wa_sign(raw))
            assert resp.status_code == 200, n

    async with open_store(db, Role.CORE, clock=rig.clock) as store:
        deps = ev.worker_rig.deps(store, rig.ring, rig.clock)
        deps.trace = trace
        worker = Worker(store, deps, rig.clock)
        for _ in range(8):
            if not await worker.run_once():
                break
        svc = rig.service(store)
        for _ in range(3):
            await svc.tick()
        text_inbox = (await rig.sql("SELECT id FROM inbox WHERE provider_message_id = 'wa:wamid.1'"))[0]["id"]
        su = await asyncpg.connect(db.dsn())
        try:
            await su.execute(
                "INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
                " VALUES ($1, 'audit', 'a1', $2)",
                H, rig.ring.wrap(generate_secret(), household=HouseholdId(H), purpose="audit", key_id="a1"),
            )  # fmt: skip
            await grant(su, H, "show_raw", "telegram_caregiver")
        finally:
            await su.close()
        out = FakeBotOut()
        router = CallbackRouter(store, svc, rig.ring, PgAuditSink(store, rig.ring), out, trace=trace)
        up = RoutedUpdate(
            update_id=7, approver_id=ApproverId("ap1h1"), kind="callback", data=f"raw:{text_inbox}",
            household_id=HouseholdId(H),  # type: ignore[arg-type]
        )  # fmt: skip
        await router.handle(up)
        ev.shown = [p for _m, _to, p, _k in out.sent]
        plain = []
        async with store.tx(household=HouseholdId(H)) as tx:
            for r in await tx.fetch("SELECT inbox_id, part, body_sealed, key_id FROM raw_text"):
                blob = await rig.ring.open(
                    tx, bytes(r["body_sealed"]), str(r["key_id"]), aad=aad(H, str(r["inbox_id"]), str(r["part"]))
                )
                plain.append(blob.decode())
        ev.raw_plain = "\n".join(plain)
    return ev


class FakeBotOut:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any, Any, Any]] = []

    async def send_text(self, to: Any, text: str, *, key: Any) -> None:
        self.sent.append(("send_text", to, text, key))


def new_canary() -> str:
    return f"CANARY-{uuid.uuid4()}"


async def test_canary_only_in_raw_text_and_show_raw(
    pg_db: PgDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    canary = new_canary()
    trace_dir = tmp_path / "traces"
    clock = FakeClock(NOW)
    ev = await run_paths(pg_db, trace_dir, JsonlExporter(trace_dir, clock), monkeypatch, canary)
    # the canary is where it belongs: sealed text, decrypted, and the show-raw reply
    assert ev.raw_plain.count(canary) == 3 and PHONE in ev.raw_plain
    assert any(canary in str(p) for p in ev.shown)
    # and nowhere else
    assert await leaks(ev, caplog) == []
    # the run did write spans from every path, so an empty trace directory cannot make the check pass
    recs = lines(trace_dir)
    names = {r["name"] for r in recs}
    assert {"edge.persist", "edge.inbound", "worker.process", "alerts.step", "bot.update"} <= names
    assert all(set(r) <= ALLOWED_FIELDS | {"ts", "name"} for r in recs)
    # a stored phone number is only its keyed hash
    tables = await every_table(pg_db)
    assert not [n for n, t in tables.items() if PHONE_DIGITS in t or ELDER_NUMBER.lstrip("+") in t]


async def test_patched_exporter_that_writes_the_transcript_fails_the_canary(
    pg_db: PgDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The fault: an exporter without the allowlist that adds the text the radar saw. The canary check must catch it."""
    caplog.set_level(logging.DEBUG)
    from saathi.guards import radar
    from saathi.trace.jsonl import JsonlSink

    seen: list[str | None] = []
    real = radar.combine

    async def spy(text: str | None, classifier: Any, *, run: Any) -> Any:
        seen.append(text)
        return await real(text, classifier, run=run)

    monkeypatch.setattr(radar, "combine", spy)
    trace_dir = tmp_path / "traces"
    clock = FakeClock(NOW)

    class Leaky:
        def __init__(self) -> None:
            self.sink = JsonlSink(trace_dir, 30)

        def span(self, name: str, **fields: Any) -> None:
            self.sink.write(clock.now(), {"name": name, **fields, "transcript": seen[-1] if seen else ""})

    ev = await run_paths(pg_db, trace_dir, Leaky(), monkeypatch, new_canary())
    found = await leaks(ev, caplog)
    assert any(f.startswith("trace files") for f in found), found


# --- the evals row -------------------------------------------------------------------------------------------------
def test_trace_canary_suite_is_a_real_runner_and_passes() -> None:
    from evals import run
    from evals.registry import load_registry

    [row] = [r for r in load_registry() if r.name == "trace_canary"]
    assert row.runner == "harness" and not row.pending_until
    res = run.run_row(row, run.DATASETS)
    assert res.state == "verified" and res.n == 20 and not res.failures


def test_trace_canary_suite_fails_when_the_allowlist_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals import run
    from evals.registry import load_registry
    from saathi.trace import exporter as exporter_mod

    off = lambda fields: ({k: v for k, v in fields.items() if isinstance(v, str | int)}, 0)  # noqa: E731
    monkeypatch.setattr(exporter_mod, "clean_fields", off)
    [row] = [r for r in load_registry() if r.name == "trace_canary"]
    res = run.run_row(row, run.DATASETS)
    assert res.state == "invalid" and res.failures
