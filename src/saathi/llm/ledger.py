"""The Postgres ledger of paid model spend (M28; RQ-48).

`PgLedger` adapts the `LedgerPort` of the gateway: no transaction stays open across a model call, so `reserve`,
`settle` and `release` each run their own short transaction through the `Store`, bound to one household.

- `reserve` holds the worst-case cost of one call. It runs under a per-household advisory lock, so the month total
  and the insert are one step and parallel reservers can never pass the cap together. A cap that is unset or not
  above zero raises `BudgetExceeded`, as does a reservation that would take the month past the cap.
- `settle` records the actual cost (a `llm_spend` row) and closes the reservation; `release` closes a reservation
  of a call that was never made. Either one acts on a reservation still held. A second call, or an id of another
  household, is a caller bug and raises `ValueError`.
- The month total counts held reservations at their worst case and settled ones at their actual cost, so a restart
  loses nothing: every figure is a row.
- The 80% alert: the reservation that takes the month total across 80% of the cap reports it. `reserve_checked`
  returns it with the id. `reserve` (the port) returns only the id and hands the same alert to the `on_alert`
  callback, which the caller wires to a caregiver summary. A failing callback never fails a reservation that is
  already stored. A release can lower the total below the line again, so a later reservation may report once more.

Scope and month (config `ledger_scope`, `ledger_tz`):

- Scope `global` (the default) is one hard cap across every household, because one owner pays one bill. The
  reservation takes one fixed advisory lock for all households and sums the month through `control.ledger_month_total`,
  a security-definer function, since the model role sees one household's rows only. Scope `household` keeps one cap
  per household under that household's own lock.
- The month is the calendar month in `ledger_tz` (default India time), so it matches the billing month the owner sees.

Amounts are rounded UP to the 4 places of the columns before any check, so the figure compared with the cap is the
figure stored. A non-finite cap raises `BudgetExceeded`; a non-finite amount raises `ValueError`.

The cap is a monthly figure from config and is passed in; nothing here holds a value of it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from saathi.errors import BudgetExceeded
from saathi.ids import ApproverId, HouseholdId, RunId, idem_key
from saathi.ports import Tx
from saathi.store import Store
from saathi.store.repos._util import affected

log = logging.getLogger(__name__)

ALERT_FRACTION = Decimal("0.8")
_STEP = Decimal("0.0001")  # the scale of numeric(10,4)
DEFAULT_TZ = "Asia/Kolkata"

Scope = Literal["global", "household"]
_GLOBAL_LOCK = "saathi:ledger:global"

_TOTAL_SQL = (
    "SELECT COALESCE(SUM(CASE state WHEN 'reserved' THEN reserved_inr WHEN 'settled' THEN settled_inr ELSE 0 END), 0)"
    " AS total FROM llm_reservations WHERE household_id = $1 AND month = $2"
)


@dataclass(frozen=True)
class SpendAlert:
    """The month total crossed 80% of the cap with this reservation."""

    household: HouseholdId
    month: str
    total_inr: Decimal
    cap_inr: Decimal


@dataclass(frozen=True)
class Reservation:
    id: int
    alert: SpendAlert | None


AlertSink = Callable[[SpendAlert], Awaitable[None]]
CapSink = Callable[[HouseholdId, str], Awaitable[None]]  # (household, month) when a reservation hits the cap


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown ledger time zone {tz!r}") from exc


def month_of(when: datetime, tz: str = DEFAULT_TZ) -> str:
    """The ledger month of an instant, as ``YYYY-MM`` in the time zone `tz`."""
    return when.astimezone(_zone(tz)).strftime("%Y-%m")


def _round_up(amount: Decimal) -> Decimal:
    return amount.quantize(_STEP, rounding=ROUND_CEILING)


class PgLedger:
    """Implements `llm.gateway.LedgerPort` for one household."""

    def __init__(
        self,
        store: Store,
        household: HouseholdId,
        *,
        on_alert: AlertSink | None = None,
        on_cap: CapSink | None = None,
        scope: Scope = "global",
        tz: str = DEFAULT_TZ,
    ) -> None:
        if scope not in get_args(Scope):
            raise ValueError(f"unknown ledger scope {scope!r}")
        _zone(tz)  # an unknown zone fails here, not at the first reservation
        self._store = store
        self._household = household
        self._on_alert = on_alert
        self._on_cap = on_cap
        self._cap_month = ""
        self._scope: Scope = scope
        self._tz = tz

    async def reserve(self, run: RunId, tier: str, worst_case_inr: Decimal, cap: Decimal | None) -> int:
        try:
            got = await self.reserve_checked(run, tier, worst_case_inr, cap)
        except BudgetExceeded as exc:
            if str(exc) == "paid cap reached" and self._on_cap is not None:
                try:  # the caregiver summary at the cap (RQ-48); a failed notice never hides the refusal
                    await self._on_cap(self._household, self._cap_month)
                except Exception as sink_exc:  # noqa: BLE001
                    log.warning("cap notice failed: %s", type(sink_exc).__name__)
            raise
        if got.alert is not None and self._on_alert is not None:
            try:
                await self._on_alert(got.alert)
            except Exception as exc:  # the reservation is stored; a failed notice must not strand it
                log.warning("spend alert sink failed: %s", type(exc).__name__)  # the class only, never the text
        return got.id

    async def reserve_checked(self, run: RunId, tier: str, worst_case_inr: Decimal, cap: Decimal | None) -> Reservation:
        if cap is None or not cap.is_finite() or cap <= 0:
            raise BudgetExceeded("paid cap unset")
        if not worst_case_inr.is_finite():
            raise ValueError("a reservation must be a finite amount")
        if worst_case_inr < 0:
            raise ValueError("a reservation cannot be negative")
        worst_case_inr = _round_up(worst_case_inr)  # the figure the column stores
        async with self._store.tx(household=self._household) as tx:
            key = _GLOBAL_LOCK if self._scope == "global" else self._household
            await tx.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 2))", key)
            month = month_of(tx.now, self._tz)
            before = await self._total(tx, month)
            after = before + worst_case_inr
            if after > cap:
                self._cap_month = month
                raise BudgetExceeded("paid cap reached")
            rows = await tx.fetch(
                "INSERT INTO llm_reservations (household_id, run_id, tier, reserved_inr, month)"
                " VALUES ($1, $2, $3, $4, $5) RETURNING id",
                self._household, run, tier, worst_case_inr, month,
            )  # fmt: skip
        line = cap * ALERT_FRACTION
        crossed = before < line <= after
        alert = SpendAlert(self._household, month, after, cap) if crossed else None
        return Reservation(int(rows[0]["id"]), alert)

    async def settle(self, reservation: int, actual_inr: Decimal) -> None:
        if not actual_inr.is_finite():
            raise ValueError("a settled cost must be a finite amount")
        if actual_inr < 0:
            raise ValueError("a settled cost cannot be negative")
        actual_inr = _round_up(actual_inr)
        async with self._store.tx(household=self._household) as tx:
            rows = await tx.fetch(
                "SELECT tier FROM llm_reservations WHERE id = $1 AND state = 'reserved' FOR UPDATE", reservation
            )
            if not rows:
                raise ValueError("no open reservation with this id")
            tag = await tx.execute(
                "UPDATE llm_reservations SET state = 'settled', settled_inr = $2 WHERE id = $1 AND state = 'reserved'",
                reservation, actual_inr,
            )  # fmt: skip
            if affected(tag) != 1:
                raise ValueError("no open reservation with this id")
            # The port carries no model name, so the row keeps the tier and an empty model.
            await tx.execute(
                "INSERT INTO llm_spend (household_id, tier, model, cost_inr, reservation_id)"
                " VALUES ($1, $2, '', $3, $4)",
                self._household, rows[0]["tier"], actual_inr, reservation,
            )  # fmt: skip

    async def release(self, reservation: int) -> None:
        async with self._store.tx(household=self._household) as tx:
            tag = await tx.execute(
                "UPDATE llm_reservations SET state = 'released' WHERE id = $1 AND state = 'reserved'", reservation
            )
            if affected(tag) != 1:
                raise ValueError("no open reservation with this id")

    async def month_total(self, month: str) -> Decimal:
        async with self._store.tx(household=self._household) as tx:
            return await self._total(tx, month)

    async def _total(self, tx: Tx, month: str) -> Decimal:
        if self._scope == "global":
            rows = await tx.fetch("SELECT control.ledger_month_total($1) AS total", month)
        else:
            rows = await tx.fetch(_TOTAL_SQL, self._household, month)
        return Decimal(rows[0]["total"])


CAP_SUMMARY = (
    "Saathi: the paid helper has reached this month's limit. Until next month the elder's requests come to you as"
    " summaries only; the emergency and scam alerts are not affected."
)


class SpendNotices:
    """The ledger's two notices (RQ-48): at 80% the owner hears a short code (`on_alert`); at the cap every approver of
    the household gets one caregiver summary per month (`on_cap`). Both are best effort and hold no cap figure."""

    def __init__(
        self,
        caregivers: Any,
        approvers: Callable[[HouseholdId], Awaitable[Sequence[ApproverId]]],
        owner_alert: Callable[[str], Awaitable[None]],
    ) -> None:
        self._caregivers = caregivers
        self._approvers = approvers
        self._owner_alert = owner_alert
        self._told: set[tuple[str, str]] = set()

    async def on_alert(self, alert: SpendAlert) -> None:
        await self._owner_alert(f"spend_80pct_{alert.month}")

    async def on_cap(self, household: HouseholdId, month: str) -> None:
        if (str(household), month) in self._told:
            return
        failure: Exception | None = None
        for approver in await self._approvers(household):
            try:
                await self._caregivers.send_text(approver, CAP_SUMMARY, key=idem_key("cap", household, month, approver))
            except Exception as exc:  # noqa: BLE001  (one approver failing does not stop the others)
                failure = failure or exc
        await self._owner_alert(f"spend_cap_{month}")
        if failure is not None:
            raise failure
        self._told.add((str(household), month))


def store_approvers(store: Store) -> Callable[[HouseholdId], Awaitable[list[ApproverId]]]:
    """The approvers of one household, primary first, read under that household's scope."""

    async def approvers(household: HouseholdId) -> list[ApproverId]:
        async with store.tx(household=household) as tx:
            rows = await tx.fetch("SELECT id FROM approvers ORDER BY is_primary DESC, id")
        return [ApproverId(r["id"]) for r in rows]

    return approvers
