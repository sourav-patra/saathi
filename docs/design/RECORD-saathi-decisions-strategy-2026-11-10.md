---
id: RECORD-saathi-decisions-strategy
kind: record
status: current
sources: the owner's decisions of 2026-11-10 on an independent strategy review, in sanitized form; the review report is held privately; personal values, names and places are left out on purpose
---

Continues RECORD-saathi-decisions-2026-11-09.md, which holds D-01 to D-77. Cite a decision by its D number. The marks are defined in PRD-saathi-2026-11-09-r2.md section 2. The source of every decided line is the owner. Phases are set only in the PRD phase table.

## Platform risk and the elder channel

- 2026-11-10 D-78 decision: reading and recording the platform operator's current WhatsApp Business terms on AI assistants is a Phase 0 gate item: a manual receipt, an owner task, required before the elder is onboarded. The Telegram bot terms are read in the same step. Commerce-service terms stay a Phase 1 receipt (POLICY-data PL-32, PL-20). If the terms forbid the use or the reading is unclear, Telegram is the active elder channel. @mark=decided
- 2026-11-10 D-79 decision: the elder channel is a port with two implementations, WhatsApp primary (D-03 holds) and Telegram backup. The Telegram backup has full functionality: voice notes, photos, video, text, replies and voice clips. Per-elder config chooses the active channel and allows switching without data loss. The elder uses a separate Telegram bot, never the caregiver approval bot (D-04), so roles never mix and approval authority can never come from the elder bot. When WhatsApp is down or banned, the caregiver is alerted and can switch the elder to Telegram (SPEC platform RQ-105 to RQ-108, requests RQ-78). @mark=decided
- 2026-11-10 D-80 decision: a plain voice-call or IVR elder channel is deferred until after the first release, with the re-entry condition in SPEC evidence section 5. @mark=decided

## Pilot and phone agent

- 2026-11-10 D-81 decision: a manual pilot of 2 to 4 weeks runs in parallel with Phase 0: the caregiver answers the elder's requests by hand on the helper channel, with no AI. The protocol, consent and exit criteria are in SPEC product RQ-117 to RQ-119. Pilot data stays private; only aggregate counts are committed, as a receipt. @mark=decided
- 2026-11-10 D-82 decision: the phone agent stays for the owner's personal v1 (Phase 2) and is fenced as experimental, personal use only. It is excluded from any spec, conformance suite, certification, productized or multi-household path unless an official API appears. The product path uses only official rails: commerce MCP servers, documented deep links and human handoff. This narrows D-31, D-36 and D-53 to personal use (SPEC product RQ-115). @mark=decided

## Product readiness

- 2026-11-10 D-83 decision: tenant isolation is a boundary: Postgres row-level security on every household-scoped table, per-household config rows (caps, policies, channel choice), per-household envelope encryption keys for raw text, media and TOTP secrets, and a per-household audit chain (SPEC platform RQ-109 to RQ-111b, assurance RQ-56a, RQ-56b). This carries out D-02. @mark=decided
- 2026-11-10 D-84 decision: the consent model has one record per data use (who may hear or read the elder's voice and text: caregiver, backup approver, hosted tiers by class), a capacity and guardian field, and withdrawal per use, aligned with the DPDP section of POLICY-data (PL-33 to PL-35). @mark=decided
- 2026-11-10 D-85 decision: approval mode is a per-household setting (every order; or routine-order sign-off for a pre-approved repeat basket under a cap) and visibility is a setting (what the caregiver sees). Routine-order sign-off is OFF in v1 and, if turned on, needs a standing sign-off with expiry. Every order still needs a human approval (D-14); a standing sign-off is that approval given in advance (SPEC product RQ-112 to RQ-114). @mark=decided
- 2026-11-10 D-86 decision: goal G-1 splits into task calls (a reduction target) and social calls (not a target to reduce). A guard metric G-12 requires that social contact does not drop. @mark=decided
- 2026-11-10 D-87 decision: the emergency path is described everywhere (PRD, safety spec, elder-facing replies, consent notice) as "Saathi is not an emergency service; it alerts your family", with 112 and 108 always given (SPEC product RQ-116). @mark=decided
- 2026-11-10 D-88 decision: product direction: an open reference implementation under Apache-2.0; a published consent-to-commit spec with a conformance suite later; certification for adapters and deployers; no consumer subscription; the commons are protected with trademark and a Developer Certificate of Origin, never with copyleft (POLICY PL-37; deferrals in SPEC evidence section 5). @mark=decided
- 2026-11-10 D-89 decision: permanent invariant: Saathi never holds, routes or initiates money movement. Payment stays cash on delivery or a wallet chosen inside the merchant's own flow (POLICY PL-36). This extends D-12 from a v1 limit to a standing rule. @mark=decided

Skipped or not checked: no decision here was checked against a live service, platform term or legal text; the strategy report's claims about outside parties were not copied and were not re-verified.
