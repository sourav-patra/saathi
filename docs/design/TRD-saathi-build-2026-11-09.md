---
id: TRD-saathi-build
kind: contract
governs: the code changes required in the Phase 0 code at 3d46456 and the Phase 0 build order; part of TRD-saathi-2026-11-09.md
status: draft, new, pending review and owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 341ecc305674155b388184dd59323faeda447676c01d9d002c188b9aeadfdd8b; SPEC-saathi-requests-2026-11-09.md sha256 b798a89a5d876a26d7e30e3d8b3ad380ffa276cb8b133e5b87baf17c3c32ea72; SPEC-saathi-safety-2026-11-09-r2.md sha256 6b9a198e72f618e26af03ff63e1555abf9c056e7fcf8a3e4cdff7fd078b9c202; SPEC-saathi-assurance-2026-11-09-r2.md sha256 be50717893ed345c507fcc856d7f8694931b520e3bf4110200b922519007137e; SPEC-saathi-evidence-2026-11-09-r2.md sha256 482a276cd9216e76d527619579f554abad7f89d4c8ea13c6159d8a56b338eabf; POLICY-saathi-2026-11-09-r2.md sha256 aee5383d7d124ddad9f2fbb8bf9dcc50ac066eb05d128d641f126a97f5495dd9; POLICY-saathi-data-2026-11-09.md sha256 993dc52a89eb0fda3333df48f110c5654a3b7f184923d71bcd39c073bc782b7f; RECORD-saathi-decisions-2026-11-09.md sha256 a1ec6109610478a4582467cebe12c11d4bd266b89b35a89fdbe44dbde9f86f45
sources: src/, tests/, infra/, scripts/ and .github/workflows/ci.yml on branch phase-0/foundation at 3d46456, each line cited as file and line; the Phase 0 row of the PRD phase table; the round-3 review order of work
checked-at: 3d46456, 2026-11-09. Code read with `git show`; the 73 tests were not re-run here (PRD section 2 records the run).
---

Read section 3 first: it is the Phase 0 build order, one bounded unit per step, each ending on a command the builder can run. Section 2 lists what changes in the existing code and which step does it.

# Saathi TRD build: code changes and the Phase 0 build order

## 1. Scope

The PRD fixes the Phase 0 gate rows (section 8). This part says what in the code at 3d46456 must change to meet them, and in which order to build. D-59 keeps the bugs out of the design work; this list is the work queue. Rows of later phases are listed so no change is lost; the Step column says Phase 0 step or the phase. All rows are P; the Where column is E.

## 2. Code changes in the existing code

| No | Change | Where (E at 3d46456) | Serves | Step |
|---|---|---|---|---|
| 1 | Replace the in-memory seen-set and the background task with the inbox insert, commit, then 200, and a worker | `app.py:36-49`, `:145-160` | RQ-79 | 3 |
| 2 | Return 400 on bad JSON and 413 on a large body; store unsupported kinds; write `rejected_inbound` for unknown senders; read the allowlist from Postgres | `app.py:152-156`; `whatsapp.py:25` maps sticker to image | RQ-81 | 3 |
| 3 | Radar over persisted text and captions; ASR failure forwards the audio with a templated reply; wire the classifier as an additive flag | `pipeline.py:78-80`, `:88-90`; `radar.py:3-4` says the classifier is OR-ed, `pipeline.py` has none | RQ-16, 82 | 4, Phase 1 |
| 4 | Fixed Hindi clips with hashes; reply keys that hold no caregiver name | `replies.py:9-26` interpolates a name | RQ-16, 17; D-69 | 4 |
| 5 | Durable alert events; cadence 60 s then 5 min with a cap of 20; scam alerts repeat; original audio attached; Acknowledge and False alarm buttons; stages b and c; drop the in-memory acknowledgement set | `telegram.py:29-30`, `:45`, `:61-92` | RQ-18 | 5 |
| 6 | Cards and alerts built from slots, with raw text only through show-raw | `telegram.py:57`, `:64`; `pipeline.py:59` | RQ-97, 19 | 6 |
| 7 | Span field allowlist; stop tracing prompts and whole node outputs | `gateway.py:209-212`; `pipeline.py:128-135`; `observability.py:26-33` masks IDs only | RQ-57 | 6 |
| 8 | Add voter and passport formats to redaction | `pii.py:53-62` | RQ-41 | 6 |
| 9 | `Sensitivity` enum defaulting to PERSONAL; `PublicPayload` for tier F | `gateway.py:177-186`, `:170-171` | RQ-40 | 7 |
| 10 | `cascade` raises `NoAcceptedResult`; refusal as a result field; no retry of a refusal | `gateway.py:249-280` | RQ-47 | 7 |
| 11 | Postgres ledger with reservations; an unset cap or a missing price disables paid tiers | `gateway.py:88-107`, `:61-62`, `:172-173`, `:234-235` | RQ-48 | 7 |
| 12 | Grade routing table; remove T4 from the router cascade; two confidence floors; urgency from a profile rule, not the model | `router.py:14`, `:70`, `:51`; `domain.py:116` | RQ-46, 02, 05 | 7, Phase 1 |
| 13 | Cache key adds prompt version, grade and sensitivity; redacted content only | `gateway.py:199-204` | RQ-54 | Phase 1 |
| 14 | Token expiry, single use and request id; commit_intent table | `quote.py:69-111`; `engine.py:176-183`; `001_init.sql:94-103` | RQ-22, 80 | Phase 1 |
| 15 | An unset cap denies instead of raising `TypeError`; caps checked on the ceiling; category allowlist | `engine.py:28-30`, `:132-134`, `:125-130` | RQ-26, 27, 29 | Phase 1 |
| 16 | Remove the ride quote TTL and rewrite `test_ride_quote_expires_fast` | `engine.py:36`, `:78-79`, `:185-187` | RQ-29; PL-12 | Phase 1 |
| 17 | Durable pause; only the primary resumes | `telegram.py:33-37`, `:115-125` | RQ-32 | Phase 1 |
| 18 | Forward intake inserts an inbox row keyed `tg:<chat>:<message>` instead of running inline | `telegram.py:136-165`; `app.py:55`, `:113` keep media in memory | RQ-03, 79 | Phase 1 |
| 19 | Schema: migrations 002 to 005 and roles; `household_id` on `approvals` and `llm_spend` | `001_init.sql:60`, `:62`, `:83-92`, `:107-114`; `00-create-databases.sh:6-7` | RQ-38, 56a, 79 | 2 |
| 20 | Startup refuses an empty approval secret | `config.py:18` | PL-14; RQ-22 | 3 |
| 21 | Split the `saathi` service by zone; `internal` networks; per-service env files; no `latest` tags; image digests | `docker-compose.yml:107`, `:129`, `:163`, `:176`, `:198-212` | RQ-72, 93, 102 | 9, Phase 1 |
| 22 | Remove ffmpeg from the ASR image (D-46) and extend the licence audit to images and model files | the ASR Dockerfile under `infra/`, `:17-19`; `license_audit.py` reads Python packages only | RQ-71; PL-09 | 9 |
| 23 | Digest-pinned actions, hashed installs, jobs `evals` and `leak-scan` | `ci.yml:11-12`, `:23`; `uv sync --frozen` | RQ-61, 72; PL-15, 22 | 9 |
| 24 | Stop storing model free text as an issue label | `001_init.sql:66`; `router.py:29`, `:53` | RQ-73 | Phase 5 |

## 3. Phase 0 build order

Each step ends on a check. The gate command is `uv run python scripts/gate.py --phase 0`; before step 1 it does not exist. Test names are proposals. A step is done when its check passes and its deliberate fault fails (RQ-62).

1. Build the gate, the rule index and the doc lint: `scripts/gate.py`, `scripts/gate_registry.toml`, `scripts/rule_index.py`, `scripts/doc_lint.py`, the `PHASE` file, and `scripts/design_check.py` from this design set. Check: `uv run pytest tests/test_gate.py tests/test_rule_index.py tests/test_doc_lint.py` passes; each deliberate fault of RQ-100 and RQ-101 exits 1; `gate.py --phase 0 --list` prints every Phase 0 id. RQ-63, 100, 101; PL-19. They come first because every later check reports through them.
2. Write migrations 002 to 005 and the roles (ports part, sections 4 and 5). Check: apply 001 to 005 to an empty database; `uv run pytest tests/test_schema.py` passes: `saathi_core` cannot update or delete `audit_log`, `saathi_model` cannot write approvals or `control.*`, every table has `household_id`. RQ-56a, 95a, 38 (the isolation query test itself is Phase 1).
3. Build the edge insert, the lease helper and the worker (changes 1, 2, 19, 20). Check: `uv run pytest tests/test_inbox_crash.py` kills the process after the 200 is flushed, restarts, and asserts one request, one acknowledgement and one card over 20 runs, and a replay creates nothing; a grep test finds no module-level seen-set. RQ-79, 81.
4. Build the radar with the local classifier as an additive flag, and the fixed Hindi clips (changes 3, 4). Check: `uv run pytest tests/test_guards.py tests/test_voice_store.py` passes; `uv run python -m evals.run radar` reports zero misses on the committed synthetic set and fails on a manifest with equal authors; a test with every model service fake-down still sends the stored clip and opens an alert event. RQ-16, 17; D-69. The Hindi reader's clip receipts are manual (RQ-100).
5. Build escalation with fake providers (change 5, alert tables, SO-1 row check). Check: `uv run pytest tests/test_alerts.py` passes under a fake clock: each stage, stop on acknowledgement at each stage, restart mid-stage, Telegram down, SO-1 expired, and the six counter scripts of the evidence spec; it fails when the cadence constant changes. RQ-18, 95a.
6. Build the card and trace redaction (changes 6, 7, 8) and the `raw_text` store with show-raw. Check: `uv run pytest tests/test_trace_canary.py tests/test_redaction.py` passes: a personal canary appears only in `raw_text` and in a show-raw reply, and every ID type is redacted. RQ-41, 44, 57, 97.
7. Fix the gateway (changes 9, 10, 11, 12 for Phase 0). Check: `uv run pytest tests/test_gateway.py` passes: default sensitivity, unset cap, missing price, concurrent reservations, restart, 80% alert, refusal not retried, all-rejected raises; the roster loader refuses a media class that has no receipt. RQ-40, 45 to 50, 96.
8. Add run records, the empty-run rule, retention values with the erasure canary test over the Phase 0 stores, the outbound host list test, and the consent gate. Check: `uv run pytest tests/test_run_record.py tests/test_erasure.py tests/test_hosts.py tests/test_consent.py` passes; `strace -f -e trace=connect` over one fake pipeline run shows only listed hosts; the consent record exists as a manual receipt. RQ-55, 36, 42, 43; PL-05. RQ-42 is a Phase 1 gate row; its Phase 0 stores are covered now.
9. Build the eval harness registry and report format, then pin CI and images (changes 22, 23, 21 for digests), then run the gate. Check: `uv run python scripts/gate.py --phase 0` exits 0 with no `unavailable` or `invalid` row, and each gate shown to fail on a deliberate fault is recorded as a receipt. RQ-58 to 62, 71, 72, 99 to 101; PL-15, 22.

The SO-1 row, the consent record and the clip receipts are owner actions; the gate reports them `unavailable` until each receipt exists. Phase 0 gate rows that no step above builds are settings and small checks, added to the registry in step 9: PL-16, PL-17, PL-21 and PL-22 (repository settings read), PL-24 (the rows of `docs/LOCKS.md`), PL-31 (a commit-message hook shown to fail on a planted trailer), PL-13 and PL-14 (an environment test and `stat -c %a .env`), PL-08 and PL-12 (a leak scan with the owner's private name list).

Skipped or not checked: the line numbers were read at 3d46456 and not re-run; no test named here exists; the step sizes were not estimated; Phase 1 to 5 changes are listed but not ordered.
