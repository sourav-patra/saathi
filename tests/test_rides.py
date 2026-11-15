"""The ride helper without a database: the recogniser, the caregiver cards and their links, and the button type.

Synthetic places only ("Clinic A", "Temple B", "Relative C"); coordinates are small placeholders.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from saathi.channels.telegram import _markup
from saathi.domain import Button, Card
from saathi.rides import card as rc
from saathi.rides.place import Place
from saathi.rides.recognise import RIDE_WORDS, RideResult, normalise, recognise, usable_name

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


@pytest.mark.parametrize("text", ["ghar jaana hai", "cab to Home P"])
def test_the_pickup_place_can_be_an_explicit_destination(text):
    assert recognise(text, PLACES) == RideResult(True, HOME.id)


@pytest.mark.parametrize(
    "text",
    [
        "do not book a cab to Clinic A",
        "Clinic A ke liye cab mat bulao",
        "Clinic A जाने के लिए कैब नहीं चाहिए",
        "don't need a cab",
        "dont book a cab",
        "no cab",
        "cab nahi chahiye",
        "cab nhi chahiye",
        "कैब मत बुलाओ",
        "cab nahin bulao",
        "never book a cab",
        "cab नहीं",
        "नहीं कैब",
    ],
)
def test_negated_requests_are_not_rides(text):
    assert recognise(text, PLACES) == RideResult(False)


@pytest.mark.parametrize(
    "text",
    [
        "cab nahi mil rahi, bulao",
        "koi auto nahi mila, cab bulao doctor ke liye",
        "der mat karo, cab bulao",
        "No one is home, please book a cab to Clinic A",
        "not feeling well, cab to Clinic A",
        "कैब नहीं मिल रही ... जाना है",
    ],
)
def test_unrelated_negation_does_not_suppress_a_ride(text):
    assert recognise(text, PLACES).ride_word


@pytest.mark.parametrize(
    ("text", "place"),
    [
        ("कैब नहीं आ रही, मंदिर जाना है", "pl_temple"),
        ("cab to Clinic A nahi, mandir jaana hai", "pl_temple"),
        ("Clinic A nahi, cab to mandir", "pl_temple"),
        ("not Clinic A, cab to mandir", "pl_temple"),
        ("cab to Clinic A, not mandir", "pl_clinic"),
        ("Cab to Clinic A, not Temple B", "pl_clinic"),
        ("Cab to Clinic A nahi, Temple B", "pl_temple"),
    ],
)
def test_unavailability_and_rejected_places_still_give_a_ride(text, place):
    assert recognise(text, PLACES) == RideResult(True, place)


@pytest.mark.parametrize(
    "text",
    [
        # refusals fail closed: any refusal marker left after the narrow exceptions gives no card
        "cab mat bhejo",
        "Clinic A ke liye auto mat bhejna",
        "cab cancel karo",
        "please cancel the cab to Clinic A",
        "कैब मत भेजो",
        "do not want a cab",
        "don't need any taxi to Clinic A",
        "Don't book, please, a cab to Clinic A",
        "cab, mat bulao",
        "ऑटो। मत बुलाओ",
        "I don't need to book a cab",
        "I don't want another cab",
        "ola nahi chahiye, uber bulao",
        "My answer is no cab please",
        "The answer is no taxi to Clinic A",
        "No cab, coming on foot to Clinic A",
        "Don't send a cab",
        "please cancel my cab",
        "कैब। मत भेजो",
        "Don't book my cab to Clinic A",
        "Don't send us a taxi to Clinic A",
        "Do not need a wheelchair taxi to Clinic A",
        "I don't want you to book a cab to Clinic A",
        "Don't need to book me a cab to Clinic A",
        "Never call me a cab to Clinic A",
        "Cancel our cab to Clinic A",
        "Cancel the booked cab to Clinic A",
        "Cab please cancel it, Clinic A",
        "No cab is available; don't send one to Clinic A",
        "There is no cab available; do not book one to Clinic A",
        "Don't book any more cab to Clinic A",
        "Please cancel tomorrow's cab to Clinic A",
        "cab book mat karo",
        "auto mat bulaiye",
        "cab mat mangwao",
        "gaadi nahi bhejiye",
        "cab ki zaroorat nahi hai",
        "cab rehne do",
        "No need for a cab to Clinic A",
        "I won't need a cab",
        "cab not needed",
        "Taxi not now",
        # accepted cost: these mention a ride but also a negation about something else; the summary still reaches
        # the caregiver
        "do not want to walk, book a cab to Clinic A",
        "do not want to walk book a cab to Clinic A",
        "Book a cab to Clinic A so I can cancel my appointment",
        "cab bulao Clinic A jana hai bus se nahi",
        "मंदिर के लिए ऑटो चाहिए बस से नहीं",
        "cab please cancel my appointment",
        "cancel my appointment, cab to Clinic A",
        "Don't send medicine; cab to Clinic A",
        "cab to Clinic A not far from home",
        "is it not far, cab to Clinic A",
    ],
)
def test_refusals_fail_closed(text):
    assert recognise(text, PLACES) == RideResult(False)


@pytest.mark.parametrize("text", ["No hurry, cab to Clinic A", "No one is home, please book a cab to Clinic A"])
def test_a_plain_no_is_not_a_refusal(text):
    assert recognise(text, PLACES) == RideResult(True, "pl_clinic")


def test_two_devanagari_letters_allow_a_home_alias():
    home = Place("pl_home", "Home P", "x", aliases=("घर",), is_pickup=True)
    assert recognise("घर जाना है", [home]) == RideResult(True, home.id)


@pytest.mark.parametrize("name", ["PG", "ma", "a 1", "a b", "aह", "क्", "12"])
def test_name_minimum_counts_letters_within_a_script(name):
    assert not usable_name(name)


@pytest.mark.parametrize("name", ["a", "me", "the", "get", "cab"])
def test_old_ambiguous_place_names_do_not_match(name):
    place = Place("p_bad", name, "x", aliases=(name,))
    assert recognise("get me a cab", [place]) == RideResult(True)


def test_normalise_keeps_devanagari_and_latin_and_drops_the_rest():
    assert normalise("  Clinic-A,  मंदिर।  ") == "clinic a मंदिर"
    assert all(normalise(w) for w in RIDE_WORDS)


def test_ride_note_states_omitted_pickup_is_unverified():
    note = (Path(__file__).resolve().parents[1] / "docs/NOTE-ride-helper-2026-11-13.md").read_text(encoding="utf-8")
    assert "omitted pickup on `m.uber.com/looking` is not documented and not verified on a device" in note
    assert "https://developers.google.com/maps/documentation/urls/get-started#directions-action" in note
    assert "Maps defaults to device location if available" in note
    assert "button/android" not in note


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
    assert [b.text for b in card.buttons] == ["Temple B", "Home P", "Place 0", "Place 1", "Place 2"]
    assert [b.data for b in card.buttons][0] == "ride:pl_temple"
    assert all(b.url is None for b in card.buttons)


def test_return_home_card_omits_the_pickup_line_and_link_origin():
    card = rc.known_card(HOME, HOME)
    assert card.title == "Ride request"
    assert card.lines == ["To: Home P", "Drop: 1 Pickup Lane, Sample Town"]
    assert card.buttons[0].url == (
        "https://m.uber.com/looking?drop[0]=%7B%22latitude%22%3A0.0%2C%22longitude%22%3A0.0%2C"
        "%22addressLine1%22%3A%22Home%20P%22%7D"
    )
    assert card.buttons[-1].url == ("https://www.google.com/maps/dir/?api=1&destination=0.0%2C0.0&travelmode=driving")


def test_choice_card_offers_home():
    assert ("Home P", "ride:pl_home") in [(b.text, b.data) for b in rc.choice_card(PLACES).buttons]


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


@pytest.mark.parametrize("word", ["पर", "कब", "सब", "अब", "तक", "जब", "हम"])
def test_common_two_letter_devanagari_words_are_not_place_names(word):
    assert not usable_name(word)


@pytest.mark.parametrize(
    "text",
    [
        "There is no cab, do not book",
        "cab nahi milegi to mat bulao",
        "not feeling like a cab",
        "not feeling up to a taxi",
        "not well enough to take a cab",
        "cab nahi aana chahiye",
        "cab nahi aaj chahiye",
        "कैब नहीं आनी चाहिए",
        "कैब नहीं मिलनी चाहिए",
        "I can’t take a cab",
        "I cannot take a taxi",
        "I shouldn’t book a cab",
        "I wouldn’t want a cab",
        "I didnt want a cab",
        "She doesnt need a cab",
        "cab na bulao",
        "cab nehi chahiye",
        "cab nahii chahiye",
        "कैब न बुलाओ",
        "कैब कैंसिल करो",
        "cab please cancel",
        "never again a cab",
        "taxi will not be needed",
        "Don't arrange transport, cab",
        "Doesn’t want a cab",
        "Didn’t request a cab",
        "not Clinic A, cab to Temple B; do not book",
        "Clinic A nahi, cab to Temple B; mat bhejo",
        "der mat karo, cab mat bulao",
        "not feeling well, cab do not book",
        "there is no cab, not needed",
        "no taxi is available, cancel it",
        "Doctor se baat karao, cab to Clinic A nahi chahiye",  # "doctor" is Clinic A's own alias
        "cab not Clinic A, doctor",
    ],
)
def test_contractions_spellings_and_abused_exceptions_still_refuse(text):
    assert recognise(text, PLACES) == RideResult(False)


@pytest.mark.parametrize(
    "text",
    [
        "Please skip the cab today",
        "Please stop booking a cab",
        "Please hold off booking a taxi",
        "Forget the cab booking please",
        "Please call off the cab",
        "I have decided against taking a cab",
        "The taxi is unnecessary now",
        "We have called off the cab to Clinic A",
        "Cab? No, thank you",
        "No thanks, I will pass on the taxi",
        "No more cab rides please",
        "cab rehne dijiye",
        "auto rehne dena",
        "cab ki zarurat nai hai",
        "cab ki zaroorat nahee hai",
        "taxi radd kar do",
        "कैब रहने दीजिए",
        "ऑटो रहने देना",
        "टैक्सी कैंसिल कर दीजिए",
        "Please cancel the taxi booking",
        "Do not send the auto today",
        "I cannot manage a cab today",
        "Please stop the cab booking to Clinic A",
        "No, I refuse the cab to Clinic A",
        "Clinic A nahi chahiye cab, Temple B ka number do",  # main refuses this; the place rule must not undo it
        "Cab to Clinic A? No, thank you",
    ],
)
def test_everyday_refusal_wording_refuses(text):
    assert recognise(text, PLACES) == RideResult(False)


@pytest.mark.parametrize(
    "text",
    [
        "No cab is coming",
        "No cab is available",
        "There is no cab",
        "No cab is coming; I will walk to Clinic A",
        "There is no auto, I will walk",
        "Clinic A nahi jaana hai, Temple B ka number do",
        "Clinic A nahi jana hai, Temple B ka number do",
        "Clinic A nahin jaana hai, Temple B ka number do",
        "cab not Clinic A",
        "cab not Clinic A, doctor",
    ],
)
def test_exceptions_never_turn_a_refusal_on_main_into_a_card(text):
    assert recognise(text, PLACES) == RideResult(False)


def test_a_rejected_place_followed_by_another_place_picks_the_other():
    assert recognise("cab not Clinic A, Temple B", PLACES) == RideResult(True, "pl_temple")


@pytest.mark.parametrize(
    "text",
    [
        # English "no cab is coming" always refuses, as on main; a missed card is accepted
        "No cab is coming, please book an auto to Clinic A",
        "No cab is coming please book an auto to Clinic A",
        "there is no auto here, get a cab to Clinic A",
        "No cab is available, get an auto to Clinic A",
        "No taxi has arrived; auto to Clinic A",
        "No cab is coming; I will walk to Clinic A and save the taxi fare",
        "There is no auto; I will walk to Clinic A instead of waiting for a cab",
        "No cab is coming; book a taxi to Clinic A",
        "cab do zqbrk not book",
        "Cab?! No...thank you!!!",
        "cab not Clinic A today; Temple B",
    ],
)
def test_english_unavailability_refuses_as_on_main(text):
    assert recognise(text, PLACES) == RideResult(False)


def test_a_place_name_that_overlaps_a_refusal_cannot_undo_it():
    taxi = Place("pl_taxi", "City Taxi", "5 Test Road, Sample Town", 0.5, 0.6)
    centre = Place("pl_centre", "Call Centre", "6 Test Road, Sample Town", 0.7, 0.8)
    assert recognise("Clinic A jaana hai, City Taxi nahi", [*PLACES, taxi]) == RideResult(False)
    assert recognise("Do not call centre, cab to Clinic A", [*PLACES, centre]) == RideResult(False)


@pytest.mark.parametrize(
    "text",
    ["cab not Family Doctor Clinic, bua", "bua, cab not Family Doctor Clinic", "Family Doctor Clinic nahi, cab to bua"],
)
def test_an_alias_inside_a_rejected_name_is_not_a_destination(text):
    family = Place("pl_family", "Family Doctor Clinic", "7 Test Road, Sample Town", 0.9, 0.9)
    assert recognise(text, [*PLACES, family]) == RideResult(True, "pl_relative")
