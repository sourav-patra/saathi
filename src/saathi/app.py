"""Composition root of the edge service (M07). `saathi-api` points at `main_edge`.

The edge persists, scans for keywords and answers. The worker, the alert service and the caregiver bot are separate
processes with their own roles and are composed by their own units.
"""

from __future__ import annotations

from saathi.edge.wiring import build_edge, main


def main_edge() -> None:  # pragma: no cover
    main()


run = main_edge  # the name the debugging entry point of the caregiver bot module imports

__all__ = ["build_edge", "main_edge", "run"]
