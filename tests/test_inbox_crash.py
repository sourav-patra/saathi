"""LT-08: crash and kill tests for the inbox (M07, M12, M29; RQ-79).

The edge and the worker run as subprocesses. A fault hook (`saathi.faults`) or the test itself kills them with SIGKILL,
they are restarted, and the worker runs to quiescence. After every kill: no lost message (one inbox row whose text
decrypts), no duplicate reply (every effect key reached the fake platform once), no lost alert. The alert service is
killed between its sends and its marks; its restart repeats the same keys and the platform refuses them, which the
journal records. Needs Postgres (`SAATHI_TEST_DSN`); the tests that only check the hooks need nothing. Synthetic
values only.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import asyncpg
import httpx
import pytest
from alert_helpers import add_approver, open_event, set_approvers
from conftest import PgDb
from edge_helpers import ELDER_NUMBER, WA_PATH, WA_SECRET, wa_body, wa_msg, wa_sign
from store_helpers import open_store
from worker_helpers import add_inbox, provision

from saathi import faults
from saathi.ids import HouseholdId, idem_key
from saathi.store import Role
from saathi.store.keys import KeyRing, sender_hmac
from saathi.store.repos._util import aad

HERE = Path(__file__).resolve().parent
PROC = HERE / "crash_proc.py"
H = "h1"
KEK = bytes(range(32))  # synthetic key-encryption key shared by the test and the worker subprocess
SENDER_KEY = b"synthetic-crash-sender-key-0001"
TEXT = "mujhe kal subah dawai yaad dilana"
WAIT_S = 30.0


# --- the hooks themselves (no database) -----------------------------------------------------------------------------


@pytest.fixture
def arm_file(tmp_path: Path) -> Path:
    """The second guard: a file that exists, as the crash rig creates in its own temporary directory."""
    path = tmp_path / "fault.arm"
    path.write_text("")
    return path


@pytest.fixture
def kills(monkeypatch: pytest.MonkeyPatch, arm_file: Path) -> list[tuple[int, int]]:
    """Replace the kill with a recorder, so a hook that fires is seen without ending the test process. The arm file is
    in place; a test that wants the second guard missing removes it."""
    seen: list[tuple[int, int]] = []
    monkeypatch.setattr(faults.os, "kill", lambda pid, sig: seen.append((pid, sig)))
    monkeypatch.delenv(faults.ENV_SWITCH, raising=False)
    monkeypatch.delenv(faults.ENV_POINT, raising=False)
    monkeypatch.setenv(faults.ENV_ARM_FILE, str(arm_file))
    return seen


def test_the_four_points_of_the_design_are_the_only_points():
    assert faults.POINTS == {
        "edge.after_commit", "edge.before_commit", "worker.before_settle", "alert.between_send_and_mark",
    }  # fmt: skip


@pytest.mark.parametrize("point", sorted(faults.POINTS))
def test_fault_is_inert_without_the_switch(kills, monkeypatch: pytest.MonkeyPatch, point: str):
    monkeypatch.setenv(faults.ENV_POINT, point)  # the point is named, the switch is not on
    faults.fault(point)
    assert kills == []


@pytest.mark.parametrize("value", ["", "prod", "production", "dev", "TEST", "1", "true"])
def test_fault_is_inert_with_any_other_environment_name(kills, monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv(faults.ENV_SWITCH, value)
    monkeypatch.setenv(faults.ENV_POINT, "edge.before_commit")
    faults.fault("edge.before_commit")
    assert kills == []


@pytest.mark.parametrize("how", ["unset", "empty", "missing", "directory"])
def test_switch_and_point_without_the_arm_file_stay_inert(
    kills, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, how: str
):
    """R16-1: the test switch and a named point are not enough. The arm file must name a file that exists."""
    if how == "unset":
        monkeypatch.delenv(faults.ENV_ARM_FILE)
    elif how == "empty":
        monkeypatch.setenv(faults.ENV_ARM_FILE, "")
    elif how == "missing":
        monkeypatch.setenv(faults.ENV_ARM_FILE, str(tmp_path / "no-such-file"))
    else:
        monkeypatch.setenv(faults.ENV_ARM_FILE, str(tmp_path))  # a directory is not an arm file
    monkeypatch.setenv(faults.ENV_SWITCH, "test")
    for point in sorted(faults.POINTS):
        monkeypatch.setenv(faults.ENV_POINT, point)
        faults.fault(point)
        assert not faults.armed(point)
    faults.fault("no.such.point")  # and an unknown point does not even raise: the hooks are inert
    assert kills == []


def test_the_arm_file_alone_arms_nothing(kills, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(faults.ENV_POINT, "edge.before_commit")  # arm file set by the fixture, switch not on
    faults.fault("edge.before_commit")
    assert kills == []


def test_fault_fires_only_at_the_named_point_with_the_switch_on(kills, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(faults.ENV_SWITCH, "test")
    faults.fault("edge.before_commit")  # no point named
    monkeypatch.setenv(faults.ENV_POINT, "worker.before_settle")
    faults.fault("edge.before_commit")  # another point named
    assert kills == []
    faults.fault("worker.before_settle")
    assert kills == [(os.getpid(), signal.SIGKILL)]


def test_an_unknown_point_raises_only_with_the_switch_on(kills, monkeypatch: pytest.MonkeyPatch):
    faults.fault("no.such.point")  # a typo outside a test changes nothing
    monkeypatch.setenv(faults.ENV_SWITCH, "test")
    with pytest.raises(ValueError):
        faults.fault("no.such.point")
    assert kills == []


def _python(code: str, **extra: str) -> subprocess.CompletedProcess:
    """Run `code` in a fresh interpreter whose SAATHI_* variables are exactly `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAATHI_")}
    env.update(extra, PYTHONPATH=str(HERE.parent / "src"))
    return subprocess.run(
        [sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, timeout=60, check=False
    )


@pytest.mark.parametrize("extra", [{}, {"SAATHI_ENV": "prod"}])
def test_importing_the_module_never_kills_and_a_named_point_alone_never_arms_a_process(extra: dict[str, str]):
    """A fresh interpreter with the point set and no switch imports the module, calls every hook and lives."""
    code = "import saathi.faults as f\nfor p in sorted(f.POINTS):\n    f.fault(p)\nprint('alive')"
    done = _python(code, SAATHI_FAULT_POINT="edge.before_commit", **extra)
    assert (done.returncode, done.stdout.strip()) == (0, "alive")


def test_a_process_with_the_switch_the_arm_file_and_the_point_dies_by_sigkill(arm_file: Path):
    code = "import saathi.faults as f\nf.fault('edge.before_commit')"
    done = _python(
        code, SAATHI_ENV="test", SAATHI_FAULT_POINT="edge.before_commit", SAATHI_FAULT_ARM_FILE=str(arm_file)
    )
    assert done.returncode == -signal.SIGKILL


def test_a_process_with_the_switch_and_the_point_but_no_arm_file_lives(tmp_path: Path):
    code = "import saathi.faults as f\nfor p in sorted(f.POINTS):\n    f.fault(p)\nprint('alive')"
    for extra in ({}, {"SAATHI_FAULT_ARM_FILE": str(tmp_path / "missing")}):
        done = _python(code, SAATHI_ENV="test", SAATHI_FAULT_POINT="edge.before_commit", **extra)
        assert (done.returncode, done.stdout.strip()) == (0, "alive")


def test_compose_pins_the_environment_name_without_interpolation():
    """R16-1: an operator `.env` must not be able to turn the hooks on, so no service takes its name from it."""
    text = (HERE.parent / "infra" / "docker-compose.yml").read_text()
    lines = [ln.strip() for ln in text.splitlines() if "SAATHI_ENV" in ln]
    assert lines and all(ln.startswith("SAATHI_ENV: prod") for ln in lines), lines
    assert not any("${SAATHI_ENV" in ln for ln in lines)
    assert "SAATHI_FAULT" not in text  # and no service is given a point or an arm file


# --- the processes ---------------------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Rig:
    """One household on a cloned database, the paths the subprocesses share, and helpers to start them."""

    def __init__(self, db: PgDb, tmp: Path) -> None:
        self.db = db
        self.tmp = tmp
        self.journal = tmp / "journal.jsonl"
        self.key_file = tmp / "sender.key"
        self.key_file.write_bytes(SENDER_KEY)
        self.arm_file = tmp / "fault.arm"  # the second guard of the hooks: only the rig creates it
        self.arm_file.write_text("")
        self.procs: list[subprocess.Popen] = []
        self.ring = KeyRing(KEK)

    def env(self, point: str | None, port: int | None = None) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if not k.startswith("SAATHI_") and not k.startswith("CRASH_")}
        env = {
            **base,
            "SAATHI_DB_URL_EDGE": self.db.dsn("saathi_edge"),
            "SAATHI_DB_URL_CORE": self.db.dsn("saathi_core"),
            "SAATHI_WA_APP_SECRET": WA_SECRET,
            "SAATHI_WA_WEBHOOK_PATH": WA_PATH,
            "SAATHI_SENDER_HMAC_KEY_FILE": str(self.key_file),
            "CRASH_JOURNAL": str(self.journal),
            "CRASH_HOUSEHOLD": H,
            "CRASH_KEK_HEX": KEK.hex(),
            "SAATHI_APPROVAL_SECRET": "synthetic-approval-secret-0123456789",  # the real worker entry requires one
            "SAATHI_MEDIA_DIR": str(self.tmp / "media"),
        }
        if port is not None:
            env["SAATHI_EDGE_PORT"] = str(port)
        if point is not None:
            env["SAATHI_ENV"] = "test"
            env["SAATHI_FAULT_POINT"] = point
            env["SAATHI_FAULT_ARM_FILE"] = str(self.arm_file)
        return env

    async def provision(self) -> bytes:
        await provision(self.db, H, keys=self.ring)
        su = await asyncpg.connect(self.db.dsn())
        try:  # the real edge hashes with its own key: point the elder's identity at it
            await su.execute(
                "UPDATE elder_identities SET sender_hmac = $1 WHERE household_id = $2 AND channel = 'whatsapp'",
                sender_hmac(SENDER_KEY, "whatsapp", ELDER_NUMBER), H,
            )  # fmt: skip
        finally:
            await su.close()
        return wa_body(wa_msg(mid="wamid.crash1", text={"body": TEXT}))

    def spawn(self, *args: str, point: str | None, port: int | None = None) -> subprocess.Popen:
        err = (self.tmp / f"proc{len(self.procs)}.err").open("w")
        proc = subprocess.Popen(
            [sys.executable, str(PROC), *args], env=self.env(point, port), cwd=self.tmp, stdout=subprocess.DEVNULL,
            stderr=err,
        )  # fmt: skip
        self.procs.append(proc)
        return proc

    @contextmanager
    def edge(self, point: str | None = None) -> Iterator[tuple[subprocess.Popen, str]]:
        port = _free_port()
        proc = self.spawn("edge", point=point, port=port)
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"edge exited early with {proc.returncode}")
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("edge did not listen in time")
        try:
            yield proc, f"http://127.0.0.1:{port}/wa/{WA_PATH}"
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)

    def run_alerts(self, point: str | None = None) -> int:
        return self.spawn("alerts", point=point).wait(timeout=WAIT_S + 20)

    def run_worker(self, point: str | None = None) -> int:
        return self.spawn("worker", point=point).wait(timeout=WAIT_S + 20)

    def cleanup(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=10)

    async def fetch(self, sql: str, *args):
        su = await asyncpg.connect(self.db.dsn())
        try:
            return await su.fetch(sql, *args)
        finally:
            await su.close()

    def _journal(self) -> list[dict]:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in self.journal.read_text().splitlines() if line]

    def effects(self) -> list[dict]:
        """The sends that reached a fake platform."""
        return [e for e in self._journal() if e.get("outcome", "sent") == "sent"]

    def refusals(self) -> list[dict]:
        """The sends a fake platform refused as repeats of a key it had already sent (R16-2)."""
        return [e for e in self._journal() if e.get("outcome") == "refused"]

    async def decrypted(self, inbox: str, part: str) -> str:
        async with open_store(self.db, Role.CORE) as store, store.tx(household=HouseholdId(H)) as tx:
            rows = await tx.fetch(
                "SELECT body_sealed, key_id FROM raw_text WHERE inbox_id = $1 AND part = $2", inbox, part
            )
            assert len(rows) == 1
            blob = await self.ring.open(
                tx, bytes(rows[0]["body_sealed"]), str(rows[0]["key_id"]), aad=aad(H, inbox, part)
            )
        return blob.decode()


@pytest.fixture
def rig(pg_db: PgDb, tmp_path: Path) -> Iterator[Rig]:
    r = Rig(pg_db, tmp_path)
    yield r
    r.cleanup()


def deliver(url: str, body: bytes) -> httpx.Response:
    return httpx.post(url, content=body, headers=wa_sign(body), timeout=20)


def _count(keys: list[str], inbox: str, effect: str) -> int:
    """Keys of one effect: the reply sender adds the form of the reply (text or clip) after the effect name."""
    return sum(1 for k in keys if k.startswith(f"{inbox}:{effect}:"))


def assert_refused_retry_of_summary(r: Rig, inbox: str | None = None) -> None:
    """R16-2: the restart really sent the summary again with its key, and the platform refused the repeat. Without the
    refusal in the journal, `len(keys) == len(set(keys))` would hold for a worker that never retried at all."""
    refused = [e["key"] for e in r.refusals()]
    summary = [k for k in refused if ":summary:" in k and (inbox is None or k.startswith(f"{inbox}:"))]
    assert len(summary) == 1, refused
    sent = [e["key"] for e in r.effects()]
    assert summary[0] in sent and sent.count(summary[0]) == 1  # sent once before the kill, refused once after
    assert all(k in sent for k in refused)  # a refusal is always of a key that had gone out


async def assert_one_message_fully_handled(r: Rig) -> str:
    """The end state after every kill: one row, its text, one request, one summary, one reply, no card, no dupes."""
    inbox = await r.fetch("SELECT id, state, acked_at FROM inbox")
    assert len(inbox) == 1, "a lost or doubled message"
    iid = str(inbox[0]["id"])
    assert inbox[0]["state"] == "processed"
    assert await r.decrypted(iid, "text") == TEXT
    req = await r.fetch("SELECT id, state, state_reason FROM requests")
    assert [(x["id"], x["state"], x["state_reason"]) for x in req] == [(f"r_{iid}", "handoff", "summary_only")]
    effects = r.effects()
    keys = [e["key"] for e in effects]
    assert len(keys) == len(set(keys)), "a second send with a key already sent reached the platform"
    assert keys.count(str(idem_key(iid, "summary", "ap1h1"))) == 1  # the one caregiver summary
    assert _count(keys, iid, "reply") == 1  # the one reply to the elder
    assert _count(keys, iid, "ack") == 1  # the one acknowledgement
    assert [e for e in effects if e["to"] == "caregiver"] == [e for e in effects if e["method"] == "send_card"]
    assert len([e for e in effects if e["to"] == "caregiver"]) == 1
    assert len(effects) == 3  # summary, reply, acknowledgement and nothing else
    assert inbox[0]["acked_at"] is not None
    assert await r.fetch("SELECT 1 FROM alert_events") == []  # a plain message opens no alert
    return iid


@pytest.mark.parametrize("run", range(1, 21), ids=lambda n: f"run {n}")
async def test_kill_after_200_one_ack_one_request_one_summary(rig: Rig, run: int):
    body = await rig.provision()
    with rig.edge() as (edge, url):
        assert deliver(url, body).status_code == 200
        edge.send_signal(signal.SIGKILL)  # the edge dies right after its 200
        assert edge.wait(timeout=10) == -signal.SIGKILL
    assert rig.run_worker("worker.before_settle") == -signal.SIGKILL  # the worker dies with every effect sent
    assert [e["method"] for e in rig.effects()].count("send_card") == 1
    assert rig.refusals() == []  # nothing has been repeated yet
    assert rig.run_worker() == 0  # both restarted; the worker runs to quiescence
    await assert_one_message_fully_handled(rig)
    assert_refused_retry_of_summary(rig)


async def test_kill_before_commit_retry_inserts_once(rig: Rig):
    body = await rig.provision()
    with rig.edge("edge.before_commit") as (edge, url):
        with pytest.raises(httpx.TransportError):  # no 200 leaves: the platform sees a dropped connection
            deliver(url, body)
        assert edge.wait(timeout=10) == -signal.SIGKILL
    assert await rig.fetch("SELECT 1 FROM inbox") == []
    assert await rig.fetch("SELECT 1 FROM raw_text") == []
    with rig.edge() as (_edge, url):  # restarted: the platform delivers again
        assert deliver(url, body).status_code == 200
    assert len(await rig.fetch("SELECT 1 FROM inbox")) == 1
    assert len(await rig.fetch("SELECT 1 FROM raw_text")) == 1
    assert rig.run_worker() == 0
    await assert_one_message_fully_handled(rig)


async def test_text_in_raw_text_after_kill(rig: Rig):
    body = await rig.provision()
    with rig.edge("edge.after_commit") as (edge, url):  # committed, then killed before the 200
        with pytest.raises(httpx.TransportError):
            deliver(url, body)
        assert edge.wait(timeout=10) == -signal.SIGKILL
    rows = await rig.fetch("SELECT id FROM inbox")
    assert len(rows) == 1  # the commit came first: nothing is lost
    assert await rig.decrypted(str(rows[0]["id"]), "text") == TEXT


async def test_replay_creates_nothing(rig: Rig):
    body = await rig.provision()
    with rig.edge("edge.after_commit") as (edge, url):
        with pytest.raises(httpx.TransportError):
            deliver(url, body)
        assert edge.wait(timeout=10) == -signal.SIGKILL
    before = await rig.fetch("SELECT id FROM inbox")
    assert len(before) == 1
    with rig.edge() as (_edge, url):  # the platform did not get a 200, so it delivers again
        assert deliver(url, body).status_code == 200
        assert deliver(url, body).status_code == 200  # and again: still answered
    assert [r["id"] for r in await rig.fetch("SELECT id FROM inbox")] == [r["id"] for r in before]
    assert len(await rig.fetch("SELECT 1 FROM raw_text")) == 1
    assert rig.run_worker() == 0
    assert len(await rig.fetch("SELECT 1 FROM requests")) == 1
    await assert_one_message_fully_handled(rig)


async def test_worker_kill_before_settle_effects_once(rig: Rig):
    await rig.provision()
    await add_inbox(rig.db, rig.ring, await _public(rig), H, "m1", text=TEXT)
    assert rig.run_worker("worker.before_settle") == -signal.SIGKILL
    sent = rig.effects()
    assert {e["method"] for e in sent} >= {"send_card", "send_text"}  # the effects went out before the kill
    assert (await rig.fetch("SELECT state FROM inbox"))[0]["state"] == "leased"  # and the row is not settled
    assert rig.run_worker() == 0  # the restart repeats the sends with the same keys
    assert_refused_retry_of_summary(rig, "m1")
    keys = [e["key"] for e in rig.effects()]
    assert len(keys) == len(set(keys))
    assert keys.count(str(idem_key("m1", "summary", "ap1h1"))) == 1
    assert _count(keys, "m1", "reply") == 1
    assert (await rig.fetch("SELECT state FROM inbox"))[0]["state"] == "processed"
    assert len(await rig.fetch("SELECT 1 FROM requests")) == 1


async def _public(r: Rig) -> bytes:
    rows = await r.fetch("SELECT public_key FROM control.household_keys WHERE household_id = $1", H)
    return bytes(rows[0]["public_key"])


async def seed_alert(r: Rig) -> tuple[str, list[str]]:
    """A household with two approvers and an open emergency event whose first send is due now."""
    await r.provision()
    su = await asyncpg.connect(r.db.dsn())
    try:
        await add_approver(su, H, "ap1h1", 2001)
        await add_approver(su, H, "ap2h1", 2002)
        await set_approvers(su, H, "ap1h1", "ap2h1")
        event = await open_event(su, H, "m1")
    finally:
        await su.close()
    return event, [f"alert:{event}:a:{a}:1" for a in ("ap1h1", "ap2h1")]


async def test_kill_between_alert_send_and_mark_repeats_the_key(rig: Rig):
    """LT-19: the alert service dies with its sends out and nothing marked. The restart sends the same keys again,
    the platform refuses them as repeats, and the attempts end marked: the humans and the elder were told once."""
    event, keys = await seed_alert(rig)
    assert rig.run_alerts("alert.between_send_and_mark") == -signal.SIGKILL
    sent_keys = [e["key"] for e in rig.effects()]
    assert all(k in sent_keys for k in keys)  # the t0 card of each approver went out before the kill
    assert {r["status"] for r in await rig.fetch("SELECT status FROM alert_attempts")} == {"due"}  # nothing marked
    assert rig.refusals() == []
    assert rig.run_alerts() == 0
    refused = [e["key"] for e in rig.refusals()]
    for key in keys:
        assert key in refused  # the restart repeated the very same key, and the platform refused it (R16-2)
    assert all(k in sent_keys for k in refused)  # nothing was refused that had not gone out first
    after = [e["key"] for e in rig.effects()]
    assert after[: len(sent_keys)] == sent_keys and len(after) == len(set(after))  # nothing was sent twice
    elder = [e["method"] for e in rig.effects() if e["to"] == "elder"]
    assert elder == ["send_audio", "send_text"]  # the elder heard the clip once, and the restart did not repeat it
    attempts = await rig.fetch("SELECT idem_key, status FROM alert_attempts WHERE idem_key = ANY($1)", keys)
    assert {a["idem_key"]: a["status"] for a in attempts} == {k: "delivered" for k in keys}
    assert [r["state"] for r in await rig.fetch("SELECT state FROM alert_events WHERE id = $1", event)] == ["open"]
