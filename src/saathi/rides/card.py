"""The caregiver cards of the ride helper, built from saved places and fixed words. No transcript text is used.

Links (cited in docs/NOTE-ride-helper-2026-11-13.md; no parameter here is a guess):
- the taxi app's universal link `https://m.uber.com/looking` with JSON `pickup` and `drop[0]` (latitude, longitude,
  `addressLine1` the nickname), from its rider deep-link documentation; `client_id` is left out because the page does
  not mark it required;
- Google Maps directions `https://www.google.com/maps/dir/?api=1&origin=..&destination=..&travelmode=driving`, from
  the Maps URLs documentation (coordinates as `lat%2Clng`);
- the Android store pages of the two other apps, which publish no ride link of their own.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from urllib.parse import quote

from saathi.domain import Button, Card
from saathi.rides.place import Place

TITLE = "Ride request"
TITLE_UNKNOWN = "Ride request: place not recognised"
CALLBACK = "ride:"  # the callback data of a place choice: `ride:<place id>`
MAX_CHOICES = 5
_SAFE_ID = re.compile(r"[\w.-]{1,43}")  # `ride:` plus the id must fit the caregiver bot's callback rule

UBER_URL = "https://m.uber.com/looking"
MAPS_URL = "https://www.google.com/maps/dir/"
RAPIDO_STORE_URL = "https://play.google.com/store/apps/details?id=com.rapido.passenger"
NAMMA_YATRI_STORE_URL = "https://play.google.com/store/apps/details?id=in.juspay.nammayatri"


def _num(value: float) -> str:
    return repr(float(value))


def _location(place: Place) -> str:
    body = {"latitude": place.lat, "longitude": place.lng, "addressLine1": place.label}
    return quote(json.dumps(body, separators=(",", ":"), ensure_ascii=False), safe="")


def uber_url(pickup: Place | None, drop: Place) -> str | None:
    """None when the drop has no coordinates. The pickup goes in only when it has coordinates."""
    if not drop.has_coordinates:
        return None
    parts = []
    if pickup is not None and pickup.has_coordinates:
        parts.append(f"pickup={_location(pickup)}")
    parts.append(f"drop[0]={_location(drop)}")
    return f"{UBER_URL}?" + "&".join(parts)


def maps_url(pickup: Place | None, drop: Place) -> str | None:
    """None when the drop has no coordinates. The origin goes in only when the pickup has coordinates."""
    if not drop.has_coordinates:
        return None
    parts = ["api=1"]
    if pickup is not None and pickup.has_coordinates:
        parts.append(f"origin={_num(pickup.lat)}%2C{_num(pickup.lng)}")  # type: ignore[arg-type]
    parts.append(f"destination={_num(drop.lat)}%2C{_num(drop.lng)}")  # type: ignore[arg-type]
    parts.append("travelmode=driving")
    return f"{MAPS_URL}?" + "&".join(parts)


def known_card(drop: Place, pickup: Place | None) -> Card:
    """The card for a recognised or chosen place: the lines, then Uber (when it has coordinates), Rapido, Namma Yatri
    and Map (when it has coordinates)."""
    lines = [f"To: {drop.label}"]
    if pickup is not None:
        lines.append(f"Pickup: {pickup.formatted}")
    lines.append(f"Drop: {drop.formatted}")
    buttons: list[Button] = []
    if (uber := uber_url(pickup, drop)) is not None:
        buttons.append(Button(text="Uber (pre-filled)", url=uber))
    buttons.append(Button(text="Rapido", url=RAPIDO_STORE_URL))
    buttons.append(Button(text="Namma Yatri", url=NAMMA_YATRI_STORE_URL))
    if (route := maps_url(pickup, drop)) is not None:
        buttons.append(Button(text="Map", url=route))
    return Card(title=TITLE, lines=lines, buttons=buttons)


def choices(places: Sequence[Place]) -> list[Place]:
    """Up to five drop places, most used first, then by label. The pickup and an id too long for a button go."""
    usable = [p for p in places if not p.is_pickup and _SAFE_ID.fullmatch(p.id)]
    return sorted(usable, key=lambda p: (-p.ride_count, p.label))[:MAX_CHOICES]


def choice_card(places: Sequence[Place]) -> Card:
    """The card for a ride word with no known place: one callback button per place, `ride:<place id>`."""
    picks = choices(places)
    lines = ["They asked for a ride, but the place is not one Saathi knows."]
    lines.append("Tap the place they meant." if picks else "No saved place is available. Ask them where to.")
    buttons = [Button(text=p.label, data=f"{CALLBACK}{p.id}") for p in picks]
    return Card(title=TITLE_UNKNOWN, lines=lines, buttons=buttons)
