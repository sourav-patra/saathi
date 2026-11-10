"""LT-50: the elder bot has no authority (M07, M09; RQ-105, RQ-106; TQ-67, TQ-68).

A callback update and the text "approved" arrive on the elder bot's webhook. Neither creates an approval, a token or a
commit, and the elder-side modules hold no symbol that could. Rows are checked as the superuser; the edge runs as its
own role. Needs SAATHI_TEST_DSN for the row tests.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from conftest import PgDb
from edge_helpers import pg_edge, seed_edge_household, su_fetch, tg_body, tg_headers, tg_url

SRC = Path(__file__).resolve().parent.parent / "src" / "saathi"
AUTHORITY_TABLES = ("approvals", "commits", "quotes", "requests")


async def authority_rows(db: PgDb) -> dict[str, int]:
    return {t: len(await su_fetch(db, f"SELECT 1 FROM {t}")) for t in AUTHORITY_TABLES}


def callback_update(data: str) -> bytes:
    sender = {"id": 4242, "is_bot": False}
    cb = {"id": "9", "from": sender, "data": data, "message": {"message_id": 5, "chat": {"id": 4242}}}
    return json.dumps({"update_id": 7, "callback_query": cb}).encode()


async def test_callback_update_from_elder_bot_creates_no_approval_token_or_commit(pg_db: PgDb):
    await seed_edge_household(pg_db)
    before = await authority_rows(pg_db)
    async with pg_edge(pg_db) as c:
        for data in ("approve:q1", "a:q1:123456", "commit:q1"):
            r = await c.post(tg_url(), content=callback_update(data), headers=tg_headers())
            assert r.status_code == 200 and r.content == b""  # no reply, nothing said back
    assert await authority_rows(pg_db) == before == dict.fromkeys(AUTHORITY_TABLES, 0)
    assert await su_fetch(pg_db, "SELECT 1 FROM inbox") == []  # a callback is not even a message
    assert await su_fetch(pg_db, "SELECT 1 FROM audit_log") == []


async def test_approved_text_creates_no_approval_token_or_commit(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        for n, text in enumerate(("approved", "haan approve kar do", "/approve q1"), start=1):
            r = await c.post(tg_url(), content=tg_body(text, message_id=n), headers=tg_headers())
            assert r.status_code == 200
    assert await authority_rows(pg_db) == dict.fromkeys(AUTHORITY_TABLES, 0)
    rows = await su_fetch(pg_db, "SELECT kind, source, state FROM inbox")
    assert len(rows) == 3 and {(r["kind"], r["source"], r["state"]) for r in rows} == {
        ("text", "telegram_elder", "received")
    }  # it is an ordinary message for the caregiver, with no power


async def test_forged_callback_data_ignored_and_counted(pg_db: PgDb):
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        counters = c.app.state.edge.counters  # type: ignore[attr-defined]
        for data in ("approve:q1:forged", "\x00\xff", "A" * 5000):
            r = await c.post(tg_url(), content=callback_update(data), headers=tg_headers())
            assert r.status_code == 200
        bad = await c.post(tg_url(), content=callback_update("approve:q1"), headers=tg_headers("wrong"))
        assert bad.status_code == 401  # a forged call without the webhook secret never reaches the parser
        assert counters.get("elder_callback_ignored") == 3
        assert counters.get("bad_signature") == 1
    assert await authority_rows(pg_db) == dict.fromkeys(AUTHORITY_TABLES, 0)


async def test_registered_approver_id_on_the_elder_bot_gets_no_approval(pg_db: PgDb):
    """An approver's Telegram id used on the elder bot is an unknown chat there: counted, no reply, no authority."""
    await seed_edge_household(pg_db)
    async with pg_edge(pg_db) as c:
        r = await c.post(tg_url(), content=tg_body("approved", chat="777001"), headers=tg_headers())
        assert r.status_code == 200 and r.content == b""
    assert len(await su_fetch(pg_db, "SELECT 1 FROM rejected_inbound")) == 1
    assert await su_fetch(pg_db, "SELECT 1 FROM inbox") == []
    assert await authority_rows(pg_db) == dict.fromkeys(AUTHORITY_TABLES, 0)


# Names that carry approval or commit authority. No elder-side module may import or define one.
AUTHORITY_WORDS = (
    "approval_token", "mint", "authorize_commit", "place_order", "commit_intent", "PolicyEngine", "approve",
)  # fmt: skip
ELDER_SIDE = [SRC / "channels" / "telegram_elder.py", *sorted((SRC / "edge").glob("*.py"))]


def _symbols(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names} | {(a.asname or "") for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {node.module or ""} | {a.name for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_elder_modules_import_no_token_or_commit_symbol():
    assert ELDER_SIDE and all(p.exists() for p in ELDER_SIDE)
    for path in ELDER_SIDE:
        names = _symbols(path)
        for word in AUTHORITY_WORDS:
            hits = {n for n in names if word.lower() in n.lower()}
            assert not hits, f"{path.name} holds {sorted(hits)}"
        assert not {n for n in names if n.startswith("saathi.policy")}, path.name
        caregiver = {n for n in names if n.startswith("saathi.channels.telegram") and not n.endswith("_elder")}
        assert not caregiver, path.name  # the caregiver bot module is not reachable from the elder side


def test_the_elder_adapter_has_no_callback_handler():
    text = (SRC / "edge" / "adapters.py").read_text()
    assert "callback_query" in text  # named once, to count it
    tree = ast.parse(text)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and "callback" in n.name.lower()]
