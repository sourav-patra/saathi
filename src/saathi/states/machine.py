"""The transition lookup (M21). Repos call `lookup` before every `UPDATE ... WHERE state = from_`."""

from __future__ import annotations

from collections.abc import Sequence

from saathi.states.tables import Row


class Machine:
    """A table of rows run at one phase. A pair outside the table, or a row of a later phase, is not callable."""

    def __init__(self, rows: Sequence[Row], *, phase: int) -> None:
        self.phase = phase
        self.rows = tuple(rows)
        self._by_pair: dict[tuple[str, str], Row] = {}
        seen: dict[tuple[str, str], Row] = {}
        for row in self.rows:
            pair = (row.from_, row.event)
            if pair in seen:
                raise ValueError(f"duplicate transition {pair} in rows {seen[pair].id} and {row.id}")
            seen[pair] = row
            if row.phase <= phase:
                self._by_pair[pair] = row

    def lookup(self, from_: str, event: str) -> Row | None:
        """The row for this pair, or None when the pair is not in the table or belongs to a later phase."""
        return self._by_pair.get((from_, event))

    def events_from(self, from_: str) -> frozenset[str]:
        return frozenset(e for (f, e) in self._by_pair if f == from_)
