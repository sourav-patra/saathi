"""FakeClock lives in the package (LLD intake M03); the test package re-exports it."""

from saathi.clock import FakeClock

__all__ = ["FakeClock"]
