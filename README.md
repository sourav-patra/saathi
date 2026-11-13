# Saathi

An open-source helper for older family members: they send a WhatsApp voice note, and Saathi checks it for danger and tells the family on Telegram. Ordering with a family member's one-tap approval is planned for Phase 1.

[![ci](https://github.com/sourav-patra/saathi/actions/workflows/ci.yml/badge.svg)](https://github.com/sourav-patra/saathi/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

![Three columns: the elder sends a voice note on WhatsApp; the server saves it, acknowledges, transcribes and scans it; the caregiver gets a Telegram card and the elder hears "I have told your family"; an emergency instead gives a "possible emergency" card and a "call 112 or 108" reply](docs/figures/saathi-today.svg)

*What Phase 0 does today, shown with synthetic text. It listens, checks and tells the family. It orders nothing yet.*

Saathi is a working codename; the public name comes later. Saathi is not an emergency service; it alerts your family. In an emergency, call 112, or 108 for an ambulance.

## Why

Many older people in India get stuck on everyday phone tasks: booking a ride, ordering groceries, telling a real message from a scam. They phone a son or daughter for each one.

Today Saathi takes the request in the elder's own words, in Hindi or everyday English, and passes it to the family with any danger flagged. From Phase 1 it is meant to do the work up to the final step, which a caregiver approves from Telegram.

Evidence so far: the Phase 0 release checklist (a "gate" of 68 checks that the automated build, CI, runs on every change) has 55 checks passing and 0 failing.

The other 13 wait on records only the owner can produce, such as the manual pilot and repository settings. One of them is filed and waits for the next CI run ([gate script](scripts/gate.py), [records](docs/evidence/receipts)).

## Features

- Voice notes, photos and text on WhatsApp; caregiver cards on Telegram. Approval buttons come in Phase 1.
- Emergency and scam radar: a keyword list is checked first, so a match alerts the family without waiting for a classifier model.
- Personal data leaves your server only by the routes [the data policy](docs/design/POLICY-saathi-data-2026-11-09.md) lists, after a recorded reason; free hosted model tiers never see it.
- Every dependency is checked in CI against [the licence policy](scripts/license_exceptions.toml): MIT, Apache-2.0 or similar permissive licences, plus a short list of named, reasoned exceptions (for container base packages, [this list](scripts/image_base_allowlist.toml)).
- For the planned order path, a policy engine is already built and tested as the only route to an order: it needs the caregiver's approval tied to the exact quote (items and price), cash on delivery or an allowed wallet, a spend cap and an address from the household's approved list.

## Quick start

These steps run the tests on your machine. They need Python 3.12 and [uv](https://docs.astral.sh/uv/).

1. Install the dependencies: `uv sync`. Check: the command ends without an error.
2. Run the tests: `uv run pytest -q`. Check: the last line reports passed tests and no failures. Tests that need Postgres skip unless `SAATHI_TEST_DSN` is set.
3. Run the licence audit: `uv run python scripts/license_audit.py`. Check: it prints `license audit passed`.

To run Saathi on a server, follow [the setup guide](docs/SETUP.md).

## How an order will be approved

![Sequence of eleven numbered messages between the caregiver on Telegram, the approval service, the database, the commit executor, the adapter and the commerce service, in three steps](docs/design/figures/commit-sequence.svg)

*The approval and order sequence planned for Phase 1. The caregiver taps Approve on a card; code checks the approval against the quote before any order is placed. The codes (C12, X1 and so on) are component and zone names from [the technical requirements](docs/design/TRD-saathi-2026-11-09.md); compare-and-set means one approval can be used only once.*

| Phase | Adds | State |
|---|---|---|
| 0 Foundation | voice intake, radar, caregiver cards, audit, consent records | built; owner records pending |
| 1 Orders | approvals, groceries through official commerce servers, live alert calls | planned |
| 2 and later | rides, forwarded-message checks, more tasks | planned |

The full phase table is in [the product requirements](docs/design/PRD-saathi-2026-11-09-r2.md), section 8.

## Read next

- [Architecture](docs/ARCHITECTURE.md) and [the design index](00-INDEX.md).
- [Setup](docs/SETUP.md) for a server, the WhatsApp app and the caregiver bot.
- [Decisions](docs/DECISIONS.md) and [the product requirements](docs/design/PRD-saathi-2026-11-09-r2.md).

## Licence

Apache-2.0. Container images are built for your own server and never published; see [the setup guide](docs/SETUP.md#container-images-are-never-published).
