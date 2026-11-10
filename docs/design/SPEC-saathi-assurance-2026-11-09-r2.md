---
id: SPEC-saathi-assurance
kind: contract
revision: r2
governs: v1; applies to every phase in PRD-saathi-2026-11-09-r2.md
status: draft r2, round-3, TRD review and strategy fold applied, pending owner lock (see docs/LOCKS.md)
supersedes: SPEC-saathi-assurance-2026-11-09-r1.md sha256 cd948b6b8c4a112f66350d4bddd3483a08980b97c7ab9737e2913b70a95e548a, which had replaced a first draft with sha256 78c70b97ae17979a73ea3c484a2fc8e7a6fe0410d93ee15c297227c8b63517eb
upstream: PRD-saathi-2026-11-09-r2.md sha256 141b677d217017de6973d67f2f5d46eb549b2b40464d4f7426f4ee83d0ed15ec; SPEC-saathi-requests-2026-11-09.md sha256 0e595737a4216baecdc1ad6a1436d3b4351988e7fc8f26c4f752935930eb60b5; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9
sources: docs/design/RECORD-saathi-decisions-2026-11-09.md (D-nn); src/, tests/, infra/sql/schema/001_init.sql and .github/workflows/ci.yml on branch phase-0/foundation at 3d46456; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-09. Code read with `git show`; nothing executed in this pass except the commands in PRD section 2.
---

Read section 2 for the requirements, section 3 for the record and audit formats, and section 1 for the model grades and the minimum grade per step.

# Saathi assurance spec: models, data, records, budgets

## 1. Goal, scope and grades

Goal: personal data reaches only places with a recorded reason, every decision leaves a record that survives a crash and cannot be rewritten, and model spend and effort stay inside stated bounds. This file holds RQ-37, RQ-40 to RQ-55, RQ-56a, RQ-56b, RQ-57, RQ-66 to RQ-68, RQ-73, RQ-92, RQ-95a, RQ-95b, RQ-96 to RQ-98. The evidence spec holds evals and the rule index (RQ-58 to RQ-65, RQ-71, RQ-72, RQ-99 to RQ-101, RQ-103). The safety spec holds arrival and commit. RQ-69 is withdrawn: the self-improvement agent is deferred (D-42). Marks: PRD section 2. All rows are P unless the state column says otherwise. Rules name grades; the rule on naming models in public files is POLICY PL-12. Tier labels T0 to T4 and T2_FREE stay as names.

| Grade | Tier | Capability | Personal data |
|---|---|---|---|
| G0 | T0 | Code only: radar, policy, caches, macro replay, templates | n/a |
| G1 | T1 | Local model on the VPS CPU: intent and slots, safety classifier, embeddings, ASR | yes |
| G2 | T2 | Hosted open-weight model: vision, Q&A, tech help, drafting | only with a receipt covering the data class (RQ-50, RQ-96) |
| G3 | T3 | Paid multimodal: native audio and video, hard ASR | only with a receipt |
| G4 | T4 | Paid frontier reasoning: planning, final debunk verdicts | only with a receipt |
| F | T2_FREE | Free-tier endpoint without a no-training guarantee | never |

Minimum grade per step (RQ-46). Every floor is P until the sentinel suite (RQ-64) measures it.

| Step | Floor | Behaviour below the floor |
|---|---|---|
| Emergency and scam keywords | G0 | A G1 classifier may add a flag and never clear one |
| ASR | G1; G3 on low confidence with an audio receipt | G3 unavailable: forward the audio to the caregiver (safety RQ-82) |
| Intent and slots | G1; one retry at G2 | Still unsure: summary to caregiver |
| Quote building | G0 plus G1 slots | n/a |
| Phone-agent macro replay | G0 | Checkpoint fails: handoff, reason `macro_failed` (safety RQ-89) |
| Vision on images and screens | G2 with an image receipt | No receipt: handoff with summary |
| Q&A and tech help | G2 | Blocked with summary |
| Debunk claim extraction | G2 for text and images; G3 for video | Blocked with summary |
| Debunk final verdict | G4 | UNVERIFIED and caregiver preview; never a lower grade |

## 2. Requirements

| ID | Requirement | Check | Evidence and state |
|---|---|---|---|
| RQ-37 | Backup. A nightly database dump is encrypted before it leaves the server, with a key held away from the bucket credential, and goes to off-site object storage. Backups are kept 30 days, rolling. A restore into an empty database is tested monthly and replays the erasure log (RQ-98). | `file` on the dump shows encrypted output; restore script exit code | N |
| RQ-40 | Personal by default. The gateway argument is a `Sensitivity` enum whose default is PERSONAL. Tier F accepts only a `PublicPayload` built in an allowlisted module (public claim research, synthetic eval data, code tasks). | privacy-gate test; default-argument test; grep lint for `PUBLIC` outside the allowlist | E: `personal` is a required keyword and `personal=False` reaches tier F; `test_privacy_gate_blocks_free_tier_for_personal_data` passes. N: default and lint |
| RQ-41 | Deterministic redaction of Aadhaar, PAN, voter and passport numbers, phone numbers, card and OTP strings before any hosted call and before any trace or cache write. Redaction works on text only; media is governed by RQ-96. An address leaves the server only inside an order step. | suite `redaction` with canary values per type | E: tests pass for Aadhaar, PAN, phone, OTP, card. N: voter, passport |
| RQ-42 | Retention, values in private config, proposed: raw media 90 days; raw text store 30 days (RQ-97); debug traces 30 days; run records and audit rows 1 year, holding hashes and ids only; profile until deleted; backups 30 days. Erasure removes media, transcripts, caches, traces and profile from every store the system controls, destroys the household's keys (platform RQ-111b) and writes an audit row with ids only. | erasure test with canary values across every store; retention job dry run | N |
| RQ-43 | Every outbound host the code can call is a row in the POLICY-data table (PL-02, PL-04). | test that lists configured base URLs and fails on an unlisted host; `strace -f -e trace=connect` during one pipeline run | N |
| RQ-44 | Caches, traces and run records hold no restricted data (ID numbers, card data, OTPs). | canary test across all stores | N |
| RQ-45 | Tiers are configured by grade. The rule on model and provider names in public files, and its check, are in POLICY PL-12. | tier-config loader test | N |
| RQ-46 | A routing table in code assigns each step its floor (section 1). Model labels never change routing. | one test per row | N |
| RQ-47 | Fallback moves up a grade on schema failure, low confidence or outage, never down. A fallback tier gets exactly the tool allowlist of its intent. A refusal is a schema field (`refusal: bool`, `refusal_reason`) set from the provider's finish reason or content-filter field and from decision schemas. A refusal stands: no retry on another tier, the request ends `blocked` with a caregiver summary. `cascade` fails with an error when no tier gives an accepted result. It never returns an unaccepted result. | fake gateway tests: refusal not retried, tool sets equal, all-rejected raises | E: `cascade` returns the last unaccepted result. `test_cascade_escalates_and_skips_disallowed_tiers` passes. N |
| RQ-48 | The paid hard cap is a private config value. Unset disables every paid tier. A paid tier with no price entry is disabled, so the cap cannot be bypassed by a missing price. The ledger is a Postgres table: before each call a reservation row holds the worst-case cost, the call settles it, a failed call releases it. The ledger alerts at 80% and at the cap blocks paid-floor intents with a caregiver summary while G0 paths keep running. | ledger tests: missing price, reservation under concurrency, restart, 80% alert, unset cap | E: `BudgetLedger` is in memory; `test_budget_cap_blocks_paid_tier` passes. N |
| RQ-49 | Each model call that feeds a decision declares a schema. A parse failure retries once at the same grade, then fails. No decision uses free-text parsing. Every prompt carries `PROMPT_VERSION`. | schema-failure test | N |
| RQ-50 | Each roster entry has a receipt: licence, training and retention terms, the data classes and media classes it may receive (text, audio, image, screenshot, video). An entry without a receipt receives no personal data. | roster loader test | R: model-card licences and hosted terms are unverified (D-37) |
| RQ-51 | The profile lives in versioned Postgres tables, including the rules for ride type and urgency (PRD RQ-05). An agent-proposed change becomes a caregiver card and applies only on approve. History is kept. | version test; proposal test | E: `profile_facts` exists. N |
| RQ-52 | Deferred. A facts table (a value, a verbatim source quote, the source event id and a confidence, with a check on every read that the quote still occurs in its source) waits for its re-entry condition (SPEC evidence section 5). Until then v1 memory is the profile of RQ-51 only. | tamper test: edit the source, the fact disappears (when the row re-enters) | N |
| RQ-53 | [EXP] Macros are human-authored, versioned, replay as code with no model, hold selectors and slots and no credential or address, and enforce the app allowlist. A checkpoint failure is a handoff (safety RQ-89). The owner repairs a macro as a new version, which replays in shadow mode first and cannot be used for a commit before a sign-off, an audit row naming the macro version and the signer. Automated repair is deferred (SPEC evidence section 5). | replay test with a changed screen (handoff, no repair); allowlist test; unsigned-macro test | N |
| RQ-54 | Cache rules. Exact model cache key: prompt version, grade, sensitivity, messages, temperature 0, never for time-dependent calls. Media cache is keyed by content hash. Catalogue cache lives 15 min. A commit re-reads live data. Caches hold redacted content only. No cache serves an action intent. A semantic claim cache is deferred (SPEC evidence section 5). | one test per rule | E: `test_exact_cache_hit_is_free` passes. N |
| RQ-55 | A run record is written when a run ends in any state (section 3). | schema test; empty-run test | N |
| RQ-56a | Audit write path. The audit trail is a separate append-only Postgres store, not the trace backend (D-44), in table `audit_log` (section 3). The application writes through a database role that can insert and cannot update or delete. `seq` comes from a database identity column. Every row carries `household_id`; the keyed hashes use that household's `audit` key and name its `key_id` (platform RQ-111a). Identifiers and low-entropy values (phone numbers, addresses) enter the log as keyed hashes (HMAC with an audit key held outside the database), so a leaked log cannot be matched by guessing. Every row carries the run id and the actor identity (RQ-95a). Failed attempts, denials and refusals are kept. The chain columns stay empty until RQ-56b. | UPDATE and DELETE denied test for the application role; keyed-hash canary test (a phone number appears in no row); actor-present test; failed-attempt test | N |
| RQ-56b | Audit chain and export, one chain per household. One serialized writer per household (a transaction-level advisory lock keyed by the household) assigns `hseq` and the chain hash over that household's rows in `audit_log`; a genesis row per household hashes the rows written under RQ-56a. The chain hash does not use the HMAC key, so a chain verifies after that key is destroyed (platform RQ-111a). Each household's chain head (hseq, row hash, time) is exported nightly to off-site storage, and `scripts/audit_verify.py` (P) recomputes every chain, prints the first broken `hseq` and household and compares each live head with the last export. One household's erasure or export touches only its own chain. | chain-break test; head-mismatch test; concurrent-writer test for one household and for two households at once; genesis-row test; a test that two households' chains do not share a sequence | N |
| RQ-57 | Traces leave through one exporter interface: JSON-lines files on the server disk, then Langfuse through OpenTelemetry once the trace stack runs (D-74); the allowlist and masking are the same for both. A span holds ids, intent, confidence, flags, counts, hashes and durations; it holds no transcript or message text. Node outputs are logged through a field allowlist, not by dumping the state model. Tracing never fails a request. Phone screenshots are personal data in local object storage under the 30 day rule. | canary test on the exporter output (RQ-97); backend-down and unwritable-file tests | E: `traced()` logs whole node outputs and the mask removes ID patterns only. N |
| RQ-66 | Each approve, edit and reject writes an audit row (the source of truth) and mirrors a score to the trace backend. An edit becomes a proposed eval case after anonymisation and review. | feedback tests | N |
| RQ-67 | Deferred. Elder reactions and short voice replies classified satisfied or problem by schema (the text carries no authority), with an incident card for a problem, wait for the re-entry condition (SPEC evidence section 5). Until then the outcome check comes from MCP tracking or `/called`, stored on the run record. | classifier cases; outcome test (when the row re-enters) | N |
| RQ-68 | `/called <class> <reason>` writes an audit row; the class is `task` or `social` (goals G-1, G-12), and a missing class is asked once with two buttons. The weekly digest prints measured counts with n; suggestions are marked P. | digest snapshot test | N |
| RQ-73 | Issue taxonomy (Phase 5, D-61). Every request gets a label {category, sub_type, outcome} from a closed, versioned vocabulary, derived by code from router and run record fields. A label holds no free text, name, address, number or time finer than a week. It stays in the local store. Export needs household consent and at least 10 consenting households. | schema test; canary-leak test; export gate test | N |
| RQ-92 | Effort bounds. Per request, defaults: 12 model calls, 60000 tokens in and out, 25 agent actions, 3 min of model time. Per elder: the safety RQ-81 message limit and 60 model-backed requests per day. An exhausted bound ends the request `blocked` with reason `budget` and a caregiver summary. Emergency scans, cancel matching and acknowledgements are code and sit outside the bounds. All values are config. | bound tests with a fake gateway; emergency-over-limit test | N |
| RQ-95a | Standing sign-off row and identity. A standing sign-off is a row (`id`, scope, granter, `granted_at`, `expires_at`, `revoked_at`) in a schema the agent roles cannot write; only an authenticated owner command writes it. SO-1 (emergency alert stages b and c) expires after 90 days with an owner alert 14 days before; an expired or revoked row behaves as off, and an expired SO-1 alerts the owner at once (safety RQ-18). Each use writes an audit row (RQ-56a) naming the sign-off id. Actor identities: `elder`, `approver:<id>`, `system:<service>`, `agent:<role>`. | write-denied test for agent roles; SO-1 expiry test; use-audit test; pre-expiry alert test | N |
| RQ-95b | Other sign-off classes and config changes, with the row schema and expiry behaviour of RQ-95a. Expiry limits: debunk auto-send (SO-2) 15 days; a routine-order sign-off (SO-3, product RQ-114, off in v1) 30 days; a temporary widening of an app or category allowlist 30 days. Config changes (roster, caps, allowlists, channel, approval mode, visibility) are `config_change` rows by a human identity that write a new `household_config` version (platform RQ-110); agent roles hold read access only. | expiry test per class; config-write-denied test for agent roles | N |
| RQ-96 | Media classes and tiers. Redaction cannot clean audio, images or screens, so the tier receipt is the control. Raw audio goes to local ASR (G1) by default and to G3 only with a receipt covering audio. Each route also needs the elder's consent for that class (POLICY-data PL-33). Screenshots and forwarded images go to G2 or G4 only with a receipt covering images and screenshots. Video goes to G3 only with a receipt covering video. Without the receipt the class routes to handoff: the caregiver gets the original media and a summary. OCR text from an image is redacted before any text-only call. | roster loader test: no route for a class without a receipt; handoff test per class | N |
| RQ-97 | Raw text. Transcripts and message text live in one table, `raw_text` (`inbox_id`, `part` of text, caption or transcript, `text`, `expires_at`), in the application database, for 30 days, readable by the worker and the show-raw action only, encrypted with the household content key (platform RQ-111a). The edge inserts the text and caption parts in the inbox transaction (safety RQ-79); the worker adds transcript parts. Telegram cards and alerts carry a one-line summary and slots built by code from schema-parsed fields, never the transcript. The caregiver reads the transcript only through "show raw", which reads this table, sends it in a Telegram reply and writes an audit row. The original audio attached to a card is personal data held by Telegram (POLICY-data section 2). Traces, run records and logs never hold raw text. | canary test: a pipeline run with a personal canary phrase, a fake trace sink and a fake Telegram client; the phrase appears only in `raw_text` and in a show-raw reply | E: raw text reaches traces and cards today. N |
| RQ-98 | Erasure limits and the request path. Erasure reaches every store the system controls (RQ-42). It cannot reach: Telegram (messages, audio and attachments held by the platform; bot deletion is limited by platform rules, R, not read); WhatsApp copies on the elder's and approvers' devices and on the platform; backups, for up to 30 days; commerce service order records; the telephony provider's call logs; hosted tier providers within their receipt retention; off-site chain-head exports (hashes only). The erasure record lists each unreachable store and its retention. A data-principal request follows POLICY-data PL-30. | erasure test with canaries across reachable stores; record-lists-unreachable test | N |

## 3. Record formats

Run record (RQ-55), one row per run in `run_records`, computed once at run end. The first line of any exported file states what it proves.

| Field | Content |
|---|---|
| ids | run id, request id, inbox id, household id, intent, prompt versions |
| times and state | start, end, final state (requests RQ-39) and reason |
| steps | per step: type, tool, grade and tier label, input hash, output hash, exit status, ms, cost in INR |
| checks | per check: id and result `verified`, `invalid`, `unavailable` or `not applicable`. A check that did not run is `unavailable`. |
| decisions | policy verdicts and reasons, refusals, cache and macro hits |
| refs | audit row ids, artifact hashes, outcome |

Audit trail (RQ-56a, RQ-56b), table `audit_log`.

| Field | Content |
|---|---|
| seq, ts, household_id | insertion sequence (identity column, RQ-56a), UTC time, the household; `hseq` is the per-household chain sequence of RQ-56b |
| run_id, actor | run id and identity (RQ-95a) |
| event | `card_sent`, `decided`, `token_spent`, `token_voided`, `commit_intent`, `commit_result`, `commit_unknown`, `deny`, `pause`, `resume`, `config_change`, `signoff`, `signoff_use`, `called`, `alert`, `ack`, `refusal`, `show_raw`, `totp_fail`, `erasure` |
| subject, payload_hmac, key_id | request or quote id, HMAC of the canonical payload, the key used (platform RQ-111a) |
| prev_hash, row_hash | written from RQ-56b on: `row_hash = sha256(prev_hash, fields)` |

Audit rows hold ids and keyed hashes only. The verify script is in RQ-56b.

## 4. Out of scope and order of work

Out of scope: choosing concrete models (private config), a UI for the audit trail, and the memory library (deferred, D-41). Order of work: the PRD phase table.

Skipped or not checked: figures (the tables carry the structure); no schema here is implemented; hosted provider terms and platform deletion rules were not read; Telegram and telephony retention are unknown; the doc lint (SPEC evidence RQ-101) does not exist.
