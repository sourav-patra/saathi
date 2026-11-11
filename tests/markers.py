"""Rule markers (RQ-63): `@rule("PE-nn")` ties a test to a policy rule in scripts/rules.toml.

The decorator only records the ids; scripts/rule_index.py reads the decorators with ast.
"""

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)


def rule(*rule_ids: str) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        fn.__rule_ids__ = (*getattr(fn, "__rule_ids__", ()), *rule_ids)  # type: ignore[attr-defined]
        return fn

    return deco
