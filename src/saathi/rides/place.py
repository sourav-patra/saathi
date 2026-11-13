"""A saved place of a household: a row of `addresses` as the ride helper reads it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    id: str
    label: str
    formatted: str
    lat: float | None = None
    lng: float | None = None
    aliases: tuple[str, ...] = ()
    is_pickup: bool = False
    ride_count: int = 0

    @property
    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lng is not None
