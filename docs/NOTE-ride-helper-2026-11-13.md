# Note: ride helper

## Scope

A ride request gives caregivers an extra card after the summary. Saathi books nothing, holds no payment and calls no model. The elder reply stays "I have told your family." Emergency, scam and failed-radar messages get no ride card. Ride errors log only an inbox id and error class. Each ride database operation has a three-second caller deadline, including acquisition and commit. On timeout the worker cancels its separate task; rollback and connection reset continue independently while summaries, replies and alerts proceed. Other database paths keep their existing settings.

Seat decision, 2026-11-14: ride counts are optional ordering data. A failed or timed-out count is logged with inbox id and error class only and is not re-raised. The inbox row completes normally when its required effects succeed. Counts can understate delivered rides after a failure; an in-flight commit may complete after the caller deadline, with the claim preventing a duplicate increment.

## Data

Places reuse household-scoped `addresses`: label, formatted address, coordinates, aliases, one optional pickup, ride count and last ride time. Migration 008 adds the ride columns and append-only `ride_counts` claims, unique by household, source and source id. Each claim and increment commit together. Inbox claims count once across caregivers and retries. Callback claims store only destination and optional pickup ids, rebuilding edit retries from live addresses. Removing a place leaves no label or address text in the database. A replay for a removed destination answers that the place is gone and counts nothing; a removed pickup is omitted. Claims use forced household row-level security. The worker and bot use the core role: read addresses, update only counters, insert/read claims; address INSERT is revoked. Owner admin commands add, list, alias and remove places. Audit rows contain ids and flags only.

## Recognition and cards

The pure recogniser normalises Unicode, case and punctuation, preserving Latin and Devanagari. It requires a ride phrase and rejects request-bound negation phrases in English, Hinglish and Hindi, such as "do not book", "mat bulao" and "नहीं चाहिए". Unrelated negation, such as "cab nahi mil rahi, bulao", still allows the request. Labels and aliases match whole phrases, longest first; ties give caregiver choice. Names need at least three letters in one script, or two Devanagari letters, so "घर" is allowed. Spaces, digits and combining marks do not count as letters. Admin and recognition both apply this minimum and the common-word stoplist.

A known destination shows To, Pickup and Drop with app/map buttons. An explicitly named pickup label or alias is a valid destination, including "ghar jaana hai". That return card shows To and Drop, omits Pickup and leaves pickup/origin out of the links. Unknown destinations offer up to five saved places, including the pickup, ordered by count then label. An authorised tap edits the choice card into the saved destination card.

## Links and limits

- [Uber deep links](https://developer.uber.com/docs/riders/ride-requests/tutorials/deep-links/introduction), checked 2026-11-14: `/looking` with encoded pickup and drop JSON (latitude, longitude, addressLine1). The default for an omitted pickup on `m.uber.com/looking` is not documented and not verified on a device. Return trips omit pickup; the caregiver must verify the rider's actual pickup before booking, especially when opening the link remotely.
- [Maps URLs](https://developers.google.com/maps/documentation/urls/get-started#directions-action), checked 2026-11-14: directions with `api=1`, optional origin, destination and `travelmode=driving`; coordinate commas are encoded. When origin is omitted, Maps defaults to device location if available, otherwise an empty origin form.
- Rapido and Namma Yatri open official Android store pages, not pre-filled rides: packages `com.rapido.passenger` and `in.juspay.nammayatri`, checked 2026-11-13. No Apple store links.
- No coordinates means no Uber/Map button. Missing pickup coordinates omit the link origin. Exact matching needs aliases for spelling variants. Negation detection uses a bounded phrase list and does not parse intent.

Skipped or not checked: mobile-app link behaviour, the omitted-pickup default on `m.uber.com/looking`, omission of Uber's `client_id` and real network-partition cleanup remain unverified. Stalled cleanup is covered by fake transactions.
