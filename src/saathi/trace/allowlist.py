"""The field allowlist of a trace span (M18; RQ-57, RQ-44).

A span holds ids, counts, hashes, durations and short coded reasons. Everything else is dropped before it is written:
a key that is not listed, a value that is not a scalar, a string over `MAX_STRING` characters, a string that does not
fit its field's type (id, code or exception class name, hex digest, list of codes), and a string that still holds a
restricted pattern after `assert_clean`. A dropped value is written as the marker, so the line shows that
something was there without showing what.
"""

from __future__ import annotations

import enum
import functools
import re
from collections.abc import Mapping
from typing import Literal, get_args, get_origin

from saathi.guards.pii import RestrictedDataLeft, assert_clean

ALLOWED_FIELDS = frozenset(
    {
        "run_id", "request_id", "inbox_id", "event_id", "household_id", "intent", "confidence", "flags", "rule_ids",
        "count", "bytes", "sha256", "duration_ms", "state", "reason", "stage", "grade", "attempt", "ok",
    }
)  # fmt: skip
MAX_STRING = 64
DROPPED = "<dropped>"
MAX_ITEMS = 16  # `flags` and `rule_ids` may be short lists of codes; they are written as one comma-joined string

_HASH = re.compile(r"^[0-9a-f]{64}$")  # a full digest is long digit runs by chance; it is not text and is let through
_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,47}$")  # a span name is a code constant, never a value
_CODE = _NAME  # a coded value: lowercase, no spaces, no leading digit
_CLASS = re.compile(r"^[A-Z][A-Za-z]{0,47}$")  # an exception class name, and only a real one (see `_is_exception_name`)
_ID = re.compile(r"^(?=.*[0-9])(?=.*[a-z])[a-z0-9][a-z0-9_:.-]{0,63}$")  # an opaque id holds a letter and a digit
_ITEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,47}$")  # one entry of `flags` or `rule_ids`

ID_FIELDS = frozenset({"run_id", "request_id", "inbox_id", "event_id", "household_id"})
CODE_FIELDS = frozenset({"reason", "state", "stage", "grade", "intent"})
LIST_FIELDS = frozenset({"flags", "rule_ids"})
EXTRA_CODES = frozenset({"processed", "handoff", "unknown", "ok"})  # code values used by call sites, not in `domain`


def clean_name(name: object) -> str:
    return name if isinstance(name, str) and _NAME.match(name) else DROPPED


@functools.cache
def known_codes() -> frozenset[str]:
    """Every value of a `domain` StrEnum or Literal, plus `EXTRA_CODES`. A word outside it needs a separator."""
    from saathi import domain

    out: set[str] = set(EXTRA_CODES)

    def literal_args(tp: object) -> None:
        if get_origin(tp) is Literal:
            out.update(a for a in get_args(tp) if isinstance(a, str))
        for arg in get_args(tp):
            literal_args(arg)

    for obj in vars(domain).values():
        if isinstance(obj, type) and issubclass(obj, enum.StrEnum):
            out.update(str(m.value) for m in obj)
        elif isinstance(obj, type) and hasattr(obj, "model_fields"):
            for f in obj.model_fields.values():  # type: ignore[attr-defined]
                literal_args(f.annotation)
        else:
            literal_args(obj)
    return frozenset(out)


def _is_code(value: str) -> bool:
    """A coded value is a known code, or a code with a separator (`no_config`); a bare word (a name) is not one."""
    if not _CODE.match(value):
        return False
    return value in known_codes() or any(c in value for c in "_.:-")


def _is_item(item: str) -> bool:
    """One entry of a code list: a known code, or a code with a letter and a separator or digit (`RD-EMG-1`)."""
    if not _ITEM.match(item) or not re.search("[A-Za-z]", item):
        return False
    return item in known_codes() or bool(re.search(r"[_.:0-9-]", item))


def _exception_names() -> set[str]:
    seen: set[str] = set()
    stack: list[type] = [BaseException]
    while stack:
        cls = stack.pop()
        if cls.__name__ not in seen:
            seen.add(cls.__name__)
            stack.extend(cls.__subclasses__())
    return seen


def _is_exception_name(value: str) -> bool:
    return bool(_CLASS.match(value)) and value in _exception_names()


def _restricted(value: str) -> bool:
    try:
        assert_clean(value)
    except RestrictedDataLeft:
        return True
    return False


def _clean_text(value: str, *, key: str) -> str | None:
    """The string, or None when it must be dropped. Each listed field has a type; anything outside it is dropped."""
    if len(value) > MAX_STRING:
        return None
    if key == "sha256":
        return value if _HASH.match(value) else None
    if key in ID_FIELDS:
        ok = bool(_ID.match(value))
    elif key in CODE_FIELDS:
        ok = _is_code(value) or _is_exception_name(value)
    elif key in LIST_FIELDS:
        items = value.split(",") if value else []
        ok = all(_is_item(i) for i in items)
    else:
        ok = False  # a listed numeric field never takes a string
    return value if ok and not _restricted(value) else None


def clean_value(key: str, value: object) -> object | None:
    """The value as it may be written, or None when it is dropped. A bool is checked before an int (it is one)."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _clean_text(value, key=key)
    if isinstance(value, list | tuple | frozenset | set) and key in LIST_FIELDS:
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else list(value)
        if len(items) > MAX_ITEMS or not all(isinstance(i, str) and "," not in i for i in items):
            return None
        joined = ",".join(items)
        return _clean_text(joined, key=key)
    return None  # a mapping, bytes, a model object, a long list: never exported


def clean_fields(fields: Mapping[str, object]) -> tuple[dict[str, object], int]:
    """(the fields to write, the number dropped). An unlisted key is dropped by name; its value is never looked at."""
    out: dict[str, object] = {}
    dropped = 0
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS:
            dropped += 1
            continue
        cleaned = clean_value(key, value)
        if cleaned is None and value is not None:
            out[key] = DROPPED
            dropped += 1
        else:
            out[key] = cleaned
    return out, dropped
