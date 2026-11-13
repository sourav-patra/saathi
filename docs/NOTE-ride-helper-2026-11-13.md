# Note: ride helper

## Scope

When an elder asks for a ride and the household has saved places, the caregiver gets one extra card after the summary card. The card links to taxi apps and a map. Saathi books nothing, holds no payment method and calls no model. The elder's reply is unchanged ("I have told your family."). An emergency or scam message never gets a ride card, and a failure in this step is logged and blocks neither the summary nor the elder reply.

## Data

Places are rows of `addresses` (label, formatted address, lat, lng). Migration `008_ride_places.sql` adds `aliases text[]`, `is_pickup boolean` (at most one per household, by a partial unique index), `ride_count integer` and `last_ride_at timestamptz`. Row-level security is the existing household policy. The worker and bot role may read places and change only the two counters; the owner role adds, lists, aliases and removes them with `saathi-admin place add|list|alias-add|remove`. Audit rows hold ids and flags only, never a label or address.

## Recognition

`rides/recognise.py` is a pure function. It normalises the words (composed Unicode, case folded, punctuation dropped; Latin and Devanagari kept), requires a ride word from a list in code (English, Hinglish, Hindi), then matches place labels and aliases as whole words, longest first. The pickup place is never a drop. A tie on the same longest phrase names no place. No ride word means not a ride.

## Cards

A known place gives "Ride request" with the lines To, Pickup and Drop, and buttons Uber (pre-filled), Rapido, Namma Yatri and Map. A ride word with no known place gives "Ride request: place not recognised" with up to five places (most rides first, then label) as buttons `ride:<place id>`. A tap edits the card into the known-place card. A known-place card or a tap adds one to `ride_count` (once per message, however many caregivers).

Links, each taken from the provider's own page:
- Uber universal link `https://m.uber.com/looking` with JSON `pickup` and `drop[0]` (`latitude`, `longitude`, `addressLine1` as the nickname): developer.uber.com/docs/riders/ride-requests/tutorials/deep-links/introduction. The page lists `client_id` in its examples without marking it required, so it is left out.
- Map: `https://www.google.com/maps/dir/?api=1&origin=..&destination=..&travelmode=driving`, coordinates as `lat%2Clng`: developers.google.com/maps/documentation/urls/get-started.
- Rapido store page, Android package `com.rapido.passenger`, and Namma Yatri store page, package `in.juspay.nammayatri`, both under `https://play.google.com/store/apps/details?id=`. Both pages were opened on 2026-11-13 and name the apps.

## Limits

- No booking, no fare, no driver contact. The caregiver taps and books in the app.
- Rapido and Namma Yatri have no documented ride link here, so the buttons open the Android store page, not a pre-filled ride. Apple store pages are not linked.
- A place without coordinates gets no Uber or Map button. A pickup without coordinates is left out of both links.
- Matching is by exact words after normalising. Spelling variants need an alias.
- A caregiver tap whose card edit fails and is retried counts the ride twice.

Skipped or not checked: the Uber link was not opened in the Uber app, and the `client_id` omission was not tested against it.
