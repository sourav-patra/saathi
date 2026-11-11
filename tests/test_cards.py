"""LT-24: the caregiver bot (M15; RQ-97, RQ-18; TQ-15, 53).

Cards hold slots only. A tap is verified against the household's approver list, acts once, and a repeat changes
nothing. "Show raw" needs consent and is audited. The Telegram offset moves only after a tap was handled. Postgres
tests need SAATHI_TEST_DSN; the rest run without it. Synthetic values only.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import Update
from alert_helpers import AlertRig, B, H, P, make_rig, open_store
from conftest import PgDb
from fakes import FakeAudit, FakeCaregiverChannel, FakeClock
from store_helpers import NOW
from worker_helpers import add_inbox, grant

from saathi.alerts.stages import alert_card as stages_alert_card
from saathi.audit import PgAuditSink
from saathi.channels import telegram as tg
from saathi.channels.telegram import (
    CallbackRouter,
    PgApproverDirectory,
    RoutedUpdate,
    TelegramCaregiverChannel,
    alert_card,
    show_raw,
    summary_card,
)
from saathi.config import ConfigError, Settings
from saathi.domain import AlertEventRef, Card, ChannelHealth, MediaKind, SummarySlots
from saathi.errors import AuditUnavailable, ChannelDown, RateLimited, SendRejected
from saathi.ids import ApproverId, EventId, HouseholdId, IdemKey, InboxId, PublicKey
from saathi.store import Role
from saathi.store.keys import generate_secret
from saathi.worker.summaries import summary_slots

HH = HouseholdId(H)
ROOT = Path(__file__).resolve().parent.parent
CANARY = "synthetic canary words of the elder"
PRIMARY_TG, BACKUP_TG, STRANGER_TG = 3001, 3002, 9999  # the telegram ids `alert_helpers` registers


# --- a stand-in for the Bot API client -------------------------------------------------------------------------------
class FakeBot:
    """The methods of an aiogram `Bot` that the channel calls. `batches` are what `get_updates` returns, in turn."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answers: list[tuple[str, str]] = []
        self.offsets: list[int | None] = []
        self.batches: list[list[Update]] = []
        self.fail: Exception | None = None

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> SimpleNamespace:
        if self.fail:
            raise self.fail
        self.sent.append({"chat": chat_id, "text": text, **kw})
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, text: str, **kw: Any) -> None:
        self.sent.append({"edit": text, **kw})

    async def send_voice(self, chat_id: int, voice: Any, **kw: Any) -> SimpleNamespace:
        self.sent.append({"chat": chat_id, "voice": voice.data, **kw})
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def send_audio(self, chat_id: int, audio: Any, **kw: Any) -> SimpleNamespace:
        self.sent.append({"chat": chat_id, "audio": audio.data, **kw})
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def answer_callback_query(self, callback_query_id: str, **kw: Any) -> None:
        self.answers.append((callback_query_id, kw.get("text", "")))

    async def get_updates(self, **kw: Any) -> list[Update]:
        self.offsets.append(kw.get("offset"))
        if self.fail:
            raise self.fail
        await asyncio.sleep(0)  # a real long poll waits; an empty answer must still give the loop up
        return self.batches.pop(0) if self.batches else []

    async def get_me(self) -> None:
        if self.fail:
            raise self.fail


def tap(update_id: int, user: int, data: str) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"cb{update_id}", "chat_instance": "ci", "data": data,
                "from": {"id": user, "is_bot": False, "first_name": "Caregiver"},
                "message": {
                    "message_id": 7, "date": 1767268800, "chat": {"id": user, "type": "private"},
                    "from": {"id": 1, "is_bot": True, "first_name": "Bot"}, "text": "card",
                },
            },
        }
    )  # fmt: skip


def say(update_id: int, user: int, text: str, chat_type: str = "private") -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id, "date": 1767268800, "text": text,
                "chat": {"id": user, "type": chat_type},
                "from": {"id": user, "is_bot": False, "first_name": "Caregiver"},
            },
        }
    )  # fmt: skip


class Directory:
    def __init__(self) -> None:
        self.known = {PRIMARY_TG: (HH, ApproverId(P)), BACKUP_TG: (HH, ApproverId(B))}

    async def resolve(self, telegram_id: int) -> tuple[HouseholdId, ApproverId] | None:
        return self.known.get(telegram_id)

    async def chat_of(self, approver: ApproverId) -> int:
        for tid, (_, who) in self.known.items():
            if who == approver:
                return tid
        raise KeyError(approver)


def channel(bot: FakeBot | None = None) -> tuple[TelegramCaregiverChannel, FakeBot]:
    bot = bot or FakeBot()
    return TelegramCaregiverChannel(bot, Directory(), FakeClock(NOW)), bot  # type: ignore[arg-type]


def routed(update_id: int, data: str, who: str = P, kind: str = "callback") -> RoutedUpdate:
    return RoutedUpdate(update_id=update_id, approver_id=ApproverId(who), kind=kind, data=data, household_id=HH)  # type: ignore[arg-type]


# --- cards -----------------------------------------------------------------------------------------------------------
def test_alert_card_slots_only():
    assert alert_card is stages_alert_card  # one card builder for alerts (WU-17 ruling 5)
    for kind in ("emergency", "scam", "unscanned"):
        card = alert_card(AlertEventRef(household_id=HH, event_id=EventId("al_m1_" + kind), kind=kind))  # type: ignore[arg-type]
        assert [b.data for b in card.buttons] == [f"ack:al_m1_{kind}", f"fa:al_m1_{kind}"]
        assert [b.text for b in card.buttons] == ["Acknowledge", "False alarm"]
        assert all(len(b.data.encode()) <= tg.CALLBACK_DATA_LIMIT for b in card.buttons)
        assert set(Card.model_fields) == {"title", "lines", "buttons"}
        text = " ".join([card.title, *card.lines])
        assert CANARY not in text and not re.search(r"\d{6,}", text)  # no transcript, no number


def test_summary_card_buttons():
    slots = summary_slots(MediaKind.AUDIO, ["RD-SCR-1"], "ride", scam=True, transcribed=True)
    card = summary_card(slots, InboxId("m1"))
    assert [(b.text, b.data) for b in card.buttons] == [("Show raw", "raw:m1")]
    assert card.lines == [*card.lines] and "Rules: RD-SCR-1" in card.lines
    for flag in ("consent_missing", "unsupported"):  # nothing was stored, so no button
        assert summary_card(summary_slots(MediaKind.TEXT, [], "unknown", flag=flag), InboxId("m1")).buttons == []  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # a slot cannot hold words
        SummarySlots(kind=MediaKind.TEXT, transcript=CANARY)  # type: ignore[call-arg]


def test_a_button_that_telegram_would_refuse_is_not_sent_quietly():
    too_long = Card(title="t", lines=[], buttons=[tg.Button(text="x", data="ack:" + "e" * 70)])
    with pytest.raises(SendRejected):
        tg._markup(too_long)


# --- the channel -----------------------------------------------------------------------------------------------------
async def test_send_card_renders_html_buttons_and_dedupes_by_key():
    ch, bot = channel()
    card = alert_card(AlertEventRef(household_id=HH, event_id=EventId("al_m1_emergency"), kind="emergency"))
    ref = await ch.send_card(ApproverId(P), card, key=IdemKey("k1"))
    assert ref.approver_id == P and ref.message_ref == f"{PRIMARY_TG}:101"
    (sent,) = bot.sent
    assert sent["chat"] == PRIMARY_TG and sent["parse_mode"] == "HTML" and "<b>Emergency alert</b>" in sent["text"]
    assert [b.callback_data for row in sent["reply_markup"].inline_keyboard for b in row] == [
        "ack:al_m1_emergency", "fa:al_m1_emergency",
    ]  # fmt: skip
    with pytest.raises(SendRejected) as dup:  # a repeat of a key is refused, like the fake does
        await ch.send_card(ApproverId(P), card, key=IdemKey("k1"))
    assert dup.value.code == "duplicate_key" and len(bot.sent) == 1
    with pytest.raises(SendRejected) as unknown:
        await ch.send_text(ApproverId("nobody"), "hi", key=IdemKey("k2"))
    assert unknown.value.code == "unknown_approver"


async def test_send_errors_map_to_the_port_errors_and_a_failed_send_records_no_key():
    ch, bot = channel()
    bot.fail = TelegramRetryAfter(method=None, message="slow", retry_after=3)  # type: ignore[arg-type]
    with pytest.raises(RateLimited) as slow:
        await ch.send_text(ApproverId(P), "hi", key=IdemKey("k1"))
    assert slow.value.retry_after == 3
    bot.fail = TelegramNetworkError(method=None, message="down")  # type: ignore[arg-type]
    with pytest.raises(ChannelDown):
        await ch.send_text(ApproverId(P), "hi", key=IdemKey("k1"))
    assert await ch.health() is ChannelHealth.DOWN
    bot.fail = None
    await ch.send_text(ApproverId(P), "hi", key=IdemKey("k1"))  # the failed tries did not use the key up
    assert await ch.health() is ChannelHealth.UP


async def test_audio_goes_as_a_voice_note_with_its_caption():
    from saathi.domain import AudioBlob

    ch, bot = channel()
    await ch.send_audio(ApproverId(P), AudioBlob(data=b"synthetic-opus"), "caption", key=IdemKey("a1"))
    assert bot.sent[0]["voice"] == b"synthetic-opus" and bot.sent[0]["caption"] == "caption"


async def test_unregistered_chat_counted_not_acted():
    ch, bot = channel()
    bot.batches = [
        [tap(1, STRANGER_TG, "ack:al_m1_emergency"), say(2, STRANGER_TG, "approved"), say(3, PRIMARY_TG, "hi")]
    ]
    got = ch.updates()
    first = await anext(got)
    assert (first.update_id, first.approver_id, first.kind) == (3, P, "text")  # only the registered one is yielded
    assert ch.dropped == 2
    assert bot.sent == [] and bot.answers == []  # nothing was sent back to the stranger
    await got.aclose()


async def test_group_chats_and_botless_updates_are_dropped():
    ch, bot = channel()
    bot.batches = [[say(1, PRIMARY_TG, "x", chat_type="supergroup"), Update.model_validate({"update_id": 2})]]
    bot.batches.append([say(3, BACKUP_TG, "ok")])
    got = ch.updates()
    assert (await anext(got)).update_id == 3 and ch.dropped == 2
    await got.aclose()


async def test_offset_advances_after_commit():
    """The offset moves past an update only when the caller comes back for the next one."""
    ch, bot = channel()
    bot.batches = [[tap(10, PRIMARY_TG, "ack:al_m1_emergency"), tap(11, BACKUP_TG, "ack:al_m1_emergency")], []]
    got = ch.updates()
    first = await anext(got)
    assert first.update_id == 10 and ch._offset is None  # handing it out moves nothing
    second = await anext(got)  # the caller finished the first one
    assert second.update_id == 11 and ch._offset == 11
    await got.aclose()  # a caller that stops before finishing the second leaves the offset at 11
    assert ch._offset == 11
    bot.batches = [[]]
    got = ch.updates()
    task = asyncio.ensure_future(anext(got))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert bot.offsets[-1] == 11  # the next poll asks for 11: Telegram sends it again


# --- the router, against Postgres ------------------------------------------------------------------------------------
async def audit_rows(rig: AlertRig, event: str | None = None) -> list[asyncpg.Record]:
    return await rig.sql(
        "SELECT actor, event, detail FROM audit_log WHERE ($1::text IS NULL OR event = $1) ORDER BY seq", event
    )


async def wired(rig: AlertRig, store: Any, *, show_raw_consent: bool = True, text: str | None = CANARY):
    su = await asyncpg.connect(rig.db.dsn())
    try:
        wrapped = rig.ring.wrap(generate_secret(), household=HH, purpose="audit", key_id="a1")
        await su.execute(
            "INSERT INTO control.household_keys (household_id, purpose, key_id, wrapped_key)"
            " VALUES ($1, 'audit', 'a1', $2)",
            H, wrapped,
        )  # fmt: skip
        if show_raw_consent:
            await grant(su, H, "show_raw", "telegram_caregiver")
    finally:
        await su.close()
    if text is not None:
        await add_inbox(rig.db, rig.ring, PublicKey(rig.pub), H, "m1", text=text, state="processed")
    audit = PgAuditSink(store, rig.ring)
    bot_channel = FakeCaregiverChannel()
    router = CallbackRouter(store, rig.service(store), rig.ring, audit, bot_channel)
    return router, bot_channel


@pytest.fixture
async def rig(pg_db: PgDb) -> AlertRig:
    return await make_rig(pg_db)


async def test_show_raw_reads_table_and_audits(rig: AlertRig):
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, out = await wired(rig, store)
        await router.handle(routed(50, "raw:m1"))
        assert [(m, to, p) for m, to, p, _ in out.sent] == [("send_text", P, CANARY)]
        (row,) = await audit_rows(rig, "show_raw")
        assert row["actor"] == f"approver:{P}"
        assert CANARY not in str(dict(row))  # the audit row holds no elder words
        await router.handle(routed(50, "raw:m1"))  # the same update again: nothing is shown or audited twice
        assert len(out.sent) == 1 and len(await audit_rows(rig, "show_raw")) == 1
        await router.handle(routed(51, "raw:m1", who=B))  # a second approver, a second reading, a second row
        assert len(out.sent) == 2 and len(await audit_rows(rig, "show_raw")) == 2


async def test_show_raw_denied_without_consent(rig: AlertRig):
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, out = await wired(rig, store, show_raw_consent=False)
        await router.handle(routed(60, "raw:m1"))
        ((_, _, sent, _),) = out.sent
        assert sent == tg.SAY_NO_CONSENT and CANARY not in str(sent)
        assert [r["event"] for r in await audit_rows(rig)] == ["consent_denied"]
        assert await audit_rows(rig, "show_raw") == []


async def test_withdrawn_consent_closes_show_raw(rig: AlertRig):
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, out = await wired(rig, store)
        su = await asyncpg.connect(rig.db.dsn())
        try:
            await grant(su, H, "show_raw", "telegram_caregiver", event="withdraw")
        finally:
            await su.close()
        await router.handle(routed(61, "raw:m1"))
        assert out.sent[0][2] == tg.SAY_NO_CONSENT


async def test_show_raw_fails_closed_when_the_audit_is_down(rig: AlertRig):
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        await wired(rig, store)
        audit = FakeAudit()
        audit.fail("append", AuditUnavailable("down"))
        with pytest.raises(AuditUnavailable):
            await show_raw(store, rig.ring, audit, routed(62, "raw:m1"), InboxId("m1"))


async def test_show_raw_for_an_item_with_no_text_and_for_an_unregistered_approver(rig: AlertRig):
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        await wired(rig, store, text=None)
        audit = PgAuditSink(store, rig.ring)
        assert await show_raw(store, rig.ring, audit, routed(63, "raw:m9"), InboxId("m9")) == tg.SAY_NO_TEXT
        stranger = routed(64, "raw:m9", who="not-in-config")
        assert await show_raw(store, rig.ring, audit, stranger, InboxId("m9")) == tg.SAY_NOT_ALLOWED
        assert [r["event"] for r in await audit_rows(rig)] == ["show_raw", "deny"]


async def test_ack_tap_feeds_the_alert_service_and_false_alarm_sets_the_flag(rig: AlertRig):
    event = await rig.event("m2", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, _ = await wired(rig, store, text=None)
        await router.handle(routed(70, f"ack:{event}", who=B))
        row = await rig.state(event)
        assert (row["state"], row["acked_by"], row["false_alarm"]) == ("acknowledged", B, False)
        await router.handle(routed(71, f"fa:{event}", who=P))
        assert (await rig.state(event))["false_alarm"] is True
        names = [str(e.event) for e in rig.audit.events]  # the alert service writes its audit rows to the rig's sink
        assert "ack_tap" in names and "false_alarm" in names


async def test_replayed_update_id_changes_nothing_and_a_late_second_tap_does_not_steal_the_ack(rig: AlertRig):
    event = await rig.event("m2", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, _ = await wired(rig, store, text=None)
        await router.handle(routed(80, f"ack:{event}", who=P))
        before = len(await audit_rows(rig))
        await router.handle(routed(80, f"ack:{event}", who=P))  # Telegram sent it again
        assert len(await audit_rows(rig)) == before
        await router.handle(routed(81, f"ack:{event}", who=B))  # another approver, later: logged, not the acker
        assert (await rig.state(event))["acked_by"] == P
        (seen,) = await rig.sql("SELECT count(*) AS n FROM tg_updates WHERE bot = 'caregiver' AND update_id = 80")
        assert seen["n"] == 1


async def test_a_tap_from_someone_not_in_the_household_list_acts_on_nothing(rig: AlertRig):
    event = await rig.event("m2", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, _ = await wired(rig, store, text=None)
        await router.handle(routed(90, f"ack:{event}", who="not-in-config"))
        assert (await rig.state(event))["state"] == "open"
        assert [r["event"] for r in await audit_rows(rig)] == ["deny"]


async def test_text_is_never_an_approval_and_unknown_buttons_are_denied(rig: AlertRig):
    event = await rig.event("m2", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, out = await wired(rig, store, text=None)
        for n, words in enumerate(["approved", "ack:" + event, "/pause", "/resume", "yes book it"], start=100):
            await router.handle(routed(n, words, kind="text"))
        await router.handle(routed(110, "approve:r1"))  # no such button exists in Phase 0
        await router.handle(routed(111, "ack:" + "x" * 80))  # not an id of ours
        assert (await rig.state(event))["state"] == "open"
        assert out.sent == []  # no reply to free text, no pause, no resume
        assert [r["event"] for r in await audit_rows(rig)] == ["deny", "deny"]


async def test_start_and_status_commands(rig: AlertRig):
    await rig.event("m2", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, out = await wired(rig, store, text=None)
        await router.handle(routed(120, "/start", kind="text"))
        await router.handle(routed(121, "/status@SaathiBot", kind="text"))
        assert out.sent[0][2] == tg.SAY_WELCOME
        assert out.sent[1][2].startswith("Open alerts: 1. Newest message from the elder:")


async def test_a_store_outage_is_not_recorded_so_the_update_comes_again(rig: AlertRig):
    event = await rig.event("m2", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, _ = await wired(rig, store, text=None)
        broken = FakeAudit()
        router._alerts = SimpleNamespace(  # type: ignore[assignment]
            acknowledge=_raise(ChannelDown()), false_alarm=_raise(ChannelDown())
        )
        with pytest.raises(ChannelDown):
            await router.handle(routed(130, f"ack:{event}"))
        assert await rig.sql("SELECT 1 FROM tg_updates WHERE update_id = 130") == []
        assert broken.events == []


def _raise(exc: Exception):
    async def boom(*a: Any, **k: Any) -> bool:
        raise exc

    return boom


async def test_pg_directory_finds_the_chat_of_an_approver(rig: AlertRig):
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        scan = SimpleNamespace(resolve_approver=_resolve)
        d = PgApproverDirectory(scan, store, _households)
        assert await d.chat_of(ApproverId(B)) == BACKUP_TG
        with pytest.raises(KeyError):
            await d.chat_of(ApproverId("nobody"))
        assert await d.resolve(PRIMARY_TG) == (HH, P) and await d.chat_of(ApproverId(P)) == PRIMARY_TG


async def _resolve(telegram_id: int) -> tuple[HouseholdId, ApproverId] | None:
    return (HH, ApproverId(P)) if telegram_id == PRIMARY_TG else None


async def _households() -> Sequence[HouseholdId]:
    return [HH]


# --- no database needed ----------------------------------------------------------------------------------------------
def test_the_in_memory_pause_the_repeat_task_and_the_ack_set_are_gone():
    src = (ROOT / "src/saathi/channels/telegram.py").read_text()
    for gone in ("RuntimeFlags", "_repeat_until_ack", "_acked", "TelegramNotifier", "saathi.legacy", "paused"):
        assert gone not in src, gone
    assert not [n for n in dir(tg) if n.lower() in {"pause", "resume"}]
    assert not (ROOT / "src/saathi/legacy.py").exists()


def test_module_holds_no_commit_path_and_no_model_call():
    src = (ROOT / "src/saathi/channels/telegram.py").read_text()
    for banned in ("place_order", "authorize_commit", "Gateway", "gateway"):
        assert banned not in src, banned


async def test_serve_checks_the_secrets_before_it_builds_anything():
    built: list[int] = []

    def build(s: Settings) -> Any:
        built.append(1)
        raise AssertionError("must not be reached")

    with pytest.raises(ConfigError):
        await tg.serve(Settings(_env_file=None, approval_secret="short"), build)  # type: ignore[arg-type]
    assert built == []


async def test_serve_waits_out_a_failure_and_asks_for_the_same_update_again():
    ch, bot = channel()
    bot.batches = [[tap(5, PRIMARY_TG, "ack:al_m1_emergency")], [tap(5, PRIMARY_TG, "ack:al_m1_emergency")]]
    stop = asyncio.Event()
    calls: list[int] = []

    class Router:
        async def handle(self, up: Any) -> None:
            calls.append(up.update_id)
            if len(calls) == 1:
                raise ChannelDown()
            stop.set()

    s = Settings(_env_file=None, approval_secret="x" * 40)  # type: ignore[arg-type]
    await asyncio.wait_for(tg.serve(s, lambda _s: (ch, Router()), backoff_s=0, stop=stop), 5)  # type: ignore[arg-type]
    assert calls == [5, 5]
    assert bot.offsets[:2] == [None, None]  # the failed one was not passed, so the poll did not move


class _Stopping:
    """Wraps a router; stops `serve` once the update with this id has been handled."""

    def __init__(self, inner: Any, stop: asyncio.Event, last: int) -> None:
        self._inner, self._stop, self._last = inner, stop, last

    async def handle(self, up: Any) -> None:
        await self._inner.handle(up)
        if up.update_id == self._last:
            self._stop.set()


async def test_an_ack_tap_behind_a_show_raw_that_keeps_failing_still_lands(rig: AlertRig):
    event = await rig.event("m2", "emergency")
    ch, bot = channel()
    batch = [tap(50, PRIMARY_TG, "raw:m1"), tap(51, BACKUP_TG, f"ack:{event}")]
    bot.batches = [list(batch), list(batch), list(batch)]
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, _ = await wired(rig, store)
        router._caregivers = SimpleNamespace(send_text=_raise(SendRejected("blocked")))  # type: ignore[assignment]
        stop = asyncio.Event()
        s = Settings(_env_file=None, approval_secret="x" * 40)  # type: ignore[arg-type]
        await asyncio.wait_for(
            tg.serve(s, lambda _s: (ch, _Stopping(router, stop, 51)), backoff_s=0, stop=stop), 10  # type: ignore[arg-type]
        )
        assert (await rig.state(event))["acked_by"] == B
        (poison,) = await audit_rows(rig, "deny")
        assert poison["actor"] == f"approver:{P}" and "poison_update" in str(poison["detail"])
        seen = await rig.sql("SELECT update_id FROM tg_updates WHERE bot = 'caregiver' ORDER BY update_id")
        assert [r["update_id"] for r in seen] == [50, 51]


async def test_a_failure_that_is_not_a_saathi_error_is_counted_and_then_passed(rig: AlertRig):
    event = await rig.event("m2", "emergency")
    async with open_store(rig.db, Role.CORE, clock=rig.clock) as store:
        router, _ = await wired(rig, store, text=None)
        router._alerts = SimpleNamespace(  # type: ignore[assignment]
            acknowledge=_raise(RuntimeError("bad key")), false_alarm=_raise(RuntimeError("bad key"))
        )
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await router.handle(routed(140, f"ack:{event}"))
        assert await rig.sql("SELECT 1 FROM tg_updates WHERE update_id = 140") == []
        await router.handle(routed(140, f"ack:{event}"))  # the third failure: audited, recorded, passed
        assert len(await audit_rows(rig, "deny")) == 1
        assert len(await rig.sql("SELECT 1 FROM tg_updates WHERE update_id = 140")) == 1


async def test_serve_does_not_crash_on_an_exception_that_is_not_a_saathi_error():
    ch, bot = channel()
    bot.batches = [[tap(5, PRIMARY_TG, "ack:al_m1_emergency")], [tap(5, PRIMARY_TG, "ack:al_m1_emergency")]]
    stop = asyncio.Event()
    calls: list[int] = []

    class Router:
        async def handle(self, up: Any) -> None:
            calls.append(up.update_id)
            if len(calls) == 1:
                raise ValueError("not a Saathi error")
            stop.set()

    s = Settings(_env_file=None, approval_secret="x" * 40)  # type: ignore[arg-type]
    await asyncio.wait_for(tg.serve(s, lambda _s: (ch, Router()), backoff_s=0, stop=stop), 5)  # type: ignore[arg-type]
    assert calls == [5, 5]
