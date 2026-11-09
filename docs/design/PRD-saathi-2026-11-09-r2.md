---
id: PRD-saathi
kind: contract
revision: r2
governs: v1 (phases 0 to 4); phase 5 items are marked P
status: draft r2, round-3 fixes applied, pending owner lock (see docs/LOCKS.md)
supersedes: PRD-saathi-2026-11-09-r1.md sha256 021742ba14e4d154f291d059aaf1add458e2e3b90c36992990d7518ffae1434a, which had replaced a first draft with sha256 a784aba1ef15c35c41f01a04e0d94248a008f416c9307f2462190187f27e4b29
upstream: RECORD-saathi-decisions-2026-11-09.md sha256 a1ec6109610478a4582467cebe12c11d4bd266b89b35a89fdbe44dbde9f86f45
sources: docs/design/RECORD-saathi-decisions-2026-11-09.md (cited as D-nn); docs/ARCHITECTURE.md, docs/DECISIONS.md, docs/ROADMAP.md, evals/README.md, src/, tests/ and infra/sql/schema/001_init.sql on branch phase-0/foundation at 3d46456; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-09. Code read with `git show`; the test, lint and licence commands ran in a scratch export of that commit (section 2).
companions: SPEC-saathi-requests-2026-11-09.md, SPEC-saathi-safety-2026-11-09-r2.md, SPEC-saathi-assurance-2026-11-09-r2.md, SPEC-saathi-evidence-2026-11-09-r2.md, SPEC-saathi-alternatives-2026-11-09.md, POLICY-saathi-2026-11-09-r2.md, POLICY-saathi-data-2026-11-09.md; each lists the RQ and PL ids it holds in its section 1
---

Read section 4 for what v1 must do, section 8 for the phase table that every other file follows, and section 7 for the owner questions still open (none today).

# Saathi PRD: prepared requests for an elder, one tap for the caregiver

## 1. What it is and who it is for

Saathi serves one elderly parent (the elder) and the family member who looks after them (the caregiver). The elder phones the caregiver for tasks a program can prepare: a ride, groceries, food, shopping, a check of a viral forward, an appointment, a reminder or a simple question. The input is a WhatsApp voice note, photo, video or text in Hindi or Hinglish with basic English, sent to a helper number. The output is a prepared action on a Telegram card, or a short Hindi reply when no action is needed. The decision it supports: the caregiver approves, edits or rejects one payment with one tap. Emergencies and scams go to the humans (D-01 to D-07, D-14, D-18).

## 2. Where it stands (observed 2026-11-09)

Marks, defined here and used in every file of this set: E observed by a command or a code read in this pass; R reported by a named source that was not re-run (the line or the file header names the source); P proposed; decided accepted by the owner and recorded as a D number; N not measured, with the command that would measure it; BLOCKED a comparison that cannot run until a named prerequisite exists.

- E: in a scratch export of 3d46456 (branch phase-0/foundation, rewritten for privacy on 2026-11-09, D-68), `uv run pytest -q` printed `73 passed`: 73 test cases from 53 functions, 32 of them in `tests/test_policy.py`. `uv run ruff check src tests scripts` and `uv run python scripts/license_audit.py` passed. `git diff` against the retired tree shows 15 files changed, all removed personal or model-name values; the code claims in this set were re-read at 3d46456.
- E: Phase 0 code exists: webhook, radar, redaction, tier gateway, policy engine, Telegram summaries. Its defects are recorded as E in the row of the requirement that fixes each (safety RQ-18, RQ-22, RQ-32, RQ-79, RQ-80; assurance RQ-40, RQ-47, RQ-48, RQ-57, RQ-97; evidence RQ-58, RQ-72) and are not fixed in this design work (D-59). `test_ride_quote_expires_fast` encodes a ride quote TTL that this PRD removes (requests RQ-20).
- E: the three caps in `PolicyConfig` are required fields with no defaults (`src/saathi/policy/engine.py:28-30`); `Settings.paid_monthly_cap_inr` defaults to 0.0 and `Settings.t1_model` to "" (`src/saathi/config.py:55`, `:40`); `.env.example` holds placeholders (lines 42, 56). Two technical defaults stay public: `ride_fare_tolerance_abs = 20` (engine.py:35) and `ride_quote_ttl` of 3 min (engine.py:36). The ride TTL conflicts with RQ-20, which has none; removing it is a known code change for the Phase 0 or Phase 1 build (POLICY PL-12).
- E: RQ-26 "an unset cap denies" is not implemented: a missing cap raises `TypeError` at construction (engine.py:28-30) instead of denying a commit.
- N: no real ride, order, voice note, debunk or approval has run. Every latency, accuracy and cost figure below is a target.

## 3. Goals

Each target is P. A rate claim carries its rule-of-three bound (zero events in n trials bound the rate at 3/n, 95%).

| ID | Goal | Target | Method and sample |
|---|---|---|---|
| G-1 | Calls avoided | In-scope calls fall 80% against a baseline period | `/called` rows for 2 weeks before launch and after Phase 2. A percentage needs 20 or more baseline calls. |
| G-2 | Caregiver time per approval | Median 30 s or less from card delivery to decision, for cards answered within 5 min | `card_sent` and `decided` audit timestamps, 30 or more cards, median and p90.  Delivery is the proxy for opening (N). |
| G-3 | Request to card | p90 under 3 min for food and groceries, under 4 min for rides | Run record timestamps (assurance RQ-55), 30 or more requests per class, shadow runs included. |
| G-4 | First-pass approval | 85% or more of cards approved with no edit or reject | Wilson 95% interval. A lower bound above 85% needs at least 57 of 60. |
| G-5 | Wrong commits | 0, as a design invariant | Policy tests pass; observed wrong live commits are reported with 3/n. |
| G-6 | Emergency and scam recall | Every positive in the radar set is flagged | 100 or more positives per class in Hindi, Hinglish and English, written by someone other than the keyword author (safety RQ-16). Zero misses in 100 bound recall at 97%; false alarms per 100 benign messages are reported beside it. |
| G-7 | Debunk quality | 85% verdict accuracy and 0 fabricated sources on at least 60 held-out claims | Zero fabrications in 60 bound the rate at 5%. |
| G-8 | Abstention | Reported as its own class, no target | Router and debunk abstentions per week. |
| G-9 | Paid model spend | At or under the cap in private config | `llm_spend` ledger, monthly (assurance RQ-48). |
| G-10 | First response latency | p95 of elder messages get an acknowledgement or a reply within 10 s | `inbox.received_at` to the first outbound message, at least 100 messages (requests RQ-74). |
| G-11 | Cart and ride correctness | n = 30 blind-labelled quotes per class; threshold 0 mismatches (zero in 30 bound the rate at 10%) | Shadow quotes (SPEC evidence section 3). Code compares each field listed in safety RQ-87 with the caregiver's blind label and checks the total or fare against the cap ceiling. A pass lets the first real order of that class go ahead; it is not evidence for G-5. |

## 4. Requirements (product behaviour)

All rows are P unless the state column says otherwise. Request lifecycle, timers, acknowledgement, cancel, WhatsApp windows and channel states are in the requests spec (RQ-20, RQ-39, RQ-74, RQ-77, RQ-78). Safety, commit and phone rules are in the safety spec. The phase of every requirement is in section 8 and nowhere else.

| ID | Requirement | Check | Evidence and state |
|---|---|---|---|
| RQ-01 | Elder replies are a short Hindi voice note plus 1 to 2 lines of text with the key facts (driver, vehicle, ETA, amount, "cash dena hai"). The helper says it is a bot and never speaks as the caregiver. Language rule: the reply uses the language of the elder's message (Hindi by default, English when the elder wrote English, Hindi for mixed). Caregiver cards use the caregiver's language. | schema test on the reply model; language table test with 10 cases | N |
| RQ-02 | The router returns a schema-parsed intent, slots and confidence. Code picks one of three outcomes. (a) Confidence at or above the floor and every required slot present: build a quote. (b) A required slot missing or confidence between the floors: ask one question at a time, at most 2 per request. (c) Still missing or below the lower floor after 2 questions: summary to the caregiver and no action. A best guess is allowed only for optional preference slots (brand, ride type default) and is flagged on the card. Item, quantity, destination and payment are never guessed. Medicine orders and bills always take outcome (c). | pipeline cases for each outcome; guess-scope test | E: summary path tested. N: floors not measured |
| RQ-03 | A message the caregiver forwards to the Telegram bot is handled as elder input with `source=caregiver_forward`. Replies leave from the helper number. | pipeline test with a fake Telegram update | E: intake built. N: label untested |
| RQ-04 | Rides: the agent builds a quote (app, ride type, pickup, drop, fare, ETA, cash) from the Uber deep link, then the phone agent. Rapido and a regional open ride network app (configured privately) go through the phone agent. The card shows the top choice and one alternative. | suite `ride`, shadow mode | N |
| RQ-05 | Ride type and urgency come from rules in the profile table, evaluated by code. A request is urgent only when a rule marked urgent matches (for example a destination category the caregiver listed). A model proposes slots and cannot override a rule or set urgency. No rule ships in the repository. | one unit test per rule kind; model-cannot-set-urgent test | N |
| RQ-06 | Groceries and food use an official MCP server where access exists (Swiggy food, Instamart, Zepto, Zomato once granted) and the phone agent otherwise (Blinkit, Amazon, Zomato fallback, Phase 4). An adapter must provide `find_order`, a read-only order lookup used to reconcile an unknown commit (safety RQ-80); an adapter without it is handoff-only. No MCP access (not granted, lapsed or token expired): a handoff card with the cart as text and a deep link, no tap, state `handoff`. The card says when a service cannot cancel. The flow stops before `place_order`. | suite `cart` in shadow mode; adapter contract tests against fakes; no-access test; adapter without `find_order` cannot commit | N. R: MCP access is an application with unknown outcome (decision point, section 8) |
| RQ-07 | A grocery list with medicine is split. The medicine part goes to the caregiver as a summary. | 10 synthetic split cases | N |
| RQ-08 | Shopping (Amazon, Flipkart) runs through the phone agent and search deep links, shows a comparison card and stops before payment. | suite `cart` in shadow mode | N |
| RQ-09 | Debunk: claims are extracted to 1 to 3 checkable claims. The verdict is FALSE, MISLEADING, TRUE or UNVERIFIED, parsed against a schema. Fewer than 2 independent fetched sources gives UNVERIFIED. TRUE needs at least 3 independent fetched sources. Every cited source has a fetch record (URL, time, content hash) from the same run. | suite `debunk`; every card source maps to a fetch record | N |
| RQ-10 | Debunk output in v1 is a Hindi voice note plus a FACT CHECK image card with cited sources (publisher, title, fetch time), target 2 min. No video in v1 (D-45). Tone is indirect and respectful, health topics end with "doctor se poochhein", and a banned-phrase list blocks accusatory wording. | code checks on the card fields; phrase-list unit test | N |
| RQ-11 | A debunk auto-sends only when the category is health or scam, the verdict is FALSE, at least 2 sources come from distinct allowlisted publishers, and the auto-send flag is on. The flag is a standing sign-off that expires after 15 days (assurance RQ-95b). It stays off until 60 held-out claims pass and the preview period has ended with no rejected factual error. The preview period ends at the later of 4 weeks or at least 20 previewed debunks. Everything else, political and religious forwards included, waits for caregiver preview. | rule table unit tests; suite `debunk`; expiry test | N. Decided (D-54) |
| RQ-12 | Tech help has an empty tool allowlist and answers with a voice note and step images. | allowlist unit test | N |
| RQ-13 | Appointments use the phone agent, pay-at-clinic only. A prepaid-only booking goes to the caregiver. The booking tap passes safety RQ-30. | shadow run; handoff test | N |
| RQ-14 | Reminders are set by the elder or caregiver and stored in Postgres. Delivery follows requests RQ-77. The caregiver is notified of each new reminder. A failed delivery alerts the caregiver. Medicine reminders carry times only, never doses. | scheduler tests with a fake clock | N |
| RQ-15 | Q&A has an empty tool allowlist, gives no diagnosis and no medicine or dose change, always points to a doctor, and escalates emergency symptoms (safety RQ-16). | suite `safety` | N |
| RQ-19 | The card shows a one-line summary and the slots (items and quantities, pickup and drop, address label, price, ETA, payment method), a doubt flag when a guess was made, one alternative, a screenshot or cart lines, and buttons Approve, Edit and Reject with a reason chip. The original voice note is attached as audio. The transcript is not on the card; the "show raw" button reads the raw store (assurance RQ-97) and writes an audit row. Telegram holds personal data: the audio, the slots, the address label and any raw text the caregiver opens (POLICY-data section 2). | card renderer snapshot test; attachment test; show-raw audit test | N |
| RQ-24 | Edit: free text is parsed into a new quote with a new hash and a new card. Reject records its reason chip. Neither commits. | tests on both paths | N |

## 5. Not in scope

- Medicine orders, bills, recharges and anything the router cannot classify (caregiver summary only); UPI, card and any payment that needs a PIN or OTP.
- An additional regional language (later phase), video of any kind (later phase, SPEC evidence section 5) and the public debunk account (only its dataset, assurance RQ-73, belongs to Phase 5).
- Proving that commerce and ride apps allow automation (accepted risk, D-36).

## 6. Decisions, standing exceptions and sign-offs

Rows are accepted pending owner lock unless marked decided on 2026-11-09. Until the lock row exists, a pending answer is the reviewers' recommendation.

| Source | Decision | State |
|---|---|---|
| Q1 | Standing exception E-1 to the two-of-three rule. Scope: the phone agent and commerce adapters, for cart and ride commits only. Conditions: code verifies the final screen for carts and rides (safety RQ-87), the hostile-screen suite passes (RQ-88), shadow mode runs first. It lapses while a condition fails and is reviewed at the Phase 2 gate. | decided 2026-11-09 (D-53) |
| Q3 | Debunk auto-send: 60 held-out claims, then the preview period (later of 4 weeks or at least 20 previewed debunks), health and scam FALSE only, sign-off expires after 15 days. | decided 2026-11-09 (D-54) |
| Q6 | Urgent ride refreshes before handoff: config `urgent_refresh_max`, default 5 (requests RQ-20). | decided 2026-11-09 (D-55) |
| Q18 | False-alarm budget: config `false_alarm_budget_per_approver_week`, default 2; config `voice_calls_per_event`, default 6 (safety RQ-18). | decided 2026-11-09 (D-56) |
| Q2 | 100 shadow quotes plus the policy tests replace the 10-order gate; report 3/n. | accepted pending owner lock |
| Q4 | Either approver may pause; only the primary resumes. | accepted pending owner lock |
| Q5 | The backup's rights equal the primary's, with an audit row per tap and a code over a cap (POLICY PL-27). | accepted pending owner lock |
| Q7, Q8 | G-1 baseline is 2 weeks of tallied calls, and a percentage needs 20 or more. G-2 uses delivery to decision; whether Telegram exposes a card-open event is measured in Phase 1. | accepted pending owner lock |
| Q9, Q10 | The owner labels data and the caregiver labels Hindi tone cases (D-43). CI runs synthetic sets; private sets run locally with a receipt (SPEC evidence RQ-65). | accepted pending owner lock |
| Q12, Q14 | The memory library is deferred (D-41) and Langfuse stays (D-40). The account-ban risk of app automation is accepted (D-36). | accepted pending owner lock |
| Q15, Q16 | Debug traces live 30 days; one year applies to the audit trail and counters only. Taxonomy labels stay local until 10 consenting households exist (D-61). | accepted pending owner lock |
| Q17 | A hosted tier receives personal data only after a receipt (POLICY-data PL-20). | accepted pending owner lock |

Also decided 2026-11-09: the SO-1 expiry (D-65, in the table below), the MCP decision date (D-66, section 8), Q11 and Q13 (D-47, D-42; D-51) and the deferrals, each with a re-entry condition in SPEC evidence section 5 (D-40 to D-46, D-61).

Standing sign-offs by the owner. Each is a row with scope and expiry that agents cannot write (assurance RQ-95a, RQ-95b); an expired row behaves as off.

| ID | What it allows | Scope | Expiry |
|---|---|---|---|
| SO-1 | The automatic voice call (stage c) and the template alert T-01 to approvers' personal numbers (stage b) | Emergency events only (never a scam alert); registered approvers only; fixed script and template, no elder content; caps in safety RQ-18 | 90 days, decided (D-65). Renewal alert 14 days ahead. After expiry stages b and c send nothing, stage a continues and the owner is alerted. |
| SO-2 | Debunk auto-send (RQ-11) | Health and scam claims rated FALSE with 2 allowlisted publishers | 15 days (D-54) |

## 7. Open owner questions

None. The two questions listed here before 2026-11-09 are decided in section 6: the SO-1 expiry (D-65) and the MCP decision date (D-66).

Owner checks before Phase 1, each a manual receipt (SPEC evidence RQ-100): the terms of WhatsApp for AI assistants, of Telegram for bots and of each commerce service, read with version and date (R today, not read; if WhatsApp forbids the use, v1 falls back to Telegram-only intake and the elder phones the caregiver); template approval lead times (requests RQ-77); whether Telegram exposes a card-open event.

## 8. Order of work

Shadow mode (RQ-02 to RQ-08): the helper prepares quotes that no one can commit, shown to the primary approver only. Its request sources, no-write rule, viewer and blind-label rule are in SPEC evidence section 3 (Shadow mode).

Dated decision point (decided, D-66): if no food or grocery MCP access is granted by 2026-12-21, Phase 1 falls back to a deep link plus human handoff only. Its gate drops the shadow-order and real-order checks and verifies the no-access path (RQ-06). The fallback gate is written into the registry (SPEC evidence RQ-100) the day the date passes without access.

A phase gate has one command, `uv run python scripts/gate.py --phase N` (P: not written; SPEC evidence RQ-100). It prints only the rows of phases 0 to N and exits 0 only when each is verified or not applicable. Unavailable and invalid are never a pass; a row of a later phase is not printed and is not a failure.

This is the only phase table. Other files cite RQ and PL IDs and carry no phase. A range includes both ends. RQ-56 and RQ-95 are split: the a part is in Phase 0, the b part in Phase 1 (D-67). Each RQ and PL id appears in one phase row.

| Phase | Builds | Requirements verified by the gate | Extra evidence |
|---|---|---|---|
| 0 Foundation | Inbox with persist-before-ack, radar, emergency stage a, redaction, gateway with durable ledger, eval harness, insert-only audit write path, SO-1 sign-off row, secret scan | RQ-16 to RQ-18, RQ-36, RQ-40, RQ-41, RQ-43 to RQ-50, RQ-55, RQ-56a, RQ-57 to RQ-63, RQ-71, RQ-72, RQ-79, RQ-81, RQ-95a, RQ-96, RQ-97, RQ-99 to RQ-101, PL-01 to PL-05, PL-07 to PL-19, PL-21, PL-22, PL-24, PL-31 | `scripts/gate.py --phase 0`. Synthetic data only. Stages b and c run against fake providers. The consent record (PL-05) exists before any real message. |
| 1 Requests, approval and groceries | Router, ASR, MCP adapters, profile, cart, approval gate, policy engine, audit chain and head export, reminders, backup, egress control, live emergency stages b and c | RQ-01 to RQ-03, RQ-06, RQ-07, RQ-14, RQ-19 to RQ-33, RQ-35, RQ-37 to RQ-39, RQ-42, RQ-51, RQ-54, RQ-56b, RQ-64 to RQ-66, RQ-68, RQ-70, RQ-74, RQ-77, RQ-78, RQ-80, RQ-82, RQ-83, RQ-91 to RQ-94, RQ-95b, RQ-98, RQ-102, PL-06, PL-20, PL-23, PL-25 to PL-30 | `scripts/gate.py --phase 1`. Owner receipts for the terms in section 7 and for SO-1; RQ-18 stages b and c live (drill). At least 100 shadow quotes with zero policy violations and G-11 met for carts, then 10 real orders (a check that the path works, not evidence for G-5). |
| 2 Rides | Bridge board, phone agent, human-authored macros, device controls | RQ-04, RQ-05, RQ-34, RQ-53, RQ-87 to RQ-90 | `scripts/gate.py --phase 2`. At least 30 ride quotes in shadow mode and G-11 met for rides before the first real ride. Phone and macro comparisons (SPEC alternatives). |
| 3 Debunk and help | Claim check, voice and image card, tech help, Q&A | RQ-09 to RQ-12, RQ-15 | `scripts/gate.py --phase 3`. SO-2 stays off until RQ-11 conditions are met. |
| 4 More commerce | Blinkit, Amazon, Flipkart, Zomato fallback, appointments | RQ-08, RQ-13 | `scripts/gate.py --phase 4`. RQ-87 cart fields on phone flows. |
| 5 Grow | Taxonomy and its export, additional regional language, video, document RAG | RQ-73 | `scripts/gate.py --phase 5` after a new revision defines the rest. |
| Deferred | Facts table, elder-reaction classifier, semantic claim cache, automated macro repair | RQ-52, RQ-67 | No earlier than Phase 3; each returns only when its re-entry condition in SPEC evidence section 5 is met. Until then the owner records them as not applicable. |

## 9. Not measured and limits of the evidence

- Every latency, accuracy, recall and cost figure: no run exists.
- Radar recall on real elder speech (command: suites `radar` and `asr` on consented notes); whether the phone agent survives app detection (command: macro hit rate from run records in Phase 2); telephony delivery of emergency calls (command: a drill that rings the approvers' phones with a test event).
- Template approval times, platform terms, Telegram card-open events, and whether one food service MCP offers cash on delivery.

Skipped or not checked: figures (the tables carry the flow; no reader check ran); live services and platform terms; no model grader ran; `scripts/gate.py` and the doc lint (SPEC evidence RQ-100, RQ-101) do not exist.
