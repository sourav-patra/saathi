# Note: ride helper

## Scope

A ride request gives caregivers an extra card after the summary. Saathi books nothing, holds no payment and calls no model. The elder reply stays "I have told your family." Emergency, scam and failed-radar messages get no ride card. Ride errors log only an inbox id and error type. Each ride database transaction has a three-second deadline, including acquisition and commit; summaries, replies and alerts proceed. Failed counting retries with the inbox after the reply.

## Data

Places reuse household-scoped `addresses`: label, formatted address, coordinates, aliases, one optional pickup, ride count and last ride time. Migration 008 adds the ride columns and append-only `ride_counts` claims, unique by household, source and source id. Each claim and increment commit together. Inbox claims count once across caregivers and retries. Callback claims count once per update and retain the card for edit retries, even after place removal. Claims use forced household row-level security. The worker and bot use the core role: read addresses, update only counters, insert/read claims; address INSERT is revoked. Owner admin commands add, list, alias and remove places. Audit rows contain ids and flags only.

## Recognition and cards

The pure recogniser normalises Unicode, case and punctuation, preserving Latin and Devanagari. It requires a ride phrase and rejects negation words in English, Hinglish and Hindi. Labels and aliases match whole phrases, longest first; ties give caregiver choice. Admin rejects names shorter than three normalised characters or on the common-word stoplist; recognition also ignores old ambiguous names.

A known destination shows To, Pickup and Drop with app/map buttons. An explicitly named pickup label or alias is a valid destination, including "ghar jaana hai". That return card shows To and Drop, omits Pickup and leaves pickup/origin out of the links. Unknown destinations offer up to five saved places, including the pickup, ordered by count then label. An authorised tap edits the choice card into the saved destination card.

## Links and limits

- [Uber deep links](https://developer.uber.com/docs/riders/ride-requests/tutorials/deep-links/introduction): `/looking` with encoded pickup and drop JSON (latitude, longitude, addressLine1). [Uber's button documentation](https://developer.uber.com/docs/riders/ride-requests/tutorials/button/android#customize-button-parameters), checked 2026-11-14, states that pickup defaults to the user's current location. Return trips therefore omit pickup. The caregiver must verify the rider's actual pickup before booking, especially when opening the link remotely.
- [Maps URLs](https://developers.google.com/maps/documentation/urls/get-started): directions with `api=1`, optional origin, destination and `travelmode=driving`; coordinate commas are encoded.
- Rapido and Namma Yatri open official Android store pages, not pre-filled rides: packages `com.rapido.passenger` and `in.juspay.nammayatri`, checked 2026-11-13. No Apple store links.
- No coordinates means no Uber/Map button. Missing pickup coordinates omit the link origin. Exact matching needs aliases for spelling variants. Negation detection is conservative word matching.

Skipped or not checked: mobile-app link behaviour and omission of Uber's `client_id` remain unverified.
