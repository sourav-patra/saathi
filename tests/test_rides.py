"""The ride helper without a database: the recogniser, the caregiver cards and their links, and the button type.

Synthetic places only ("Clinic A", "Temple B", "Relative C"); coordinates are small placeholders.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from saathi.channels.telegram import _markup
from saathi.domain import Button, Card
from saathi.rides import card as rc
from saathi.rides.place import Place
from saathi.rides.recognise import RIDE_WORDS, RideResult, normalise, recognise

HOME = Place("pl_home", "Home P", "1 Pickup Lane, Sample Town", 0.0, 0.0, ("ghar",), is_pickup=True)
CLINIC = Place("pl_clinic", "Clinic A", "2 Test Road, Sample Town", 0.1, 0.2, ("doctor",))
TEMPLE = Place("pl_temple", "Temple B", "3 Test Road, Sample Town", 0.3, 0.4, ("mandir", "मंदिर"), ride_count=4)
RELATIVE = Place("pl_relative", "Relative C", "4 Test Road, Sample Town", None, None, ("bua",))
PLACES = [HOME, CLINIC, TEMPLE, RELATIVE]


# --- the recogniser ---------------------------------------------------------------------------------------------
def test_english_ride_to_a_known_place():
    assert recognise("Please get me a cab to Clinic A tomorrow.", PLACES) == RideResult(True, "pl_clinic")


def test_hinglish_with_an_alias():
    assert recognise("mujhe mandir jaana hai", PLACES) == RideResult(True, "pl_temple")
    assert recognise("Doctor ke yahan le chalo", PLACES) == RideResult(True, "pl_clinic")


def test_devanagari_ride_word_and_alias():
    assert recognise("मुझे मंदिर जाना है", PLACES) == RideResult(True, "pl_temple")
    assert recognise("मंदिर के लिए ऑटो चाहिए", PLACES) == RideResult(True, "pl_temple")
    # the nukta may be typed as one letter or as two: both spell the same word
    assert recognise("गाड़ी चाहिए, मंदिर", PLACES).ride_word
    assert recognise("गाड़ी चाहिए, मंदिर", PLACES).ride_word


def test_longest_alias_wins():
    short = Place("p_short", "Doctor", "x", aliases=())
    long = Place("p_long", "Family Doctor Clinic", "y", aliases=("doctor sahab ka clinic",))
    assert recognise("doctor sahab ka clinic jaana hai", [short, long]) == RideResult(True, "p_long")
    assert recognise("doctor jaana hai", [short, long]) == RideResult(True, "p_short")


def test_the_same_longest_alias_on_two_places_names_none():
    a = Place("pa", "North Clinic", "x", aliases=("clinic",))
    b = Place("pb", "South Clinic", "y", aliases=("clinic",))
    assert recognise("clinic jaana hai", [a, b]) == RideResult(True, None)


def test_no_ride_word_is_not_a_ride():
    assert recognise("Clinic A ka number do", PLACES) == RideResult(False, None)
    assert recognise("", PLACES) == RideResult(False, None)
    assert recognise("a cabinet near Clinic A", PLACES) == RideResult(False, None)  # whole words only


def test_ride_word_without_a_known_place():
    assert recognise("cab chahiye", PLACES) == RideResult(True, None)
    assert recognise("cab to Clinic AB", PLACES) == RideResult(True, None)  # "clinic ab" is not "clinic a"


def test_punctuation_and_case_are_ignored():
    assert recognise("CAB, to clinic-a!!", PLACES) == RideResult(True, "pl_clinic")


def test_the_pickup_place_is_never_a_drop():
    assert recognise("ghar se cab chahiye", PLACES) == RideResult(True, None)


def test_normalise_keeps_devanagari_and_latin_and_drops_the_rest():
    assert normalise("  Clinic-A,  मंदिर।  ") == "clinic a मंदिर"
    assert all(normalise(w) for w in RIDE_WORDS)


# --- the Button type --------------------------------------------------------------------------------------------
def test_button_has_exactly_one_of_data_and_url():
    assert Button(text="t", data="ack:e1").url is None
    assert Button(text="t", url="https://example.com/").data is None
    with pytest.raises(ValidationError):
        Button(text="t")
    with pytest.raises(ValidationError):
        Button(text="t", data="x", url="https://example.com/")


def test_telegram_markup_renders_url_and_callback_buttons():
    card = Card(title="t", buttons=[Button(text="Go", url="https://example.com/x"), Button(text="Tap", data="ride:p1")])
    rows = _markup(card).inline_keyboard  # type: ignore[union-attr]
    assert (rows[0][0].text, rows[0][0].url, rows[0][0].callback_data) == ("Go", "https://example.com/x", None)
    assert (rows[1][0].text, rows[1][0].url, rows[1][0].callback_data) == ("Tap", None, "ride:p1")


# --- the cards --------------------------------------------------------------------------------------------------
UBER_HOME_TO_CLINIC = (
    "https://m.uber.com/looking?pickup=%7B%22latitude%22%3A0.0%2C%22longitude%22%3A0.0%2C"
    "%22addressLine1%22%3A%22Home%20P%22%7D&drop[0]=%7B%22latitude%22%3A0.1%2C%22longitude%22%3A0.2%2C"
    "%22addressLine1%22%3A%22Clinic%20A%22%7D"
)
MAPS_HOME_TO_CLINIC = "https://www.google.com/maps/dir/?api=1&origin=0.0%2C0.0&destination=0.1%2C0.2&travelmode=driving"


def test_known_place_card_has_the_lines_and_exact_links():
    card = rc.known_card(CLINIC, HOME)
    assert card.title == "Ride request"
    assert card.lines == ["To: Clinic A", "Pickup: 1 Pickup Lane, Sample Town", "Drop: 2 Test Road, Sample Town"]
    assert [(b.text, b.url, b.data) for b in card.buttons] == [
        ("Uber (pre-filled)", UBER_HOME_TO_CLINIC, None),
        ("Rapido", "https://play.google.com/store/apps/details?id=com.rapido.passenger", None),
        ("Namma Yatri", "https://play.google.com/store/apps/details?id=in.juspay.nammayatri", None),
        ("Map", MAPS_HOME_TO_CLINIC, None),
    ]


def test_a_place_without_coordinates_gets_no_uber_or_map_button():
    card = rc.known_card(RELATIVE, HOME)
    assert [b.text for b in card.buttons] == ["Rapido", "Namma Yatri"]
    assert card.lines[0] == "To: Relative C"


def test_without_a_pickup_the_card_has_no_pickup_line_and_the_links_leave_the_origin_out():
    card = rc.known_card(CLINIC, None)
    assert not any(line.startswith("Pickup") for line in card.lines)
    uber, _, _, maps = (b.url for b in card.buttons)
    assert uber is not None and "pickup=" not in uber and "drop[0]=" in uber
    assert maps == "https://www.google.com/maps/dir/?api=1&destination=0.1%2C0.2&travelmode=driving"


def test_a_pickup_without_coordinates_is_left_out_of_the_links_only():
    bare = Place("pl_bare", "Home Q", "5 Pickup Lane", None, None, is_pickup=True)
    card = rc.known_card(CLINIC, bare)
    assert "Pickup: 5 Pickup Lane" in card.lines
    assert "pickup=" not in (card.buttons[0].url or "") and "origin=" not in (card.buttons[-1].url or "")


def test_choice_card_lists_up_to_five_places_by_ride_count_then_label():
    many = [Place(f"p{i}", f"Place {i}", "x", ride_count=0) for i in range(7)] + [TEMPLE, HOME]
    card = rc.choice_card(many)
    assert card.title == "Ride request: place not recognised"
    assert [b.text for b in card.buttons] == ["Temple B", "Place 0", "Place 1", "Place 2", "Place 3"]
    assert [b.data for b in card.buttons][0] == "ride:pl_temple"
    assert all(b.url is None for b in card.buttons)  # the pickup is never offered as a drop


def test_choice_card_skips_an_id_too_long_for_a_button():
    odd = Place("x" * 60, "Long Id", "x")
    assert [b.text for b in rc.choice_card([odd, CLINIC]).buttons] == ["Clinic A"]


async def test_a_routed_tap_carries_the_card_it_came_from():
    from aiogram.types import Update
    from fakes import FakeClock

    from saathi.channels.telegram import TelegramCaregiverChannel
    from saathi.ids import ApproverId, HouseholdId

    class Directory:
        async def resolve(self, telegram_id: int) -> tuple[HouseholdId, ApproverId] | None:
            return (HouseholdId("h1"), ApproverId("ap1")) if telegram_id == 3001 else None

        async def chat_of(self, approver: ApproverId) -> int:
            return 3001

    raw = Update.model_validate(
        {
            "update_id": 5,
            "callback_query": {
                "id": "cb5", "chat_instance": "ci", "data": "ride:pl_clinic",
                "from": {"id": 3001, "is_bot": False, "first_name": "Caregiver"},
                "message": {
                    "message_id": 7, "date": 1767268800, "chat": {"id": 3001, "type": "private"},
                    "from": {"id": 1, "is_bot": True, "first_name": "Bot"}, "text": "card",
                },
            },
        }
    )  # fmt: skip
    channel = TelegramCaregiverChannel(object(), Directory(), FakeClock())  # type: ignore[arg-type]
    routed = await channel._route(raw)
    assert routed is not None and (routed.data, routed.message_ref) == ("ride:pl_clinic", "3001:7")
